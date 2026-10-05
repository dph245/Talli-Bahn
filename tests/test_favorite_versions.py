"""Import-scoped station references must never survive ID reuse silently."""
import sqlite3
from datetime import datetime
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from app.import_gtfs import import_feed
from app.main import create_app
from app.providers.gtfs_static import GTFSStaticProvider
from test_transit import provider, write_feed


def test_version_restart_catalog_rebuild_and_reimport(provider, tmp_path):
    version = provider.dataset_version()
    assert GTFSStaticProvider(provider.path).dataset_version() == version
    (provider.path.parent / 'vrb-stops.json').write_text('{}')
    assert provider.dataset_version() == version
    archive = tmp_path / 'same.zip'
    write_feed(archive)
    import_feed(archive, provider.path)
    assert provider.dataset_version() != version  # Even same names are not migrated.


def test_old_import_gets_stable_read_only_version(provider, tmp_path):
    with sqlite3.connect(provider.path) as db:
        db.execute("DELETE FROM metadata WHERE key='import_id'")
    before = provider.path.read_bytes()
    version = provider.dataset_version()
    assert version.startswith('gtfs:legacy:')
    assert GTFSStaticProvider(provider.path).dataset_version() == version
    assert provider.path.read_bytes() == before
    replacement = tmp_path / 'replacement.sqlite'
    replacement.write_bytes(before)
    replacement.replace(provider.path)
    assert provider.dataset_version() != version


def test_failed_import_preserves_favorite_version(provider, tmp_path):
    version = provider.dataset_version()
    archive = tmp_path / 'bad.zip'
    write_feed(archive, {'frequencies.txt': 'trip_id,start_time,end_time,headway_secs\nt,08:00:00,10:00:00,600\n'})
    with pytest.raises(ValueError):
        import_feed(archive, provider.path)
    assert provider.dataset_version() == version


def test_api_rejects_reused_id_before_board_lookup(provider, tmp_path, monkeypatch):
    with TestClient(create_app(provider)) as client:
        saved = client.get('/api/stops?q=Berlin').json()[0]
        assert saved['dataset_version'] == client.get('/api/dataset').json()['version']
        archive = tmp_path / 'new.zip'
        write_feed(archive, {'stops.txt': 'stop_id,stop_name,parent_station,platform_code,location_type\ns,Anderer Ort,,,1\np,Anderer Ort,s,7,0\no,Potsdam,,,0\nd,Ahrensfelde,,,0\n'})
        import_feed(archive, provider.path)
        async def forbidden(*args):
            pytest.fail('Old favorite reached board lookup')
        monkeypatch.setattr(provider, 'board', forbidden)
        response = client.get('/api/board', params={'stop_id': saved['id'], 'dataset_version': saved['dataset_version']})
        assert response.status_code == 409
        assert response.headers['cache-control'] == 'no-store'
        assert client.get('/api/stops?q=Anderer').json()[0]['dataset_version'] != saved['dataset_version']


def test_current_version_can_have_empty_board(provider, monkeypatch):
    monkeypatch.setattr("app.main.datetime", SimpleNamespace(now=lambda zone: datetime(2026, 10, 5, tzinfo=zone)))
    with TestClient(create_app(provider)) as client:
        version = provider.dataset_version()
        response = client.get('/api/board', params={'stop_id': 's', 'dataset_version': version, 'realtime': False})
        assert response.status_code == 200
        assert response.json()['stop']['dataset_version'] == version
        assert response.json()['journeys'] == []  # Fixture service dates are past.
        assert client.get('/api/board', params={'stop_id': 'missing', 'dataset_version': version}).status_code == 404
        assert client.get('/api/board', params={'stop_id': 's', 'dataset_version': 'demo:v1'}).status_code == 409


def test_import_during_board_or_search_is_rejected(provider, tmp_path, monkeypatch):
    archive = tmp_path / 'next.zip'
    write_feed(archive)
    original_board = provider.static_board
    async def changing_board(*args):
        result = await original_board(*args)
        import_feed(archive, provider.path)
        return result
    monkeypatch.setattr(provider, 'static_board', changing_board)
    with TestClient(create_app(provider)) as client:
        assert client.get('/api/board', params={'stop_id': 's', 'realtime': False}).status_code == 409
        original_search = provider.search
        def changing_search(*args):
            result = original_search(*args)
            import_feed(archive, provider.path)
            return result
        monkeypatch.setattr(provider, 'search', changing_search)
        assert client.get('/api/stops?q=Berlin').status_code == 409
