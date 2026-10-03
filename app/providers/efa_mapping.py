"""Persistent, strict GTFS-group → EFA-stop mapping, independent of board logic."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import unicodedata

STOPFINDER = 'https://bsvg.efa.de/vrbstd_relaunch/XML_STOPFINDER_REQUEST'


def normalized(value):
    return re.sub(r'\s*,\s*', ',', ' '.join(unicodedata.normalize('NFC', value).casefold().split()))


def distance(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(h)))


def classify(group, payload, *, include_assigned=True):
    if not isinstance(payload, dict):
        raise ValueError('Stopfinder-Antwort ist kein Objekt')
    locations = payload.get('locations')
    if not isinstance(locations, list):
        raise ValueError('Stopfinder locations fehlt')
    exact, plausible = {}, {}
    # Require an explicit locality in the GTFS name; do not guess city prefixes.
    locality, separator, _ = group['name'].partition(',')
    for loc in locations:
        if not isinstance(loc, dict) or loc.get('type') != 'stop':
            continue
        dhid = loc.get('id', '')
        if not isinstance(dhid, str) or not re.fullmatch(r'de:\d+:\d+', dhid):
            continue
        # Only explicit names of this very stop are aliases, never neighbouring
        # assigned stops or platforms. Keep each alias's coordinates and locality
        # evidence together rather than mixing fields from different records.
        assigned = loc.get('assignedStops')
        variants = [loc]
        if isinstance(assigned, list):
            variants += [a for a in assigned if isinstance(a, dict)
                         and a.get('type') == 'stop' and a.get('id') == dhid]
        for variant in variants:
            item, same_city, same_name = classify_name(group, locality, separator, loc, variant)
            if item is None:
                continue
            meters = item['distance_m']
            if same_city and (same_name or meters is not None and meters <= 100):
                plausible[dhid] = item
            if same_city and same_name and meters is not None and meters <= 100:
                exact[dhid] = item
    if len(exact) == 1:
        result = dict(status='UNIQUE', **next(iter(exact.values())))
        if include_assigned:
            assigned = classify_assigned(group, locations, result['dhid'])
            if assigned:
                result['assigned'] = assigned
        return result
    return dict(status='AMBIGUOUS' if plausible else 'NONE', candidates=list(plausible.values()))


def classify_assigned(group, locations, primary_dhid):
    # Only associations explicitly attached to the verified primary stop.
    candidates = []
    for loc in locations:
        if not isinstance(loc, dict) or loc.get('id') != primary_dhid or loc.get('type') != 'stop':
            continue
        candidates.append(loc)
        assigned = loc.get('assignedStops')
        if isinstance(assigned, list):
            candidates.extend(a for a in assigned if isinstance(a, dict))
    # Match against all candidates together, so two DHIDs for one child cannot
    # become two independent UNIQUE results. Count the primary DHID as well.
    matches = {}
    children = group.get('children', [])
    if not isinstance(children, list):
        return []
    for child in children:
        if (not isinstance(child, dict) or child.get('parent_station') != group['id']
                or child.get('id') == group['id'] or child.get('id') not in group['stops']
                or not isinstance(child.get('name'), str) or not valid_coord(child.get('coord'))):
            continue
        result = classify(child, {'locations': candidates}, include_assigned=False)
        if result['status'] == 'UNIQUE' and result['dhid'] != primary_dhid:
            matches.setdefault(result['dhid'], {})[child['id']] = dict(result, gtfs_stop_id=child['id'])
    return [next(iter(children.values())) for children in matches.values() if len(children) == 1]


def valid_coord(coord):
    return (isinstance(coord, list) and len(coord) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in coord)
            and -90 <= coord[0] <= 90 and -180 <= coord[1] <= 180)


def classify_name(group, locality, separator, location, variant):
    name = variant.get('name', '')
    parent = variant.get('parent') or {}
    coord = variant.get('coord')
    if not isinstance(name, str) or not isinstance(parent, dict):
        return None, False, False
    parent_name = parent.get('name')
    if not isinstance(parent_name, str):
        return None, False, False
    places = {normalized(parent_name)}
    if variant is not location:
        canonical_parent = location.get('parent') or {}
        alias_place, comma, stop_name = name.partition(',')
        # A qualified same-DHID alias explicitly supplies an alternative
        # locality spelling, provided EFA links it to the same parent place.
        if (isinstance(canonical_parent, dict) and parent_name.strip()
                and isinstance(canonical_parent.get('name'), str)
                and normalized(canonical_parent['name']) == normalized(parent_name)
                and comma and alias_place.strip() and stop_name.strip()):
            places.add(normalized(alias_place))
    meters = None
    if valid_coord(coord):
        meters = distance(group['coord'], coord)
    same_city = bool(separator) and normalized(locality) in places
    same_name = normalized(name) == normalized(group['name'])
    item = dict(dhid=location['id'], name=name, distance_m=meters, platforms=[])
    return item, same_city, same_name


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


class MappingStore:
    def __init__(self, database, catalog=None, cache=None):
        self.database = database
        self.cache = Path(cache or os.getenv('VRB_EFA_CACHE_PATH', str(database.parent / 'efa-cache')))
        catalog = Path(catalog or os.getenv('VRB_EFA_CATALOG_PATH', str(database.parent / 'vrb-stops.json')))
        self.groups = {}
        self.aliases = {}
        self.loaded = {}
        self.signature = None
        if catalog.exists():
            data = json.loads(catalog.read_text())
            self.signature = data['database_signature']
            if self.signature == self.database_signature():
                self.groups = data['groups']
                self.aliases = {stop: key for key, group in self.groups.items() for stop in group['stops']}
        # Previously verified observations remain data, not stop-specific business rules.
        self.seeds = json.loads(Path(__file__).with_name('efa_seed.json').read_text())

    def database_signature(self):
        stat = self.database.stat()
        return [stat.st_size, stat.st_mtime_ns]

    def group(self, stop_id, name):
        if self.signature is not None and self.signature != self.database_signature():
            return None  # Rebuild the catalog after replacing GTFS; never reuse old IDs.
        group_id = self.aliases.get(stop_id)
        if group_id:
            return self.groups[group_id]
        seed = next((s for s in self.seeds if s['name'] == name), None)
        if seed:
            return dict(id=stop_id, name=name, coord=seed['coord'], stops=[stop_id])
        return None

    def key(self, group):
        return hashlib.sha256(json.dumps([group['name'], group['coord']], ensure_ascii=False).encode()).hexdigest()

    def read(self, group):
        key = self.key(group)
        # Raw discovery is shared by name/coordinates; verified child membership
        # belongs to the exact current GTFS group, not to that shared file key.
        loaded_key = (key, json.dumps(group, sort_keys=True))
        if loaded_key in self.loaded:
            return self.loaded[loaded_key]
        path = self.cache / (key + '.json')
        if path.exists():
            data = json.loads(path.read_text())
            result = classify(group, data['response'])
            self.load_platforms(group, result)
            self.loaded[loaded_key] = result
            return result
        seed = next((s for s in self.seeds if normalized(s['name']) == normalized(group['name'])
                     and distance(s['coord'], group['coord']) <= 100), None)
        if seed:
            result = dict(status='UNIQUE', dhid=seed['dhid'], name=seed['name'], platforms=list(seed['platforms']))
            self.load_platforms(group, result)
            self.loaded[loaded_key] = result
            return result
        return None

    def save(self, group, payload):
        result = classify(group, payload)
        atomic_json(self.cache / (self.key(group) + '.json'), dict(group=group, response=payload, result=result))
        self.loaded[(self.key(group), json.dumps(group, sort_keys=True))] = result
        return result

    def load_platforms(self, group, result):
        path = self.cache / (self.key(group) + '.platforms.json')
        if result['status'] == 'UNIQUE' and path.exists():
            data = json.loads(path.read_text())
            if data.get('dhid') == result['dhid']:
                result['platforms'] = data['platforms']

    def record_platforms(self, group, result, events):
        platforms = sorted(set(result.get('platforms', [])) | {
            e.location.id for e in events
            if e.location.parent.id == result['dhid'] and e.location.id != result['dhid']
        })
        if platforms != result.get('platforms', []):
            atomic_json(self.cache / (self.key(group) + '.platforms.json'),
                        dict(dhid=result['dhid'], platforms=platforms))
            result['platforms'] = platforms
