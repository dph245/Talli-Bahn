import asyncio
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import time

import httpx
import pytest

from app.providers.vrb_efa import EFAFeed, Event, MAX_AGE, Prediction, match_predictions
from test_transit import NOW, journey, provider

DHID = 'de:03158:1677:1:1'
NAME = 'Wolfenbüttel, Birkenweg'
DISCOVERY = Path(__file__).resolve().parents[1] / 'docs' / 'efa-discovery'


def event(**changes):
    data = dict(location={'id': DHID, 'name': NAME},
                transportation={'number': 'RE 1', 'destination': {'name': 'Berlin'}},
                departureTimePlanned=NOW, departureTimeEstimated=NOW,
                realtimeStatus=['MONITORED'], isRealtimeControlled=True)
    data.update(changes)
    return Event.model_validate(data)


@pytest.mark.parametrize('case', ['unique', 'destination', 'ambiguous', 'none', 'suffix'])
def test_conservative_match(case):
    first = journey()
    departures = [first]
    if case in ('destination', 'ambiguous', 'suffix'):
        second = first.model_copy(update={'id': 'other', 'trip_id': 'other',
                                         'destination': 'Berlin' if case == 'ambiguous' else 'Potsdam'})
        departures.append(second)
    if case == 'suffix':
        first.destination = 'Berlin | Haltestelle 1'
    if case == 'none':
        first.scheduled += timedelta(seconds=1)
    predictions = match_predictions([event()], departures, DHID, NAME)
    assert predictions == ({} if case in ('none', 'ambiguous') else {'j': Prediction(NOW)})


def test_missing_realtime_and_wrong_stop_do_not_match():
    for e in [event(departureTimeEstimated=None),
              event(realtimeStatus=[], isRealtimeControlled=False),
              event(location={'id': 'other', 'name': NAME})]:
        assert match_predictions([e], [journey()], DHID, NAME) == {}


@pytest.mark.parametrize('failure', ['timeout', 'json'])
def test_efa_failure_keeps_gtfs_board(provider, monkeypatch, failure):
    provider.efa = EFAFeed(provider)
    monkeypatch.setattr(provider.efa, 'resolve_group', lambda name: 's')
    async def get(request):
        if failure == 'timeout':
            raise httpx.ReadTimeout('unavailable')
        return httpx.Response(200, content=b'{invalid json')
    async def run():
        before = await provider.board('s', 'departures', NOW)
        async with httpx.AsyncClient(transport=httpx.MockTransport(get)) as client:
            await provider.efa.refresh_stop(client, DHID, NAME)
        after = await provider.board('s', 'departures', NOW)
        assert after == before
    asyncio.run(run())


def test_cache_priority_expiry_and_board_reads(provider):
    provider.efa = EFAFeed(provider)
    async def run():
        baseline = await provider.board('s', 'departures', NOW)
        first = baseline.journeys[0]
        estimate = first.scheduled + timedelta(minutes=3)
        provider.efa.cached[DHID] = (time.monotonic(), {first.id: Prediction(estimate)})
        # No running fetcher: repeated board reads only reuse the central cache.
        for _ in range(2):
            board = await provider.board('s', 'departures', NOW)
            assert board.journeys[0].realtime == estimate
            assert board.journeys[0].source == 'GTFS + VRB-EFA'
        board.journeys[0].realtime = first.scheduled
        board.journeys[0].source = 'GTFS + GTFS-Realtime'
        provider.efa.enrich(board)
        assert board.journeys[0].realtime == first.scheduled
        board.journeys[0].realtime = None
        board.journeys[0].cancelled = True
        provider.efa.enrich(board)
        assert board.journeys[0].realtime is None
        arrivals = await provider.board('s', 'arrivals', NOW)
        assert all(d.realtime is None for d in arrivals.journeys)
        provider.efa.cached[DHID] = (time.monotonic() - MAX_AGE - 1, {first.id: Prediction(estimate)})
        assert await provider.board('s', 'departures', NOW) == baseline
    asyncio.run(run())


def test_stop_group_requires_unique_exact_name(provider):
    feed = EFAFeed(provider)
    assert feed.resolve_group('Berlin Hbf') == 's'
    assert feed.resolve_group('berlin hbf') is None
    assert feed.resolve_group('berlin hbf', 's') == 's'
    assert feed.resolve_group('Wrong stop', 's') is None
    with sqlite3.connect(provider.path) as db:
        db.execute("INSERT INTO stops VALUES ('duplicate','Berlin Hbf','berlin hbf','','',1)")
    assert feed.resolve_group('Berlin Hbf') is None


