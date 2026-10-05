"""Read-only local audit. Run from repo root: PYTHONPATH=. .venv/bin/python docs/favorites-and-stops-2026-10-05/analyze.py

stdout is JSON. No network, cache writes, import or migration.
"""
import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
from zipfile import ZipFile

from app.database import connect
from app.providers.efa_mapping import MappingStore
from app.providers.gtfs_static import GTFSStaticProvider, BERLIN

path = Path('data/gtfs.sqlite')
provider = GTFSStaticProvider(path)
store = MappingStore(path)
result = {'examined_at_utc': datetime.now(timezone.utc).isoformat(),
          'database_signature': store.database_signature(), 'catalog_signature': store.signature,
          'groups': [], 'search': {}, 'boards_20261005_1200': []}
with connect(path) as db:
    result['metadata'] = dict(db.execute('SELECT key,value FROM metadata'))
    for sid in ('421683', '380494', '255810'):
        group = {'requested_id': sid, 'stops': [dict(r) for r in db.execute(
            'SELECT * FROM stops WHERE stop_id=? OR parent_station=?', (sid, sid))]}
        ids = [r['stop_id'] for r in group['stops']]
        marks = ','.join('?' for _ in ids)
        group['routes'] = [dict(r) for r in db.execute(f'''SELECT st.stop_id,
            r.route_id,r.route_short_name,r.route_type,a.agency_name,
            count(*) AS stop_events,count(DISTINCT t.trip_id) AS trips
            FROM stop_times st JOIN trips t USING(trip_id) JOIN routes r USING(route_id)
            JOIN agencies a USING(agency_id) WHERE st.stop_id IN ({marks})
            GROUP BY st.stop_id,r.route_id ORDER BY st.stop_id,r.route_id''', ids)]
        root = next(r for r in group['stops'] if r['stop_id'] == sid)
        group['efa_catalog_group'] = store.group(sid, root['stop_name'])
        if sid == '255810':
            group['trip_events'] = [dict(r) for r in db.execute('''SELECT t.*,st.*
                FROM trips t JOIN stop_times st USING(trip_id) WHERE st.stop_id='227540' ''')]
            service_ids = {r['service_id'] for r in group['trip_events']}
            group['calendars'] = [dict(r) for s in service_ids for r in db.execute('SELECT * FROM calendar WHERE service_id=?', (s,))]
            group['calendar_dates'] = [dict(r) for s in service_ids for r in db.execute('SELECT * FROM calendar_dates WHERE service_id=?', (s,))]
        result['groups'].append(group)
    result['historical_ids_now'] = [dict(r) for r in db.execute(
        "SELECT * FROM stops WHERE stop_id IN ('203787','439072','437706','454966','414153','562185')")]
with ZipFile('latest.zip') as archive:
    wanted = {s['stop_id'] for g in result['groups'] for s in g['stops']}
    raw_stops = {r['stop_id']: r for r in csv.DictReader(io.TextIOWrapper(archive.open('stops.txt'))) if r['stop_id'] in wanted}
    for group in result['groups']:
        group['zip_stops'] = [raw_stops[s['stop_id']] for s in group['stops']]
        for s in group['stops']:
            raw = raw_stops[s['stop_id']]
            assert (s['stop_name'], s['parent_station']) == (raw['stop_name'], raw['parent_station']), 'ZIP/SQLite conflict'
for sid in ('203787', '563662', '155197', '421683', '380494', '255810'):
    station, ds = provider.scheduled(sid, 'departures', datetime(2026,10,5,12,tzinfo=BERLIN), 0)
    result['boards_20261005_1200'].append({'stop': station.model_dump(), 'journeys': len(ds),
                                       'lines_modes': sorted({(d.line,d.mode) for d in ds})})
for q in ('Bad Harzburg', 'Bad Harzburg Bahnhof', 'Bad Harzburg Parkdeck', 'Goslar Bahnhof', 'Braunschweig Hauptbahnhof'):
    result['search'][q] = [s.model_dump() for s in provider.search(q)]
print(json.dumps(result, ensure_ascii=False, indent=2))
