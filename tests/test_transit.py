import asyncio
from datetime import datetime, timezone
import time
from zipfile import ZipFile
import httpx
import pytest
from fastapi.testclient import TestClient
from google.transit import gtfs_realtime_pb2 as gtfs
from app.import_gtfs import import_feed, seconds
from app.main import create_app
from app.models import Departure
from app.providers import DemoProvider, GTFSProvider
from app.providers.gtfs_static import BERLIN, service_start
from app.providers.gtfs_realtime import RealtimeFeed, apply_update


def write_feed(path, extra=None):
    files = {
        'agency.txt': 'agency_id,agency_name,agency_url,agency_timezone\na,Test,https://example.org,Europe/Berlin\n',
        'stops.txt': 'stop_id,stop_name,parent_station,platform_code,location_type\ns,Berlin Hbf,,,1\np,Berlin Hbf,s,7,0\no,Potsdam,,,0\nd,Ahrensfelde,,,0\n',
        'routes.txt': 'route_id,route_short_name,route_long_name,route_type\nr,RE 1,Regionalexpress,2\n',
        'trips.txt': 'route_id,service_id,trip_id,trip_headsign\nr,weekday,t,Ahrensfelde\nr,added,added,Ahrensfelde\nr,removed,removed,Ahrensfelde\n',
        'stop_times.txt': 'trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type,drop_off_type\nt,23:30:00,23:31:00,o,1,0,1\nt,24:05:00,24:07:00,p,2,0,0\nt,24:30:00,24:30:00,d,3,1,0\nadded,24:20:00,24:21:00,p,1,0,0\nremoved,24:10:00,24:11:00,p,1,0,0\n',
        'calendar.txt': 'service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\nweekday,1,1,1,1,1,1,1,20260901,20260930\nremoved,1,1,1,1,1,1,1,20260901,20260930\n',
        'calendar_dates.txt': 'service_id,date,exception_type\nadded,20260929,1\nremoved,20260929,2\n',
    }
    files.update(extra or {})
    with ZipFile(path, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)


@pytest.fixture
def provider(tmp_path):
    archive = tmp_path / 'feed.zip'
    write_feed(archive)
    db = tmp_path / 'gtfs.sqlite'
    import_feed(archive, db)
    return GTFSProvider(db, RealtimeFeed(None))


NOW = datetime(2026, 9, 30, 0, 0, tzinfo=BERLIN)


def test_search_casefold_and_parent_stations(provider):
    assert [s.id for s in provider.search('BERLIN')] == ['s']
    assert provider.search('%') == []
    assert provider.search("' OR 1=1 --") == []


def test_overnight_calendar_exceptions_and_platform(provider):
    board = asyncio.run(provider.board('s', 'departures', NOW))
    assert [j.trip_id for j in board.journeys] == ['t', 'added']
    j = board.journeys[0]
    assert j.scheduled.hour == 0 and j.scheduled.minute == 7
    assert j.platform == '7'
    assert j.service_date == '20260929'
    assert not j.realtime and j.realtime is None
    assert board.notice is None


def test_arrivals_use_arrival_time_and_origin(provider):
    board = asyncio.run(provider.board('s', 'arrivals', NOW))
    assert board.journeys[0].scheduled.minute == 5
    assert board.journeys[0].destination == 'Potsdam'


def test_no_departure_at_dropoff_only_terminal(provider):
    assert asyncio.run(provider.board('d', 'departures', NOW)).journeys == []
    assert asyncio.run(provider.board('d', 'arrivals', NOW)).journeys


def test_unknown_station(provider):
    with pytest.raises(KeyError):
        asyncio.run(provider.board('missing', 'departures', NOW))


def test_failed_import_preserves_previous_database(provider, tmp_path):
    before = provider.path.read_bytes()
    bad = tmp_path / 'bad.zip'
    write_feed(bad, {'frequencies.txt': 'trip_id,start_time,end_time,headway_secs\nt,08:00:00,09:00:00,600\n'})
    with pytest.raises(ValueError, match='frequencies'):
        import_feed(bad, provider.path)
    assert provider.path.read_bytes() == before
    assert not list(tmp_path.glob('gtfs-*.sqlite'))