def test_refresh_matches_active_gtfs_service_day(provider, monkeypatch):
    from types import SimpleNamespace
    from app.providers import vrb_efa
    monkeypatch.setattr(vrb_efa, 'datetime', SimpleNamespace(now=lambda tz: NOW))
    provider.efa = EFAFeed(provider)
    _, scheduled = provider.scheduled('s', 'departures', NOW)
    first = next(d for d in scheduled if d.trip_id == 't')
    e = event(location={'id': DHID, 'name': 'Berlin Hbf'},
              departureTimePlanned=first.scheduled,
              departureTimeEstimated=first.scheduled + timedelta(minutes=2))
    requests = []
    async def get(request):
        requests.append(request)
        assert dict(request.url.params) == dict(name_dm=DHID, type_dm='stop',
            useRealtime='1', limit='20', outputFormat='rapidJSON', mode='direct')
        return httpx.Response(200, json={'stopEvents': [e.model_dump(mode='json')]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(get)) as client:
            await provider.efa.refresh_stop(client, DHID, 'Berlin Hbf')
        board = await provider.board('s', 'departures', NOW)
        matched = next(d for d in board.journeys if d.trip_id == 't')
        assert matched.service_date == '20260929'  # GTFS 24:07, previous service day
        assert matched.delay_minutes == 2
        assert next(d for d in board.journeys if d.trip_id == 'added').realtime is None
        assert len(requests) == 1
    asyncio.run(run())


def test_on_demand_refresh_is_nonblocking_deduplicated_and_throttled(provider, monkeypatch):
    from types import SimpleNamespace
    from app.providers import vrb_efa
    clock = [1000.0]
    monkeypatch.setattr(vrb_efa, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    feed = provider.efa = EFAFeed(provider)
    monkeypatch.setattr(feed.mapping, "group", lambda stop_id, name: dict(id="s", name="Berlin Hbf", coord=[52, 10]) if stop_id == "s" else None)
    monkeypatch.setattr(feed.mapping, "read", lambda group: dict(status="UNIQUE", dhid=DHID, name="Berlin Hbf"))
    calls = []

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def refresh(client, dhid, name, group_id=None):
            calls.append(dhid)
            entered.set()
            await release.wait()  # An arbitrarily slow upstream must not hold the board.
            if len(calls) == 1:
                feed.cached[group_id] = (clock[0], {})
            # The second attempt simulates a handled failure, leaving the cache unchanged.
        monkeypatch.setattr(feed, 'refresh_stop', refresh)
        await feed.start()
        await asyncio.sleep(0)
        assert not calls and not feed.tasks
        await provider.board('s', 'arrivals', NOW)
        await provider.static_board('s', 'departures', NOW)
        assert not feed.tasks
        boards = await asyncio.wait_for(asyncio.gather(*(
            provider.board('s', 'departures', NOW) for _ in range(5))), timeout=2)
        assert all(b.journeys and all(d.realtime is None for d in b.journeys) for b in boards)
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert calls == [DHID]
        release.set()
        await feed.tasks['s']
        clock[0] += 59
        await provider.board('s', 'departures', NOW)
        assert calls == [DHID]
        clock[0] += 2
        await asyncio.sleep(0)
        assert calls == [DHID]  # Aging alone never triggers polling.
        await provider.board('s', 'departures', NOW)
        await feed.tasks['s']
        assert calls == [DHID, DHID]
        await provider.board('s', 'departures', NOW)
        await asyncio.sleep(0)
        assert calls == [DHID, DHID]  # Failure is throttled despite the old cache.
        await feed.stop()
    asyncio.run(run())


def test_unresolved_group_preserves_fresh_cache(provider, monkeypatch):
    feed = EFAFeed(provider)
    feed.cached[DHID] = (time.monotonic(), {'j': Prediction(NOW)})
    before = dict(feed.cached)
    monkeypatch.setattr(feed, 'resolve_group', lambda name: None)
    async def get(request):
        pytest.fail('An unresolved group must not request EFA')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(get)) as client:
            await feed.refresh_stop(client, DHID, NAME)
        assert feed.cached == before
    asyncio.run(run())


@pytest.mark.parametrize('case', ['recorded_empty', 'explicit_empty', 'missing',
                                  'unknown_error', 'mixed_errors', 'null', 'invalid_event'])
def test_refresh_empty_result_and_invalid_payloads(provider, monkeypatch, caplog, case):
    raw = (DISCOVERY / 'birkenweg-after-service-board.json').read_bytes()
    payload = json.loads(raw)
    if case == 'explicit_empty':
        payload = {'stopEvents': []}
    elif case == 'missing':
        payload = {}
    elif case == 'unknown_error':
        payload['systemMessages'][0]['code'] = -9999
    elif case == 'mixed_errors':
        payload['systemMessages'].append({'type': 'error', 'module': 'BROKER', 'code': -9999})
    elif case == 'null':
        payload['stopEvents'] = None
    elif case == 'invalid_event':
        payload['stopEvents'] = [{}]
    feed = EFAFeed(provider)
    monkeypatch.setattr(feed, 'resolve_group', lambda name, group_id: 's')
    old = (time.monotonic() - 10, {'j': Prediction(NOW)})
    feed.cached['629402'] = old

    async def get(request):
        assert dict(request.url.params) == dict(name_dm='de:03158:1677', type_dm='stop',
            useRealtime='1', limit='20', outputFormat='rapidJSON', mode='direct')
        return httpx.Response(200, content=raw if case == 'recorded_empty' else json.dumps(payload).encode())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(get)) as client:
            events = await feed.refresh_stop(client, 'de:03158:1677', NAME, '629402')
        if case in ('recorded_empty', 'explicit_empty'):
            assert events == []
            assert feed.cached['629402'][0] > old[0]
            assert feed.cached['629402'][1] == {}
            assert not caplog.records
        else:
            assert events is None
            assert feed.cached['629402'] == old
            assert 'ValidationError' in caplog.text
    asyncio.run(run())


