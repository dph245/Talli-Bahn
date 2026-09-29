import asyncio
from datetime import timedelta
import json
import time
import httpx
import pytest
from fastapi.testclient import TestClient
from google.transit import gtfs_realtime_pb2 as gtfs
from app.main import create_app
from app.models import Board, Departure, Stop
from app.providers.db_ris import DBRISProvider, normalize_board
from app.providers.gtfs_realtime import RealtimeFeed, apply_alerts, FEED_INTERVAL
from app.providers.supplemented import SupplementedProvider
from test_transit import NOW, journey, provider, write_feed  # shared synthetic GTFS fixture


def feed():
    result = gtfs.FeedMessage()
    result.header.gtfs_realtime_version = '2.0'
    result.header.timestamp = int(time.time())
    return result


def test_gtfs_operator_and_nullable_public_contract(provider):
    async def fixed_board(stop_id, kind, now):
        return await provider.board(stop_id, kind, NOW)
    class FixedClock:
        search = provider.search
        board = staticmethod(fixed_board)
    with TestClient(create_app(FixedClock())) as client:
        response = client.get('/api/board?stop_id=s')
        assert response.status_code == 200
        data = response.json()
        d = data['journeys'][0]
        assert d['operator'] == 'Test'
        assert d['realtime'] is None and d['delay_minutes'] is None
        assert d['scheduled'] and data['notice'] is None
        assert not {'trip_id', 'agency_id', 'route_id', 'sequence', 'timeSchedule', 'trip_update', 'expected'} & d.keys()
        assert 'Departure' in client.get('/openapi.json').text


