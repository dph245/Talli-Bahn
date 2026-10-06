"""Read-only, ID-independent quality gate for a completed GTFS database."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import re
import unicodedata

from .database import connect

EFA_AGENCIES = ('Tarifverb Region Braunschweig', 'Regionalbus Braunschweig')


def normalized(value):
    return re.sub(r'\s*,\s*', ',', ' '.join(unicodedata.normalize('NFC', value).casefold().split()))


def agency_ids(db, names):
    return [r[0] for r in db.execute(
        'SELECT agency_id FROM agencies WHERE agency_name IN (' + ','.join('?' for _ in names) + ')', names)]


def calendar_data(path):
    with connect(path) as db:
        calendars = {r['service_id']: dict(r) for r in db.execute('SELECT * FROM calendar')}
        exceptions = defaultdict(dict)
        for r in db.execute('SELECT * FROM calendar_dates'):
            exceptions[r['service_id']][r['date']] = r['exception_type']
    dates = [c[k] for c in calendars.values() for k in ('start_date', 'end_date')]
    dates += [day for values in exceptions.values() for day, kind in values.items() if kind == 1]
    return calendars, exceptions, (min(dates), max(dates)) if dates else None


def common_days(candidate, reference):
    a, b = candidate[2], reference[2]
    if not a or not b:
        return []
    start, end = max(a[0], b[0]), min(a[1], b[1])
    if start > end:
        return []
    day = datetime.strptime(start, '%Y%m%d')
    return [(day + timedelta(days=i)).strftime('%Y%m%d') for i in range(14)
            if (day + timedelta(days=i)).strftime('%Y%m%d') <= end]


def service_weights(calendar, days):
    calendars, exceptions, _ = calendar
    weekdays = 'monday tuesday wednesday thursday friday saturday sunday'.split()
    result = {}
    for sid in calendars.keys() | exceptions.keys():
        c = calendars.get(sid)
        count = 0
        for day in days:
            active = bool(c and c['start_date'] <= day <= c['end_date']
                          and c[weekdays[datetime.strptime(day, '%Y%m%d').weekday()]])
            kind = exceptions.get(sid, {}).get(day)
            count += kind == 1 or (kind != 2 and active)
        result[sid] = count
    return result


def measure(path, calendar=None, days=()):
    """One ordered streaming pass; no catalog, ZIP, writes or cross-feed ID joins."""
    calendar = calendar or calendar_data(path)
    weights = service_weights(calendar, days)
    with connect(path) as db:
        selected = set(agency_ids(db, EFA_AGENCIES))
        agencies = {r['agency_id']: normalized(r['agency_name']) for r in db.execute('SELECT * FROM agencies')}
        routes = {r['route_id']: (agencies[r['agency_id']], normalized(r['route_short_name'] or r['route_long_name'] or ''), r['route_type'])
                  for r in db.execute('SELECT * FROM routes')}
        route_agencies = {r['route_id']: r['agency_id'] for r in db.execute('SELECT route_id,agency_id FROM routes')}
        stops = {r['stop_id']: (normalized(r['stop_name']), r['parent_station'] or r['stop_id'])
                 for r in db.execute('SELECT stop_id,stop_name,parent_station FROM stops')}
        metrics = defaultdict(lambda: dict(trips=0, events=0, suspicious=0, pathological=0, active_trips=0, active_events=0))
        groups, active_groups = defaultdict(set), defaultdict(set)
        efa, active_efa = set(), set()
        examples, hard_examples = [], []
        current = None
        names = set()
        regular_names = set()
        count = equal = run = longest = 0
        start = previous = None

        def finish():
            if current is None:
                return
            metric = metrics[rkey]
            metric['trips'] += 1
            metric['events'] += count
            metric['active_trips'] += weight
            metric['active_events'] += count * weight
            if len(names) >= 8 and equal >= .8 * count and longest >= 6:
                metric['suspicious'] += 1
                # Demand-service boarding/alighting areas can legitimately list
                # many equal times. Require eight ordinary bidirectional stops
                # at the repeated start time before treating this as blocking.
                metric['pathological'] += len(regular_names) >= 8
                sample = hard_examples if len(regular_names) >= 8 else examples
                if len(sample) < 5:
                    sample.append(dict(trip=current, route=rkey, stops=count,
                                       repeated_start=equal, longest_run=longest, start=start))

        # CROSS JOIN fixes the outer trip scan; the existing index provides stop order.
        rows = db.execute('''SELECT t.trip_id,t.route_id,t.service_id,st.stop_id,st.departure,
            st.pickup_type,st.drop_off_type
            FROM trips t CROSS JOIN stop_times st INDEXED BY times_trip_sequence ON st.trip_id=t.trip_id
            ORDER BY t.trip_id,st.stop_sequence''')
        for tid, rid, sid, stop, departure, pickup, dropoff in rows:
            if tid != current:
                finish()
                current, rkey = tid, routes[rid]
                aid = route_agencies[rid]
                weight = weights.get(sid, 0)
                names = set()
                regular_names = set()
                count = equal = run = longest = 0
                start = departure
                previous = None
            name, group = stops[stop]
            names.add(name)
            count += 1
            if departure is not None:
                equal += start is not None and departure == start
                if departure == start and pickup == 0 and dropoff == 0:
                    regular_names.add(name)
                run = run + 1 if departure == previous else 1
                longest = max(longest, run)
            else:
                run = 0
            previous = departure
            groups[agencies[aid]].add(group)
            if weight:
                active_groups[agencies[aid]].add(group)
            if aid in selected:
                efa.add(group)
                if weight:
                    active_efa.add(group)
        finish()

        def group_metric(ids):
            return dict(groups=len(ids), names=len({stops[g][0] for g in ids}))
        return dict(path=str(path), routes=dict(metrics), efa=group_metric(efa),
                    active_efa=group_metric(active_efa),
                    agencies={k: group_metric(v) for k, v in groups.items()},
                    active_agencies={k: group_metric(v) for k, v in active_groups.items()},
                    suspicious=sum(m['suspicious'] for m in metrics.values()),
                    pathological=sum(m['pathological'] for m in metrics.values()),
                    examples=(hard_examples + examples)[:5])


def loss(old, new, fraction, absolute=0):
    return old > 0 and old - new >= absolute and (old - new) / old >= fraction


def shortened(old, new, fraction):
    return (old['trips'] >= 20 and new['trips'] >= .4 * old['trips']
            and loss(old['events'], new['events'], fraction)
            and loss(old['events'] / old['trips'], new['events'] / new['trips'], fraction))


@dataclass
class QualityReport:
    candidate: dict
    reference: dict | None
    days: list
    warnings: list
    errors: list

    @property
    def accepted(self):
        return not self.errors

    def render(self):
        lines = ['GTFS quality: ' + ('ACCEPTED' if self.accepted else 'REJECTED'),
                 f"Candidate: {self.candidate['path']}",
                 f"Reference: {self.reference['path'] if self.reference else 'none'}",
                 f"EFA-eligible groups: {self.candidate['efa']['groups']} (before coordinate filtering)",
                 f"Suspicious repeated start-time trips: {self.candidate['suspicious']}; "
                 f"with >=8 ordinary boarding/alighting stops: {self.candidate['pathological']}"]
        if self.reference:
            old, new = self.reference['efa']['groups'], self.candidate['efa']['groups']
            delta = f'{(new-old)/old:+.1%}' if old else 'n/a'
            lines.append(f'EFA-eligible groups: {old} -> {new} ({delta})')
            lines.append('Common service days: ' + ', '.join(self.days))
        for label, values in [('ERROR', self.errors), ('WARNING', self.warnings)]:
            lines += [f'{label}: {value}' for value in values[:10]]
            if len(values) > 10:
                lines.append(f'{label}: {len(values)-10} further findings')
        for example in self.candidate['examples']:
            lines.append(f'Example: {example}')
        return '\n'.join(lines)


class QualityError(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__(report.render())


def evaluate(candidate, reference=None):
    candidate, reference = Path(candidate), Path(reference) if reference else None
    cal = calendar_data(candidate)
    old_cal = calendar_data(reference) if reference else None
    days = common_days(cal, old_cal) if reference else []
    new = measure(candidate, cal, days)
    old = measure(reference, old_cal, days) if reference else None
    warnings, errors = [], []
    for key, metric in sorted(new['routes'].items()):
        n = metric['suspicious']
        if n:
            message = f'route {key}: {n}/{metric["trips"]} trips contain suspicious repeated start-time departures'
            hard = metric['pathological']
            message += f'; {hard} with >=8 ordinary boarding/alighting stops'
            (errors if key[1] and hard >= 50 and hard >= .1 * metric['trips'] else warnings).append(message)
    if old:
        regional = set()
        pairs = [('EFA-eligible', old['efa'], new['efa'], old['active_efa'], new['active_efa'])]
        for agency, metric in old['agencies'].items():
            zero = dict(groups=0, names=0)
            pairs.append((agency, metric, new['agencies'].get(agency, zero),
                          old['active_agencies'].get(agency, zero), new['active_agencies'].get(agency, zero)))
        for label, before, after, active_before, active_after in pairs:
            if loss(before['groups'], after['groups'], .05, 50):
                warnings.append(f'{label} groups: {before["groups"]} -> {after["groups"]}; '
                                f'normalized names: {before["names"]} -> {after["names"]}')
            if (len(days) >= 7 and loss(before['groups'], after['groups'], .1, 100)
                    and loss(before['names'], after['names'], .1, 100)
                    and loss(active_before['groups'], active_after['groups'], .1, 100)
                    and loss(active_before['names'], active_after['names'], .1, 100)):
                regional.add(label)
        for key in sorted(old['routes'].keys() & new['routes'].keys()):
            before, after = old['routes'][key], new['routes'][key]
            if not key[1]:
                continue
            if shortened(before, after, .25):
                warnings.append(f'route {key}: trips {before["trips"]} -> {after["trips"]}; '
                                f'stop events {before["events"]} -> {after["events"]}')
            active_before = dict(trips=before['active_trips'], events=before['active_events'])
            active_after = dict(trips=after['active_trips'], events=after['active_events'])
            # Corroborating losses must affect the same agency, not unrelated regions.
            if (key[0] in regional and len(days) >= 7 and shortened(before, after, .4)
                    and shortened(active_before, active_after, .4)):
                errors.append(f'route {key}: massive shortening and regional group loss confirmed on common service days')
        if len(days) < 7:
            warnings.append('Insufficient common calendar coverage; relative findings cannot block activation')
    return QualityReport(new, old, days, warnings, errors)


def check_quality(candidate, reference=None):
    report = evaluate(candidate, reference)
    if not report.accepted:
        raise QualityError(report)
    return report
