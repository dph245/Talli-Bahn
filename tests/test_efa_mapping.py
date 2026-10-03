import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.providers.efa_mapping import MappingStore, classify, atomic_json
from app.providers.vrb_efa import EFAFeed, Response, match_predictions
from test_transit import provider

ROOT = Path(__file__).resolve().parents[1] / 'docs' / 'efa-discovery'
GROUP = dict(id='46207', name='Wolfenbüttel, Campestraße', coord=[52.159, 10.54], stops=['46207'])


def payload(name=None, coord=None, city='Wolfenbüttel', dhid='de:03158:460'):
    return {'locations': [dict(id=dhid, type='stop', name=name or GROUP['name'],
                              parent={'name':city}, coord=coord or GROUP['coord'])]}


@pytest.mark.parametrize('variant,status', [('exact','UNIQUE'), ('abbreviation','AMBIGUOUS'),
    ('far','AMBIGUOUS'), ('wrong_city','NONE'), ('multiple','AMBIGUOUS'), ('missing_coords','AMBIGUOUS')])
def test_strict_stop_mapping(variant,status):
    data=payload()
    if variant=='abbreviation':data=payload(name='Wolfenbüttel, Campestr.')
    if variant=='far':data=payload(coord=[53,10])
    if variant=='wrong_city':data=payload(city='Braunschweig')
    if variant=='multiple':data['locations'] += payload(dhid='de:03158:461')['locations']
    if variant=='missing_coords':data['locations'][0].pop('coord')
    assert classify(GROUP,data)['status']==status


def test_persistent_results_and_catalog_invalidation(provider,tmp_path):
    catalog=tmp_path/'catalog.json'; cache=tmp_path/'cache'
    stat=provider.path.stat()
    group=dict(GROUP,id='s',stops=['s','p'])
    atomic_json(catalog,dict(database_signature=[stat.st_size,stat.st_mtime_ns],groups={'s':group}))
    first=MappingStore(provider.path,catalog,cache)
    assert first.group('p','any name')['id']=='s'
    first.save(group,payload(name='Wolfenbüttel, Campestr.'))
    second=MappingStore(provider.path,catalog,cache)
    assert second.read(group)['status']=='AMBIGUOUS'
    assert second.group('not-vrb','Berlin') is None
    catalog.write_text(catalog.read_text().replace(str(stat.st_mtime_ns),'0'))
    assert MappingStore(provider.path,catalog,cache).group('s',GROUP['name']) is None


def test_discovery_then_group_board_uses_one_shared_rate_limiter(provider,tmp_path,monkeypatch):
    from app.providers import vrb_efa
    group=dict(id='s',name='Berlin Hbf',coord=[52,10],stops=['s','p'])
    catalog=tmp_path/'catalog.json'; stat=provider.path.stat()
    atomic_json(catalog,dict(database_signature=[stat.st_size,stat.st_mtime_ns],groups={'s':group}))
    feed=EFAFeed(provider);feed.mapping=MappingStore(provider.path,catalog,tmp_path/'cache')
    calls=[]; sleeps=[]
    async def sleep(delay):sleeps.append(delay)
    monkeypatch.setattr(vrb_efa.asyncio,'sleep',sleep)
    # Use a comma-locality name in the synthetic DB, as required for verified discovery.
    import sqlite3
    with sqlite3.connect(provider.path) as db:db.execute("UPDATE stops SET stop_name='Berlin, Hbf' WHERE stop_id IN ('s','p')")
    group['name']='Berlin, Hbf'
    async def get(client,url,params):
        calls.append(params)
        data={'locations':[dict(id='de:03158:460',name=group['name'],type='stop',parent={'name':'Berlin'},coord=[52,10])]} if 'name_sf' in params else {'stopEvents':[]}
        return httpx.Response(200,json=data,request=httpx.Request('GET',url))
    monkeypatch.setattr(httpx.AsyncClient,'get',get)
    async def run():
        await feed._refresh(group)
        assert len(calls)==2 and calls[1]['name_dm']=='de:03158:460'
        assert sleeps[1]>1
        await feed._refresh(group)
        assert len(calls)==3 and 'name_dm' in calls[2]  # cached discovery, no Stopfinder
    asyncio.run(run())