def journey():
    return Departure(id='j', trip_id='t', stop_id='p', sequence=2, service_date='20260929', line='RE 1',
                   mode='rail', destination='Berlin', scheduled=NOW, source='GTFS')


def update():
    u = gtfs.TripUpdate()
    u.trip.trip_id = 't'
    u.trip.start_date = '20260929'
    return u


def apply(j, u, kind='departures'):
    apply_update(j, kind, {('t', '20260929'): u})


def test_explicit_zero_delay_is_realtime():
    j, u = journey(), update()
    stop = u.stop_time_update.add(stop_sequence=2, stop_id='p')
    stop.departure.delay = 0
    apply(j, u)
    assert j.realtime and j.delay_minutes == 0 and j.realtime == NOW


def test_delay_and_arrival_are_separate():
    j, u = journey(), update()
    stop = u.stop_time_update.add(stop_sequence=2)
    stop.departure.delay = 300
    stop.arrival.delay = 120
    apply(j, u)
    assert j.delay_minutes == 5
    other = journey()
    apply(other, u, 'arrivals')
    assert other.delay_minutes == 2


@pytest.mark.parametrize('cancel_trip', [True, False])
def test_cancelled_trip_and_skipped_stop(cancel_trip):
    j, u = journey(), update()
    if cancel_trip:
        u.trip.schedule_relationship = gtfs.TripDescriptor.CANCELED
    else:
        u.stop_time_update.add(stop_sequence=2, schedule_relationship=gtfs.TripUpdate.StopTimeUpdate.SKIPPED)
    apply(j, u)
    assert j.cancelled and j.realtime is None


def test_no_data_and_sequence_do_not_invent_predictions():
    j, u = journey(), update()
    u.delay = 300
    u.stop_time_update.add(stop_sequence=2, schedule_relationship=gtfs.TripUpdate.StopTimeUpdate.NO_DATA)
    apply(j, u)
    assert not j.realtime and j.realtime is None
    u.ClearField('delay')
    u.stop_time_update[0].stop_sequence = 3
    u.stop_time_update[0].stop_id = 'p'
    apply(j, u)
    assert not j.realtime


def test_different_service_date_does_not_match():
    j, u = journey(), update()
    u.delay = 120
    apply_update(j, 'departures', {('t', '20260928'): u})
    assert not j.realtime


def test_absolute_realtime_timestamp():
    j, u = journey(), update()
    u.stop_time_update.add(stop_sequence=2).departure.time = int(NOW.timestamp()) + 180
    apply(j, u)
    assert j.delay_minutes == 3


def test_delay_across_autumn_clock_change_is_elapsed_time():
    j, u = journey(), update()
    j.scheduled = datetime(2026, 10, 25, 2, 55, tzinfo=BERLIN, fold=0)
    u.stop_time_update.add(stop_sequence=2).departure.delay = 600
    apply(j, u)
    assert j.delay_minutes == 10
    assert j.realtime.hour == 2 and j.realtime.minute == 5 and j.realtime.fold == 1