@pytest.mark.parametrize('properties, expected', [
    ({'platformName': ' Bstg. E ', 'platform': 'E'}, 'Bstg. E'),
    ({'platformName': ' ', 'platform': '1'}, '1'),
    ({'platformName': None, 'platform': ' '}, None),
])
def test_platform_comes_from_matched_event(provider, properties, expected):
    from app.models import Board, Stop
    d = journey()
    e = event(location={'id': DHID, 'name': NAME, 'properties': properties})
    feed = EFAFeed(provider)
    feed.stopped = True
    feed.cached[DHID] = (time.monotonic(), match_predictions([e], [d], DHID, NAME))
    board = Board(stop=Stop(id='s', name=NAME), kind='departures', journeys=[d],
                  updated_at=NOW, source='GTFS')
    feed.enrich(board)
    assert d.platform == expected
    assert d.scheduled_platform is None
    assert d.realtime == NOW


def test_platform_fills_gap_without_overwriting_existing_data(provider):
    from app.models import Board, Stop
    d = journey()
    d.realtime = NOW + timedelta(minutes=2)
    d.source = 'GTFS + GTFS-Realtime'
    d.platform = '7'
    d.scheduled_platform = '7'
    feed = EFAFeed(provider)
    feed.stopped = True
    feed.cached[DHID] = (time.monotonic(), {d.id: Prediction(NOW, 'Bstg. E')})
    board = Board(stop=Stop(id='s', name=NAME), kind='departures', journeys=[d],
                  updated_at=NOW, source=d.source)
    feed.enrich(board)
    assert d.platform == '7' and d.source == 'GTFS + GTFS-Realtime'
    d.platform = None
    feed.enrich(board)
    assert d.platform == 'Bstg. E'
    assert d.scheduled_platform == '7'
    assert d.realtime == NOW + timedelta(minutes=2)
    assert d.source == 'GTFS + GTFS-Realtime + VRB-EFA'


def test_platform_is_not_borrowed_from_other_or_conflicting_events():
    d = journey()
    own = event()  # No platform; a different event must not supply one.
    other = event(departureTimePlanned=NOW + timedelta(minutes=1),
                  location={'id': DHID, 'name': NAME, 'properties': {'platform': '9'}})
    assert match_predictions([own, other], [d], DHID, NAME) == {d.id: Prediction(NOW)}
    conflicting = event(location={'id': DHID, 'name': NAME, 'properties': {'platform': '8'}})
    assert match_predictions([own, conflicting], [d], DHID, NAME) == {d.id: Prediction(NOW)}
    duplicate = d.model_copy(update={'id': 'other', 'trip_id': 'other'})
    assert match_predictions([conflicting], [d, duplicate], DHID, NAME) == {}
