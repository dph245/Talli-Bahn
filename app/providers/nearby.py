"""Local proximity search over the import-bound VRB catalog; no EFA discovery."""
import json
import os
from pathlib import Path

from ..models import NearbyStop
from .efa_mapping import distance, valid_coord


class NearbyCatalog:
    def __init__(self, database):
        self.database = database
        self.path = Path(os.getenv('VRB_EFA_CATALOG_PATH', str(database.parent / 'vrb-stops.json')))
        self.cached = None

    def search(self, lat, lon):
        try:
            db = self.database.stat()
            stat = self.path.stat()
            signature = [db.st_size, db.st_mtime_ns]
            key = (tuple(signature), stat.st_size, stat.st_mtime_ns)
            cached = self.cached
            if cached is None or cached[0] != key:
                data = json.loads(self.path.read_text())
                if data['database_signature'] != signature:
                    raise ValueError('Catalog does not match GTFS')
                groups = tuple(g for g in data['groups'].values()
                               if isinstance(g, dict) and isinstance(g.get('id'), str)
                               and isinstance(g.get('name'), str) and valid_coord(g.get('coord')))
                cached = (key, groups)
                self.cached = cached
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise ValueError('Umgebungssuche nicht verfügbar') from error
        ranked = sorted((distance([lat, lon], g['coord']), g['id'], g['name']) for g in cached[1])
        return [NearbyStop(id=stop_id, name=name, distance_m=round(meters))
                for meters, stop_id, name in ranked if meters <= 2000][:5]