@pytest.mark.parametrize('stale', [True, False])
def test_feed_freshness_and_network_cache(monkeypatch, stale):
    feed = gtfs.FeedMessage()
    feed.header.gtfs_realtime_version = '2.0'
    feed.header.timestamp = int(time.time()) - (600 if stale else 0)
    entity = feed.entity.add(id='1')
    entity.trip_update.CopyFrom(update())
    calls = []
    async def get(self, url, headers):
        calls.append(url)
        return httpx.Response(200, content=feed.SerializeToString(), request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    rt = RealtimeFeed('https://example.org/feed')
    async def run():
        first = await rt._refresh()
        second = await rt._refresh()
        return first, second
    first, second = asyncio.run(run())
    assert first == second and len(calls) == 1
    assert bool(first.updates) is not stale
    assert first.alerts == []


def test_dst_service_origin_and_gtfs_hours():
    assert seconds('25:10:00') == 90600
    assert seconds('') is None
    assert service_start(datetime(2026, 3, 29).date()) == datetime(2026, 3, 28, 22, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        seconds('12:60:00')


def test_api_and_pwa_assets():
    with TestClient(create_app(DemoProvider())) as client:
        stops = client.get('/api/stops?q=berlin').json()
        assert len(stops) == 2
        board = client.get('/api/board', params={'stop_id': stops[0]['id']})
        assert board.status_code == 200 and board.json()['demo'] is True
        assert board.headers['cache-control'] == 'no-store'
        assert len(board.json()['journeys']) == 12
        assert client.get('/api/board?stop_id=missing').status_code == 404
        assert client.get('/api/board?stop_id=demo-berlin&kind=bad').status_code == 422
        for url in ['/', '/static/app.js', '/static/style.css', '/static/manifest.webmanifest', '/sw.js']:
            assert client.get(url).status_code == 200


def test_static_provider_works_without_realtime(provider):
    from app.providers import GTFSStaticProvider
    static = GTFSStaticProvider(provider.path)
    with TestClient(create_app(static)) as client:
        assert client.get('/api/stops?q=Berlin').json() == [{'id': 's', 'name': 'Berlin Hbf', 'dataset_version': static.dataset_version()}]
    board = asyncio.run(static.board('s', 'departures', NOW))
    assert [j.trip_id for j in board.journeys] == ['t', 'added']
    assert all(not j.realtime and j.realtime is None for j in board.journeys)


def test_api_accepts_an_independent_provider():
    from app.models import Board, Stop

    class IndependentProvider:
        def search(self, query):
            return [Stop(id='external:1', name='Externer Bahnhof')]

        async def board(self, stop_id, kind, now):
            return Board(stop=self.search('')[0], kind=kind, journeys=[journey()],
                         updated_at=now, source='Andere Quelle')

    with TestClient(create_app(IndependentProvider())) as client:
        assert client.get('/api/stops?q=test').json()[0]['id'] == 'external:1'
        result = client.get('/api/board?stop_id=external:1&kind=arrivals')
        assert result.status_code == 200
        assert result.json()['source'] == 'Andere Quelle'
        assert result.json()['kind'] == 'arrivals'
        assert result.json()['journeys'][0]['line'] == 'RE 1'


def test_provider_factory_selection(monkeypatch, provider, tmp_path):
    from app.providers.factory import create_provider
    monkeypatch.setenv('DATABASE_PATH', str(provider.path))
    monkeypatch.setenv('TRANSIT_PROVIDER', 'auto')
    assert isinstance(create_provider(), GTFSProvider)
    monkeypatch.setenv('TRANSIT_PROVIDER', 'demo')
    assert isinstance(create_provider(), DemoProvider)
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'missing.sqlite'))
    monkeypatch.setenv('TRANSIT_PROVIDER', 'auto')
    assert isinstance(create_provider(), DemoProvider)
    monkeypatch.setenv('TRANSIT_PROVIDER', 'gtfs')
    with pytest.raises(RuntimeError, match='Datenbank fehlt'):
        create_provider()
    monkeypatch.setenv('TRANSIT_PROVIDER', 'unknown')
    with pytest.raises(RuntimeError, match='TRANSIT_PROVIDER'):
        create_provider()


def test_static_api_returns_while_realtime_is_blocked(provider):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def slow_snapshot():
            from app.providers.gtfs_realtime import Snapshot
            started.set()
            await release.wait()
            return Snapshot()

        provider.realtime.url = "https://example.org/feed"
        provider.realtime._refresh = slow_snapshot
        application = create_app(provider)
        transport = httpx.ASGITransport(app=application)
        async with application.router.lifespan_context(application), httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            pending = asyncio.create_task(client.get('/api/board?stop_id=s&realtime=true'))
            await asyncio.wait_for(started.wait(), 1)
            try:
                response = await asyncio.wait_for(client.get('/api/board?stop_id=s&realtime=false'), 1)
                assert response.status_code == 200
                assert response.json()['source'] == 'GTFS'
                realtime = await asyncio.wait_for(pending, 1)
                assert realtime.json()["realtime_status"] == "loading"
                assert not provider.realtime.refresh_task.done()
            finally:
                release.set()
                await pending
        assert provider.realtime.refresh_task is None
    asyncio.run(run())


def test_static_query_skips_past_events_but_realtime_keeps_candidates(provider):
    now = NOW.replace(minute=10)
    static = asyncio.run(provider.static_board('s', 'departures', now))
    assert [j.trip_id for j in static.journeys] == ['added']
    _, candidates = provider.scheduled('s', 'departures', now)
    assert {j.trip_id for j in candidates} == {'t', 'added'}


