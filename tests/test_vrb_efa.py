import asyncio
from datetime import timedelta
import sqlite3
import time

import httpx
import pytest

from app.providers.vrb_efa import EFAFeed, Event, MAX_AGE, match_predictions
from test_transit import NOW, journey, provider

DHID = 'de:03158:1677:1:1'
NAME = 'Wolfenbüttel, Birkenweg'


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
    assert predictions == ({} if case in ('none', 'ambiguous') else {'j': NOW})


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
        provider.efa.cached[DHID] = (time.monotonic(), {first.id: estimate})
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
        provider.efa.cached[DHID] = (time.monotonic() - MAX_AGE - 1, {first.id: estimate})
        assert await provider.board('s', 'departures', NOW) == baseline
    asyncio.run(run())


def test_stop_group_requires_unique_exact_name(provider):
    feed = EFAFeed(provider)
    assert feed.resolve_group('Berlin Hbf') == 's'
    assert feed.resolve_group('berlin hbf') is None
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