@pytest.mark.parametrize('label,dhid', [('campestrasse','de:03158:460'),('bahnhof','de:03158:458')])
def test_recorded_group_events_validate_parent_identity(label,dhid):
    from test_transit import journey
    response=Response.model_validate_json((ROOT/(label+'-board.json')).read_bytes())
    event=next(e for e in response.stopEvents if e.departureTimeEstimated is not None)
    d=journey();d.line=event.transportation.number;d.scheduled=event.departureTimePlanned
    assert event.location.parent.id==dhid
    assert match_predictions([event],[d],dhid,event.location.name)
    assert not match_predictions([event],[d],'de:03158:999999',event.location.name)


def test_catalog_builds_groups_from_routes_not_city_prefix(provider,tmp_path):
    from zipfile import ZipFile
    from app.prepare_efa_mapping import prepare
    archive=tmp_path/'source.zip'; catalog=tmp_path/'catalog.json'
    with ZipFile(archive,'w') as z:
        z.writestr('stops.txt','stop_id,stop_name,stop_lat,stop_lon,parent_station\ns,Berlin Hbf,52,10,\np,Berlin Hbf,52,10,s\n')
    result=prepare(provider.path,archive,catalog,['Test'])
    assert result['groups']==1
    group=json.loads(catalog.read_text())['groups']['s']
    assert set(group['stops'])=={'s','p'} and group['coord']==[52,10]
    assert group['children'] == [dict(id='p', name='Berlin Hbf', coord=[52,10], parent_station='s')]


def test_observed_platform_ids_survive_restart(provider,tmp_path):
    store=MappingStore(provider.path,tmp_path/'absent.json',tmp_path/'cache')
    data=json.loads((ROOT/'validation.json').read_text())['campestrasse']
    group=data['gtfs_group']; result=store.read(group)
    events=Response.model_validate_json((ROOT/'campestrasse-board.json').read_bytes()).stopEvents
    result['platforms']=[]
    store.record_platforms(group,result,events)
    reloaded=MappingStore(provider.path,tmp_path/'absent.json',tmp_path/'cache').read(group)
    assert reloaded['platforms']==['de:03158:460:1:A','de:03158:460:1:B']


def alias_payload():
    # Shape observed in EFA: the same DHID has a qualified alternative name.
    group = dict(id='395508', name='SZ-Lebenstedt, Bahnhof',
                 coord=[52.152466, 10.33183], stops=['395508'])
    alias = dict(id='de:03102:3137', type='stop', name=group['name'],
                 parent={'name': 'Lebenstedt'}, coord=[52.152464, 10.33183])
    location = dict(alias, name='Lebenstedt, Bahnhof', assignedStops=[alias])
    return group, {'locations': [location]}


@pytest.mark.parametrize('variant', ['observed', 'unrelated_prefix', 'duplicate'])
def test_same_dhid_qualified_alias_is_unique(variant):
    from copy import deepcopy
    group, data = alias_payload()
    if variant == 'unrelated_prefix':
        group['name'] = 'Anderer Ortsname, Bahnhof'
        data['locations'][0]['assignedStops'][0]['name'] = group['name']
    if variant == 'duplicate':
        data['locations'] += deepcopy(data['locations'])
    result = classify(group, data)
    assert result['status'] == 'UNIQUE'
    assert result['dhid'] == 'de:03102:3137'
    assert result['name'] == group['name']
    assert result['distance_m'] < 1


@pytest.mark.parametrize('variant', ['foreign_dhid', 'platform_dhid', 'wrong_type',
    'missing_coords', 'far', 'invalid_coords', 'wrong_parent', 'missing_parent',
    'abbreviated_name', 'missing_aliases', 'malformed_aliases', 'malformed_alias'])
def test_alias_does_not_bypass_identity_or_distance(variant):
    group, data = alias_payload()
    loc = data['locations'][0]
    alias = loc['assignedStops'][0]
    if variant == 'foreign_dhid': alias['id'] = 'de:03102:9999'
    if variant == 'platform_dhid': alias['id'] += ':1:A'
    if variant == 'wrong_type': alias['type'] = 'locality'
    if variant == 'missing_coords': alias.pop('coord')
    if variant == 'far': alias['coord'] = [53, 10]
    if variant == 'invalid_coords': alias['coord'] = [float('nan'), 10]
    if variant == 'wrong_parent': alias['parent'] = {'name': 'Anderer Ort'}
    if variant == 'missing_parent': alias.pop('parent')
    if variant == 'abbreviated_name': alias['name'] = 'SZ-Lebenstedt, Bhf.'
    if variant == 'missing_aliases': loc.pop('assignedStops')
    if variant == 'malformed_aliases': loc['assignedStops'] = {}
    if variant == 'malformed_alias': loc['assignedStops'] = [None, 'invalid']
    assert classify(group, data)['status'] != 'UNIQUE'