@pytest.mark.parametrize('query', ['ber hbf', 'hbf ber', 'lin', '  HBF   BER  ', 'Berlin Hauptbahnhof'])
def test_search_partial_words_any_order(provider, query):
    assert [s.id for s in provider.search(query)] == ['s']


def test_search_umlauts_ranking_and_literal_characters(provider):
    import sqlite3
    with sqlite3.connect(provider.path) as db:
        for identifier, name in [('m', 'München Hauptbahnhof'), ('a', 'Am München Hbf'),
                                 ('w', 'Wolfenbüttel Bahnhof'), ('x', 'Test_100%')]:
            db.execute('INSERT INTO stops VALUES (?,?,?,?,?,?)',
                       (identifier, name, name.casefold(), '', '', 1))
    assert [s.id for s in provider.search('muenchen hbf')] == ['m', 'a']
    assert [s.id for s in provider.search('hbf münch')] == ['a', 'm']
    assert [s.id for s in provider.search('buettel bahn')] == ['w']
    assert [s.id for s in provider.search('_100%')] == ['x']
    assert provider.search('Berlin Potsdam') == []


def test_demo_partial_search():
    demo = DemoProvider()
    assert [s.id for s in demo.search('hbf muen')] == ['demo-muenchen']
    assert [s.id for s in demo.search('platz ber')] == ['demo-alex']


@pytest.mark.parametrize('kind,event,minute', [('departures', 'departure', 7), ('arrivals', 'arrival', 5)])
def test_delayed_journey_survives_scheduled_time_and_sorts_by_prediction(provider, kind, event, minute):
    from app.providers.gtfs_realtime import Snapshot
    u = update()
    getattr(u.stop_time_update.add(stop_sequence=2), event).delay = 20 * 60
    provider.realtime.cached = Snapshot(timestamp=int(time.time()), updates={('t', '20260929'): u})
    board = asyncio.run(provider.board('s', kind, NOW.replace(minute=15)))
    assert [d.trip_id for d in board.journeys] == ['added', 't']
    assert board.journeys[-1].realtime.minute == minute + 20
    for seconds, visible in [(0, True), (60, True), (61, False)]:
        from datetime import timedelta
        now = NOW.replace(minute=minute + 20) + timedelta(seconds=seconds)
        board = asyncio.run(provider.board('s', kind, now))
        assert ('t' in [d.trip_id for d in board.journeys]) == visible
    # Expiry removes predictions rather than keeping the delayed row indefinitely.
    provider.realtime.cached.timestamp -= 301
    board = asyncio.run(provider.board('s', kind, NOW.replace(minute=15)))
    assert [d.trip_id for d in board.journeys] == ['added']
    assert board.realtime_status == 'unavailable'
    assert all(d.realtime is None for d in board.journeys)


@pytest.mark.parametrize('seconds,visible', [(0, True), (60, True), (61, False)])
def test_early_prediction_uses_prediction_grace_not_scheduled_time(seconds, visible):
    from datetime import timedelta
    from app.providers.time_window import visible_departures
    d = journey()
    d.scheduled = NOW + timedelta(minutes=10)
    d.realtime = NOW
    assert bool(visible_departures([d], NOW + timedelta(seconds=seconds))) == visible


def test_cancellations_use_scheduled_slot_and_separate_five_minute_grace():
    from datetime import timedelta
    from app.providers.time_window import visible_departures
    cancelled, predicted = journey(), journey()
    cancelled.cancelled = True
    cancelled.realtime = NOW + timedelta(hours=1)  # Obsolete prediction must not retain cancellation.
    predicted.scheduled = NOW - timedelta(minutes=10)
    predicted.realtime = NOW + timedelta(minutes=1)
    assert visible_departures([predicted, cancelled], NOW) == [cancelled, predicted]
    assert visible_departures([cancelled], NOW + timedelta(minutes=5)) == [cancelled]
    assert visible_departures([cancelled], NOW + timedelta(minutes=5, seconds=1)) == []
    plan = journey()
    assert visible_departures([plan], NOW + timedelta(seconds=1)) == []
