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


def test_realtime_ten_second_cache_and_refresh(monkeypatch):
    calls = []
    async def get(self, url, headers):
        calls.append(url)
        return httpx.Response(200, content=feed().SerializeToString(), request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    rt = RealtimeFeed('https://example.org/feed')
    async def run():
        await rt.snapshot()
        rt.checked -= 9
        await rt.snapshot()
        assert len(calls) == 1
        rt.checked -= 2
        await rt.snapshot()
    asyncio.run(run())
    assert FEED_INTERVAL == 10 and len(calls) == 2


def test_cached_feed_expires_even_before_next_request(monkeypatch):
    f = feed()
    async def get(self, url, headers):
        return httpx.Response(200, content=f.SerializeToString(), request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    rt = RealtimeFeed('https://example.org/feed')
    async def run():
        await rt.snapshot()
        rt.cached.timestamp -= 1000
        return await rt.snapshot()
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
    board = asyncio.run(provider.board('s', 'departures', NOW))
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
