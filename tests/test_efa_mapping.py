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
        z.writestr('stops.txt','stop_id,stop_name,stop_lat,stop_lon\ns,Berlin Hbf,52,10\np,Berlin Hbf,52,10\n')
    result=prepare(provider.path,archive,catalog,['Test'])
    assert result['groups']==1
    group=json.loads(catalog.read_text())['groups']['s']
    assert set(group['stops'])=={'s','p'} and group['coord']==[52,10]


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
