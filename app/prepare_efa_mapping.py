"""Build a VRB stop catalog locally; no network or GTFS schema changes."""
import argparse
from pathlib import Path
from zipfile import ZipFile
from .database import connect
from .import_gtfs import rows
from .providers.efa_mapping import atomic_json


def prepare(database, archive, output, agency_names):
    with connect(database) as db:
        agencies = [r[0] for r in db.execute('SELECT agency_id FROM agencies WHERE agency_name IN (' + ','.join('?' for _ in agency_names) + ')', agency_names)]
        if not agencies:
            raise ValueError('Keine passende VRB-Agency im GTFS')
        used = {r[0] for r in db.execute('''SELECT DISTINCT COALESCE(NULLIF(s.parent_station,''),s.stop_id)
            FROM trips t JOIN routes r ON r.route_id=t.route_id
            JOIN stop_times st ON st.trip_id=t.trip_id JOIN stops s ON s.stop_id=st.stop_id
            WHERE r.agency_id IN (''' + ','.join('?' for _ in agencies) + ')', agencies)}
        stops = {r['stop_id']:dict(r) for r in db.execute('SELECT stop_id,stop_name,parent_station FROM stops')}
    coords = {}
    with ZipFile(archive) as z:
        for r in rows(z, 'stops.txt', True):
            if r['stop_id'] in used and r['stop_name'] == stops[r['stop_id']]['stop_name']:
                try:
                    coord = [float(r['stop_lat']), float(r['stop_lon'])]
                    if -90 <= coord[0] <= 90 and -180 <= coord[1] <= 180:
                        coords[r['stop_id']] = coord
                except (KeyError, ValueError):
                    pass
    groups = {key:dict(id=key, name=stops[key]['stop_name'], coord=coords[key], stops=[key])
              for key in sorted(used & coords.keys())}
    for key, stop in stops.items():
        if stop['parent_station'] in groups:
            groups[stop['parent_station']]['stops'].append(key)
    stat = database.stat()
    atomic_json(output, dict(database_signature=[stat.st_size,stat.st_mtime_ns],
                             archive=archive.name, agency_names=agency_names, groups=groups))
    return dict(groups=len(groups), without_coordinates=len(used)-len(groups))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('archive', type=Path)
    p.add_argument('--database', type=Path, default=Path('data/gtfs.sqlite'))
    p.add_argument('--output', type=Path, default=Path('data/vrb-stops.json'))
    p.add_argument('--agency', action='append', default=None)
    a=p.parse_args()
    print(prepare(a.database,a.archive,a.output,a.agency or ['Tarifverb Region Braunschweig','Regionalbus Braunschweig']))


if __name__=='__main__':
    main()
