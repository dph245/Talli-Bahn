import asyncio
from copy import deepcopy

import httpx
import pytest

from app.models import Stop
from app.providers.transport_rest import TransportRestProvider, enrich_platforms
from test_transit import provider, NOW


def row(kind='departures'):
    return {'stop': {'id': '8011160'}, 'line': {'name': 'RE1', 'mode': 'train'},
            'plannedWhen': '2026-09-30T00:07:00+02:00' if kind == 'departures' else '2026-09-30T00:05:00+02:00',
            'direction': 'Ahrensfelde', 'provenance': 'Potsdam', 'platform': '9', 'plannedPlatform': '7'}


@pytest.mark.parametrize('kind', ['departures', 'arrivals'])
def test_platforms_and_changes_without_changing_times(provider, kind):
    board = asyncio.run(provider.board('s', kind, NOW))
    original = deepcopy(board)
    enrich_platforms(board, [row(kind)], '8011160')
    d = next(d for d in board.journeys if d.trip_id == 't')
    assert (d.platform, d.scheduled_platform) == ('9', '7')
    assert d.realtime is None and d.scheduled == original.journeys[0].scheduled
    assert len(board.journeys) == len(original.journeys)
    assert 'transport.rest' in d.source


@pytest.mark.parametrize('change', ['station', 'line', 'time', 'direction', 'duplicate', 'naive', 'invalid', 'primary_duplicate'])
def test_unsafe_matches_are_ignored(provider, change):
    board = asyncio.run(provider.board('s', 'departures', NOW))
    item = row()
    rows = [item]
    if change == 'station': item['stop']['id'] = 'other'
    if change == 'line': item['line']['name'] = 'RE2'
    if change == 'time': item['plannedWhen'] = '2026-09-30T00:08:00+02:00'
    if change == 'direction': item['direction'] = 'Elsewhere'
    if change == 'duplicate': rows.append(deepcopy(item))
    if change == 'naive': item['plannedWhen'] = '2026-09-30T00:07:00'
    if change == 'invalid': rows = [None, {}, {'plannedWhen': False}]
    if change == 'primary_duplicate': board.journeys.append(board.journeys[0].model_copy(deep=True))
    original = board.model_copy(deep=True)
    enrich_platforms(board, rows, '8011160')
    assert board == original


def test_planned_only_and_ris_precedence(provider):
    board = asyncio.run(provider.board('s', 'departures', NOW))
    d = board.journeys[0]
    d.platform = d.scheduled_platform = None
    item = row()
    item['platform'] = None
    enrich_platforms(board, [item], '8011160')
    assert d.platform == d.scheduled_platform == '7'
    d.source = 'GTFS + DB RIS::Boards'
    d.platform = '12'
    enrich_platforms(board, [row()], '8011160')
    assert d.platform == '12'


@pytest.mark.parametrize('failure', [None, 'timeout', '503', 'stale', 'ambiguous', 'malformed'])
def test_fetch_cache_and_fallback(provider, monkeypatch, failure):
    calls = []
    async def get(self, url, params):
        calls.append((url, params))
        if failure == 'timeout': raise httpx.ReadTimeout('offline')
        payload = [{'id': '8011160', 'name': 'Berlin Hbf', 'type': 'stop'}]
        if failure == 'ambiguous': payload.append({'id': 'other', 'name': 'Berlin Hbf', 'type': 'stop'})
        if failure == 'ambiguous': payload[-1]['id'] = '8011111'
        if '/departures' in url: payload = {'departures': [row()]}
        if failure == 'malformed': payload = None
        return httpx.Response(503 if failure == '503' else 200, json=payload,
                              headers={'Age': '181' if failure == 'stale' else '0'}, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    combined = TransportRestProvider(provider)
    async def run():
        board = await combined.board('s', 'departures', NOW)
        await combined.board('s', 'departures', NOW)
        return board
    board = asyncio.run(run())
    assert board.journeys[0].platform == ('7' if failure else '9')
    assert len(calls) == (1 if failure else 2)
    if not failure:
        assert calls[1][1]['includeRelatedStations'] == 'false'


def test_factory_enable_and_static_bypass(provider, monkeypatch):
    from app.providers.factory import create_provider
    monkeypatch.setenv('DATABASE_PATH', str(provider.path))
    monkeypatch.setenv('TRANSPORT_REST_ENABLED', 'true')
    monkeypatch.setenv('DB_RIS_ENABLED', 'false')
    combined = create_provider()
    assert isinstance(combined, TransportRestProvider)
    async def unexpected(*args, **kwargs):
        raise AssertionError('Static board must not fetch platforms')
    monkeypatch.setattr(combined, 'fetch', unexpected)
    assert asyncio.run(combined.static_board('s', 'departures', NOW)).journeys[0].platform == '7'