@pytest.mark.parametrize('failure', ['empty', 'timeout', 'http', 'malformed', 'stale'])
def test_realtime_unavailable_is_normal_fallback(provider, monkeypatch, failure):
    async def get(self, url, headers):
        if failure == 'timeout':
            raise httpx.ReadTimeout('timeout')
        f = feed()
        if failure == 'stale':
            f.header.timestamp -= 1000
        return httpx.Response(503 if failure == 'http' else 200,
            content=b'not protobuf' if failure == 'malformed' else f.SerializeToString(),
            request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    provider.realtime = RealtimeFeed('https://example.org/feed')
    board = asyncio.run(provider.board('s', 'departures', NOW))
    assert board.notice is None and board.journeys
    assert all(d.realtime is None for d in board.journeys)


def test_realtime_thirty_second_cache_and_refresh(monkeypatch):
    calls = []
    async def get(self, url, headers):
        calls.append(url)
        return httpx.Response(200, content=feed().SerializeToString(), request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    rt = RealtimeFeed('https://example.org/feed')
    async def run():
        await rt._refresh()
        rt.checked -= 29
        await rt._refresh()
        assert len(calls) == 1
        rt.checked -= 2
        await rt._refresh()
    asyncio.run(run())
    assert FEED_INTERVAL == 30 and len(calls) == 2


def test_cached_feed_expires_even_before_next_request(monkeypatch):
    f = feed()
    async def get(self, url, headers):
        return httpx.Response(200, content=f.SerializeToString(), request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    rt = RealtimeFeed('https://example.org/feed')
    async def run():
        await rt._refresh()
        rt.cached.timestamp -= 1000
        return await rt._refresh()
    assert asyncio.run(run()).timestamp == 0


def alert(selector=None, start=None, end=None):
    a = gtfs.Alert()
    a.header_text.translation.add(text='English', language='en')
    a.header_text.translation.add(text='Bauarbeiten', language='de')
    a.description_text.translation.add(text='Bitte Hinweise beachten', language='de')
    target = a.informed_entity.add()
    for key, value in (selector or {}).items():
        setattr(target, key, value)
    if start is not None or end is not None:
        period = a.active_period.add()
        if start is not None: period.start = start
        if end is not None: period.end = end
    return a


def test_alert_selector_intersection_language_and_time():
    d = journey()
    d.route_id, d.agency_id, d.route_type = 'r', 'a', 2
    entries = [
        ('station', alert({'stop_id': 's'})),
        ('route', alert({'route_id': 'r', 'agency_id': 'a'})),
        ('wrong-route', alert({'route_id': 'wrong', 'stop_id': 's'})),
        ('wrong-station', alert({'stop_id': 'elsewhere'})),
        ('expired', alert(end=int(NOW.timestamp()))),
        ('future', alert(start=int(NOW.timestamp()) + 5000)),
    ]
    entries[1][1].description_text.translation[0].text = 'Zusätzlicher Linienhinweis'
    boards = apply_alerts([d], entries, 's', NOW)
    assert [a.id for a in boards] == ['gtfs-rt:station']
    assert [a.id for a in d.alerts] == ['gtfs-rt:station', 'gtfs-rt:route']
    assert d.alerts[0].header == 'Bauarbeiten'
    assert d.realtime is None


def test_trip_alert_uses_service_date_and_direction():
    d = journey()
    d.direction_id = 1
    a = alert()
    a.informed_entity[0].trip.trip_id = d.trip_id
    a.informed_entity[0].trip.start_date = '20260928'
    apply_alerts([d], [('trip', a)], 's', NOW)
    assert not d.alerts
    a.informed_entity[0].trip.start_date = d.service_date
    a.informed_entity[0].trip.direction_id = 0
    apply_alerts([d], [('trip', a)], 's', NOW)
    assert not d.alerts
    a.informed_entity[0].trip.direction_id = 1
    apply_alerts([d], [('trip', a)], 's', NOW)
    assert len(d.alerts) == 1


def test_service_alert_feed_is_loaded(provider, monkeypatch):
    f = feed()
    f.entity.add(id='station').alert.CopyFrom(alert({'stop_id': 's'}))
    async def get(self, url, headers):
        return httpx.Response(200, content=f.SerializeToString(), request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    provider.realtime = RealtimeFeed('https://example.org/feed')
    async def loaded_board():
        await provider.realtime._refresh()
        return await provider.board('s', 'departures', NOW)
    board = asyncio.run(loaded_board())
    assert board.alerts[0].header == 'Bauarbeiten'
    assert board.journeys[0].alerts[0].description == 'Bitte Hinweise beachten'


def ris_payload(kind='departures', time_type='PREVIEW'):
    # Synthetic fixture using field names/types from the official OpenAPI 1.8.2.
    return {kind: [{
        'journeyID': 'ris-trip', 'departureID': 'dep1', 'arrivalID': 'arr1',
        'timeSchedule': (NOW + timedelta(minutes=7)).isoformat(),
        'time': (NOW + timedelta(minutes=12)).isoformat(), 'timeType': time_type,
        'canceled': False, 'platform': '9', 'platformSchedule': '7',
        'administration': {'operatorName': 'DB Test'},
        'transport': {'type': 'REGIONAL_TRAIN', 'journeyDescription': 'RE 1',
                      'categoryInternal': 'NEVER EXPOSE',
                      'destination': {'name': 'Ahrensfelde'}, 'origin': {'name': 'Potsdam'}},
        'messages': [{'text': 'Abweichendes Gleis', 'type': 'INFORMATION'}],
        'disruptions': [{'descriptions': {'DE': {'text': 'Signalstörung'}}}],
    }], 'disruptions': [{'descriptions': {'DE': {'text': 'Bahnhofsarbeiten'}}}]}


@pytest.mark.parametrize('kind,target', [('departures','Ahrensfelde'), ('arrivals','Potsdam')])
def test_ris_normalization(kind, target):
    board = normalize_board(ris_payload(kind), Stop(id='s', name='Berlin'), kind, NOW)
    d = board.journeys[0]
    assert isinstance(d, Departure) and d.destination == target
    assert d.realtime and d.delay_minutes == 5
    assert d.platform == '9' and d.scheduled_platform == '7'
    assert d.operator == 'DB Test'
    assert [a.header for a in d.alerts] == ['Abweichendes Gleis', 'Signalstörung']
    assert board.alerts[0].header == 'Bahnhofsarbeiten'
    assert 'NEVER EXPOSE' not in board.model_dump_json()
    assert 'timeType' not in board.model_dump_json()


def test_ris_schedule_is_not_realtime_and_cancellation_independent():
    payload = ris_payload(time_type='SCHEDULE')
    payload['departures'][0]['canceled'] = True
    d = normalize_board(payload, Stop(id='s', name='Berlin'), 'departures', NOW).journeys[0]
    assert d.realtime is None and d.delay_minutes is None and d.cancelled


def test_ris_request_auth_and_mapping(provider, monkeypatch):
    calls = []
    async def get(self, url, headers, params):
        calls.append((url, headers, params))
        return httpx.Response(200, json=ris_payload(), request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    ris = DBRISProvider(provider, {'s': '8011160'}, 'client', 'key')
    async def run():
        assert await ris.fetch_board(Stop(id='other', name='Other'), 'departures', NOW) is None
        await ris.fetch_board(Stop(id='s', name='Berlin'), 'departures', NOW)
        await ris.fetch_board(Stop(id='s', name='Berlin'), 'departures', NOW)
    asyncio.run(run())
    assert len(calls) == 1
    url, headers, params = calls[0]
    assert url.endswith('/public/departures/8011160')
    assert headers['DB-Client-Id'] == 'client' and headers['DB-Api-Key'] == 'key'
    assert params['includeMessagesDisruptions'] == 'true'


def test_supplement_enriches_only_explicit_matches(provider, monkeypatch):
    ris = DBRISProvider(provider, {'s': '8011160'}, 'client', 'key')
    async def extra(stop, kind, now):
        return normalize_board(ris_payload(), stop, kind, now)
    monkeypatch.setattr(ris, 'fetch_board', extra)
    combined = SupplementedProvider(provider, ris, {'t': 'ris-trip'})
    board = asyncio.run(combined.board('s', 'departures', NOW))
    d = next(d for d in board.journeys if d.trip_id == 't')
    assert len(board.journeys) == 2 and d.realtime and d.platform == '9'
    assert d.operator == 'Test'  # Static GTFS remains the primary source.
    unmatched = asyncio.run(SupplementedProvider(provider, ris, {}).board('s', 'departures', NOW))
    assert all(d.realtime is None for d in unmatched.journeys)


def test_ris_outage_retains_gtfs(provider, monkeypatch):
    async def get(*args, **kwargs):
        raise httpx.ReadTimeout('unavailable')
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    ris = DBRISProvider(provider, {'s': '8011160'}, 'client', 'key')
    board = asyncio.run(SupplementedProvider(provider, ris, {'t': 'ris-trip'}).board('s', 'departures', NOW))
    assert len(board.journeys) == 2 and board.notice is None
    assert all(d.realtime is None for d in board.journeys)


def test_factory_wires_optional_ris(provider, monkeypatch, tmp_path):
    from app.providers.factory import create_provider
    monkeypatch.setenv('DATABASE_PATH', str(provider.path))
    monkeypatch.setenv('TRANSIT_PROVIDER', 'gtfs')
    monkeypatch.setenv('DB_RIS_ENABLED', 'true')
    monkeypatch.setenv('DB_CLIENT_ID', 'client')
    monkeypatch.setenv('DB_API_KEY', 'key')
    path = tmp_path / 'mapping.json'
    path.write_text(json.dumps({'stations': {'s': '8011160'}, 'trips': {'t': 'ris-trip'}}))
    monkeypatch.setenv('DB_RIS_MAPPING_PATH', str(path))
    assert isinstance(create_provider(), SupplementedProvider)


def test_alert_index_preserves_matching_and_excludes_unrelated_entries():
    from app.providers.gtfs_realtime import Snapshot, index_alerts, matching_alert_entries
    d = journey()
    d.route_id, d.agency_id, d.route_type = 'r', 'a', 2
    trip = alert()
    trip.informed_entity[0].trip.trip_id = d.trip_id
    multi = alert({'stop_id': 'elsewhere'})
    multi.informed_entity.add(route_id='r', agency_id='a')
    entries = [(str(i), alert({'stop_id': f'unrelated-{i}'})) for i in range(1000)]
    entries += [('station', alert({'stop_id': 's'})), ('route', alert({'route_id': 'r'})),
                ('agency', alert({'agency_id': 'a'})), ('type', alert({'route_type': 2})),
                ('global', alert()), ('trip', trip), ('multi', multi),
                ('and-mismatch', alert({'stop_id': 's', 'route_id': 'wrong'}))]
    snapshot = Snapshot(alerts=entries, alert_index=index_alerts(entries))
    selected = matching_alert_entries(snapshot, [d], 's')
    assert len(selected) == 8
    indexed, full = d.model_copy(deep=True), d.model_copy(deep=True)
    assert apply_alerts([indexed], selected, 's', NOW) == apply_alerts([full], entries, 's', NOW)
    assert indexed.alerts == full.alerts


def test_background_download_shared_then_available(monkeypatch):
    async def run():
        release = asyncio.Event()
        calls = []
        async def get(self, url, headers):
            calls.append(url)
            await release.wait()
            return httpx.Response(200, content=feed().SerializeToString(), request=httpx.Request('GET', url))
        monkeypatch.setattr(httpx.AsyncClient, 'get', get)
        rt = RealtimeFeed('https://example.org/feed')
        await rt.start()
        for _ in range(5):
            _, status = await rt.current_snapshot()
            assert status == 'loading'
        await asyncio.sleep(0)
        assert len(calls) == 1
        release.set()
        async def loaded():
            while not rt.cached.fresh():
                await asyncio.sleep(.001)
        await asyncio.wait_for(loaded(), 1)
        snapshot, status = await rt.current_snapshot()
        assert status == 'available' and snapshot.fresh()
        assert len(calls) == 1
        await rt.stop()
        assert rt.refresh_task is None
    asyncio.run(run())


def provenance_alert(text='Echtzeitdaten aufbereitet von GTFS.de, bereitgestellt von DELFI'):
    a = gtfs.Alert(cause=gtfs.Alert.UNKNOWN_CAUSE, effect=gtfs.Alert.UNKNOWN_EFFECT,
                   severity_level=gtfs.Alert.INFO)
    a.informed_entity.add().trip.trip_id = 't'
    a.description_text.translation.add(text=text, language='de')
    return a


def test_provenance_classification_uses_source_fields_and_content():
    from app.providers.gtfs_realtime import is_provenance_alert, DEFAULT_FEED_URL
    a = provenance_alert()
    assert is_provenance_alert(a, DEFAULT_FEED_URL)
    assert not is_provenance_alert(a, 'https://example.org/feed')
    assert is_provenance_alert(provenance_alert(
        'Realtime data processed by GTFS.de, provided by DELFI'), DEFAULT_FEED_URL)
    assert is_provenance_alert(provenance_alert(
        'Echtzeitdaten aufbereitet von GTFS.de, bereitgestellt von opentransportdata.swiss'), DEFAULT_FEED_URL)
    for text in ['Rollstuhlgeeignet', 'Niederflur', 'Rolltreppe am Bahnhof außer Betrieb',
                 'Informationen auf GTFS.de',
                 'Echtzeitdaten aufbereitet von GTFS.de, bereitgestellt von DELFI. Zug fällt aus.',
                 'Echtzeitdaten aufbereitet von GTFS.de, bereitgestellt von DELFI\nZug fällt aus']:
        assert not is_provenance_alert(provenance_alert(text), DEFAULT_FEED_URL)
    a.header_text.translation.add(text='Zug fällt aus', language='de')
    assert not is_provenance_alert(a, DEFAULT_FEED_URL)
    a.ClearField('header_text')
    a.effect = gtfs.Alert.NO_SERVICE
    assert not is_provenance_alert(a, DEFAULT_FEED_URL)


def test_provenance_removed_before_indexing_without_removing_trip_updates():
    from app.providers.gtfs_realtime import parse_snapshot, DEFAULT_FEED_URL
    f = feed()
    f.entity.add(id='metadata').alert.CopyFrom(provenance_alert())
    f.entity.add(id='accessibility').alert.CopyFrom(provenance_alert('Rollstuhlgeeignet'))
    f.entity.add(id='prediction').trip_update.trip.trip_id = 't'
    snapshot = parse_snapshot(f.SerializeToString(), DEFAULT_FEED_URL)
    assert [key for key, _ in snapshot.alerts] == ['accessibility']
    assert snapshot.alert_index == {('trip', 't'): {0}}
    assert ('t', '') in snapshot.updates


def test_alert_content_dedup_keeps_distinct_descriptions_and_trip_scope():
    from app.providers.gtfs_realtime import index_alerts, matching_alert_entries, Snapshot
    first, second = journey(), journey()
    second.trip_id = 'other'
    a, b = alert(), alert()
    a.informed_entity[0].trip.trip_id = 't'
    b.informed_entity[0].trip.trip_id = 'other'
    different = alert()
    different.description_text.translation[0].text = 'Anderer Ersatzhalt'
    duplicate = alert()
    duplicate.description_text.translation[0].text = ' Bitte   Hinweise beachten '
    entries = [('first', a), ('second', b), ('duplicate', duplicate), ('different', different)]
    snapshot = Snapshot(alerts=entries, alert_index=index_alerts(entries))
    selected = matching_alert_entries(snapshot, [first, second], 's')
    board = apply_alerts([first, second], selected, 's', NOW)
    assert len(board) == 2
    assert len(first.alerts) == len(second.alerts) == 2
    assert first.alerts[0].id == 'gtfs-rt:first'
    assert second.alerts[0].id == 'gtfs-rt:second'


def test_cache_reads_never_trigger_upstream_even_when_expired(monkeypatch):
    from app.providers.gtfs_realtime import Snapshot
    async def forbidden(*args, **kwargs):
        raise AssertionError('Client triggered upstream')
    monkeypatch.setattr(httpx.AsyncClient, 'get', forbidden)
    async def run():
        rt = RealtimeFeed('https://example.org/feed')
        for stamp in (0, int(time.time()), int(time.time()) - 1000):
            rt.cached = Snapshot(timestamp=stamp)
            results = await asyncio.gather(*(rt.current_snapshot() for _ in range(50)))
            assert all(status == ('available' if stamp and time.time()-stamp < 180 else 'unavailable')
                       for _, status in results)
            assert rt.refresh_task is None
    asyncio.run(run())


def test_lifespan_refreshes_without_clients_and_stops(monkeypatch, provider):
    import app.providers.gtfs_realtime as rt_module
    original_sleep = asyncio.sleep
    waits, calls = [], []
    async def controlled_sleep(delay):
        if delay != FEED_INTERVAL:
            return await original_sleep(delay)
        waits.append(delay)
        if len(waits) == 1:
            # Advance only the refresh guard, avoiding a real 30-second test.
            provider.realtime.checked -= FEED_INTERVAL
            return
        await asyncio.Event().wait()
    async def get(self, url, headers):
        calls.append(url)
        return httpx.Response(200, content=feed().SerializeToString(), request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    monkeypatch.setattr(rt_module.asyncio, 'sleep', controlled_sleep)
    provider.realtime = RealtimeFeed('https://example.org/feed')
    async def run():
        application = create_app(provider)
        async with application.router.lifespan_context(application):
            task = provider.realtime.refresh_task
            await provider.start()
            assert provider.realtime.refresh_task is task
            async def refreshed_twice():
                while len(waits) < 2:
                    await original_sleep(.001)
            await asyncio.wait_for(refreshed_twice(), 1)
            assert len(calls) == 2 and waits == [30, 30]
        assert task.done() and provider.realtime.refresh_task is None
    asyncio.run(run())
