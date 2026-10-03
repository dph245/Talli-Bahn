import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.providers.demo import DemoProvider
from app.providers.supplemented import SupplementedProvider
from app.providers.transport_rest import TransportRestProvider
from test_transit import provider


def catalog(provider, groups):
    stat = provider.path.stat()
    path = provider.path.parent / 'vrb-stops.json'
    path.write_text(json.dumps(dict(database_signature=[stat.st_size, stat.st_mtime_ns],
                                   groups={g['id']: g for g in groups})))
    return path


def group(id, lat, lon=10):
    return dict(id=id, name=f'Stop {id}', coord=[lat, lon], stops=[id, id+'-child'])


def test_nearby_sorted_limited_and_grouped_without_efa(provider):
    assert provider.efa is None
    catalog(provider, [group(str(i), 52+i*.001) for i in reversed(range(8))]
            + [group('far',53), dict(id='invalid', name='Invalid', coord=[None,10])])
    hits = provider.nearby(52,10)
    assert [s.id for s in hits] == ['0','1','2','3','4']
    assert hits[0].distance_m == 0
    assert hits[1].distance_m == 111
    assert provider.nearby(0,0) == []


def test_nearby_radius_uses_unrounded_distance(provider):
    # Haversine north/south arc, one just inside and one just outside 2 km.
    import math
    catalog(provider, [group('inside',52+math.degrees(1999.9/6371000)),
                       group('outside',52+math.degrees(2000.1/6371000))])
    assert [s.id for s in provider.nearby(52,10)] == ['inside']


@pytest.mark.parametrize('failure', ['missing','invalid_json','stale','removed_after_read'])
def test_bad_catalog_does_not_break_name_search(provider, failure):
    path = provider.path.parent/'vrb-stops.json'
    if failure == 'invalid_json': path.write_text('{')
    if failure == 'stale': path.write_text(json.dumps(dict(database_signature=[0,0],groups={})))
    if failure == 'removed_after_read':
        catalog(provider,[group('s',52)])
        assert provider.nearby(52,10)
        path.unlink()
    with TestClient(create_app(provider)) as client:
        response = client.post('/api/stops/nearby',json=dict(lat=52,lon=10))
        assert response.status_code == 503
        assert client.get('/api/stops?q=Berlin').json()[0]['id'] == 's'


def test_replaced_catalog_and_database_invalidate_cached_results(provider):
    path = catalog(provider,[group('s',52)])
    assert provider.nearby(52,10)[0].id == 's'
    catalog(provider,[group('replacement',52)])
    assert provider.nearby(52,10)[0].id == 'replacement'
    import os
    stat=provider.path.stat()
    os.utime(provider.path,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000))
    with pytest.raises(ValueError): provider.nearby(52,10)


def test_nearby_api_and_wrappers(provider):
    catalog(provider,[group('s',52)])
    wrapped = TransportRestProvider(SupplementedProvider(provider,None,{}))
    with TestClient(create_app(wrapped)) as client:
        response=client.post('/api/stops/nearby',json=dict(lat=52,lon=10))
        assert response.status_code == 200
        assert response.json() == [dict(id='s',name='Stop s',distance_m=0)]
        assert response.headers['cache-control']=='no-store'
        assert client.get('/api/stops/nearby').status_code==405
        for data in [dict(lat=91,lon=10),dict(lat=52,lon=-181),{},dict(lat='NaN',lon=10)]:
            assert client.post('/api/stops/nearby',json=data).status_code==422


def test_demo_has_no_fake_nearby_results():
    with TestClient(create_app(DemoProvider())) as client:
        assert client.post('/api/stops/nearby',json=dict(lat=52,lon=10)).status_code==503