def test_two_matching_alias_dhids_remain_ambiguous():
    from copy import deepcopy
    group, data = alias_payload()
    second = deepcopy(data['locations'][0])
    second['id'] = second['assignedStops'][0]['id'] = 'de:03102:9999'
    data['locations'].append(second)
    result = classify(group, data)
    assert result['status'] == 'AMBIGUOUS'
    assert {c['dhid'] for c in result['candidates']} == {'de:03102:3137', 'de:03102:9999'}


def test_cached_negative_result_is_reclassified_without_rewriting(provider, tmp_path):
    group, data = alias_payload()
    store = MappingStore(provider.path, tmp_path/'absent.json', tmp_path/'cache')
    path = store.cache / (store.key(group) + '.json')
    atomic_json(path, dict(group=group, response=data, result=dict(status='NONE', candidates=[])))
    before = path.read_bytes()
    assert store.read(group)['status'] == 'UNIQUE'
    assert path.read_bytes() == before


def test_alias_mapping_reaches_departure_monitor(provider, tmp_path, monkeypatch):
    import sqlite3
    group, data = alias_payload()
    group.update(id='s', stops=['s', 'p'])
    with sqlite3.connect(provider.path) as db:
        db.execute('UPDATE stops SET stop_name=? WHERE stop_id IN (?, ?)', (group['name'], 's', 'p'))
    feed = EFAFeed(provider)
    feed.mapping = MappingStore(provider.path, tmp_path/'absent.json', tmp_path/'cache')
    feed.mapping.save(group, data)
    calls = []
    async def get(client, url, params):
        calls.append(params)
        return httpx.Response(200, json={'stopEvents': []}, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    asyncio.run(feed._refresh(group))
    assert len(calls) == 1
    assert calls[0]['name_dm'] == 'de:03102:3137'
    assert feed.cached['s'][1] == {}


def harzburg_mapping():
    # Names, IDs and coordinates observed in GTFS and the live EFA Stopfinder.
    child = dict(id='437706', name='Bad Harzburg, Bahnhof',
                 coord=[51.887554, 10.555276], parent_station='439072')
    group = dict(id='439072', name='Bad Harzburg, Bahnhof Parkdeck',
                 coord=[51.88719, 10.556399], stops=['439072', '454966', '437706', '30341'], children=[child])
    primary = dict(id='de:03153:4948', type='stop', name=group['name'],
                   parent={'name': 'Bad Harzburg'}, coord=[51.887192, 10.556399])
    station = dict(id='de:03153:4946', type='stop', name=child['name'],
                   parent={'name': 'Bad Harzburg'}, coord=[51.887869, 10.554953])
    return group, {'locations': [dict(primary, assignedStops=[primary, station])]}


def test_assigned_station_requires_concrete_child():
    group, data = harzburg_mapping()
    result = classify(group, data)
    assert result['dhid'] == 'de:03153:4948'
    assert result['assigned'][0]['dhid'] == 'de:03153:4946'
    assert result['assigned'][0]['gtfs_stop_id'] == '437706'
    assert result['assigned'][0]['distance_m'] < 100
    group.pop('children')  # Old catalogs remain usable, but grant no extra DHIDs.
    assert 'assigned' not in classify(group, data)


@pytest.mark.parametrize('case', ['foreign_group', 'not_a_member', 'missing_coord', 'far_child',
    'far_efa', 'wrong_city', 'wrong_name', 'platform', 'two_dhids', 'two_children',
    'unrelated_location', 'malformed_children'])
def test_assigned_station_rejects_unproven_or_ambiguous_mapping(case):
    from copy import deepcopy
    group, data = harzburg_mapping()
    child = group['children'][0]
    station = data['locations'][0]['assignedStops'][1]
    if case == 'foreign_group': child['parent_station'] = 'other'
    if case == 'not_a_member': group['stops'].remove(child['id'])
    if case == 'missing_coord': child.pop('coord')
    if case == 'far_child': child['coord'] = [53, 10]
    if case == 'far_efa': station['coord'] = [53, 10]
    if case == 'wrong_city': station['parent']['name'] = 'Other'
    if case == 'wrong_name': station['name'] = 'Bad Harzburg, Bhf.'
    if case == 'platform': station['id'] += ':1:922'
    if case == 'two_dhids':
        data['locations'][0]['assignedStops'].append(dict(station, id='de:03153:9999'))
    if case == 'two_children':
        group['children'].append(dict(child, id='454966'))
    if case == 'unrelated_location':
        unrelated = deepcopy(data['locations'][0])
        unrelated.update(id='de:03153:9999', name='Other')
        data['locations'][0].pop('assignedStops')
        data['locations'].append(unrelated)
    if case == 'malformed_children': group['children'] = None
    result = classify(group, data)
    assert result['status'] == 'UNIQUE'  # Primary mapping is unaffected.
    assert 'assigned' not in result


def test_439072_rb42_refresh_uses_verified_station_without_extra_request(provider, tmp_path, monkeypatch):
    import sqlite3
    from types import SimpleNamespace
    from datetime import timedelta
    from app.providers import vrb_efa
    from test_transit import NOW
    group, data = harzburg_mapping()
    with sqlite3.connect(provider.path) as db:
        db.execute("UPDATE stops SET stop_id='439072', stop_name=? WHERE stop_id='s'", (group['name'],))
        db.execute("UPDATE stops SET stop_id='454966', parent_station='439072', stop_name='Bad Harzburg' WHERE stop_id='p'")
        db.execute("UPDATE stop_times SET stop_id='454966' WHERE stop_id='p'")
        db.execute("UPDATE routes SET route_short_name='RB42'")
        db.execute("INSERT INTO stops VALUES ('437706','Bad Harzburg, Bahnhof','bad harzburg, bahnhof','439072','',0)")
    monkeypatch.setattr(vrb_efa, 'datetime', SimpleNamespace(now=lambda tz: NOW))
    feed = EFAFeed(provider)
    feed.mapping = MappingStore(provider.path, tmp_path/'absent.json', tmp_path/'cache')
    feed.mapping.save(group, data)
    # Restart path must rebuild additional identities from the raw response.
    feed.mapping = MappingStore(provider.path, tmp_path/'absent.json', tmp_path/'cache')
    _, departures = provider.scheduled('439072', 'departures', NOW)
    train = next(d for d in departures if d.trip_id == 't')
    event = dict(location=dict(id='de:03153:4946:1:922', name='Bad Harzburg, Bahnhof',
                              parent={'id':'de:03153:4946'}, properties={'platformName':'2'}),
                 transportation={'number':'RB42'}, departureTimePlanned=train.scheduled.isoformat(),
                 departureTimeEstimated=(train.scheduled + timedelta(minutes=2)).isoformat(),
                 isRealtimeControlled=True)
    calls = []
    async def get(client, url, params):
        calls.append(params)
        return httpx.Response(200, json={'stopEvents':[event]}, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    asyncio.run(feed._refresh(group))
    assert len(calls) == 1 and calls[0]['name_dm'] == 'de:03153:4948'
    assert feed.cached['439072'][1][train.id] == vrb_efa.Prediction(train.scheduled + timedelta(minutes=2), '2')
    parsed = vrb_efa.Event.model_validate(event)
    assert not match_predictions([parsed], departures, 'de:03153:4948', group['name'])
    assigned = feed.mapping.read(group)['assigned']
    parsed.location.parent.id = 'de:03153:9999'
    assert not match_predictions([parsed], departures, 'de:03153:4948', group['name'], assigned)


def test_primary_and_assigned_predictions_keep_conflict_rejection():
    from test_transit import journey, NOW
    from test_vrb_efa import event
    from datetime import timedelta
    group, data = harzburg_mapping()
    assigned = classify(group, data)['assigned']
    primary = event(location={'id':'de:03153:4948', 'name':group['name']})
    extra = event(location={'id':'de:03153:4946', 'name':'Bad Harzburg, Bahnhof'},
                  departureTimeEstimated=NOW + timedelta(minutes=3))
    assert match_predictions([primary, extra], [journey()], 'de:03153:4948', group['name'], assigned) == {}


def test_shared_discovery_cache_does_not_share_child_authorization(provider, tmp_path):
    group, data = harzburg_mapping()
    store = MappingStore(provider.path, tmp_path/'absent.json', tmp_path/'cache')
    store.save(group, data)
    assert store.read(group)['assigned']
    other = dict(group, id='different-group', stops=['different-group'])
    assert store.key(other) == store.key(group)
    assert 'assigned' not in store.read(other)
