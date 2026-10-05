"""Reject a snapshot whose identifiers contradict the imported timetable.

Numeric GTFS IDs are scoped to a dataset, not durable journey identities. Alert
selectors share that namespace, so a conflicting snapshot cannot supply alerts
safely either. Absence of an update/optional field is not a contradiction.
"""
import logging
from datetime import datetime
from threading import Lock

from google.transit import gtfs_realtime_pb2 as gtfs

from ..database import connect
from .gtfs_static import service_start

log = logging.getLogger(__name__)


def identity_conflict(db, snapshot, *, require_witness=False):
    witnessed = False
    updates = [u for u in snapshot.updates.values()
               if u.trip.trip_id and u.trip.schedule_relationship in
               (gtfs.TripDescriptor.SCHEDULED, gtfs.TripDescriptor.CANCELED)]
    for offset in range(0, len(updates), 400):
        batch = updates[offset:offset + 400]
        ids = list({u.trip.trip_id for u in batch})
        marks = ','.join('?' for _ in ids)
        trips = {r['trip_id']: r for r in db.execute(
            f'SELECT trip_id, route_id, direction_id FROM trips WHERE trip_id IN ({marks})', ids)}
        stops = {}
        for row in db.execute(
                f'SELECT trip_id, stop_sequence, stop_id, arrival, departure FROM stop_times WHERE trip_id IN ({marks})', ids):
            stops.setdefault(row['trip_id'], {})[row['stop_sequence']] = row
        for update in batch:
            trip = update.trip
            local = trips.get(trip.trip_id)
            if local is None:  # Regional/static subsets need not include every trip.
                continue
            if trip.route_id and trip.route_id != local['route_id']:
                return f'trip_id={trip.trip_id}: route_id widerspricht GTFS'
            if (trip.HasField('direction_id') and local['direction_id'] is not None
                    and trip.direction_id != local['direction_id']):
                return f'trip_id={trip.trip_id}: direction_id widerspricht GTFS'
            itinerary = stops.get(trip.trip_id, {})
            start = None
            if trip.start_date:
                try:
                    start = service_start(datetime.strptime(trip.start_date, '%Y%m%d').date()).timestamp()
                except ValueError:
                    return f'trip_id={trip.trip_id}: ungültiger Betriebstag'
            for stop in update.stop_time_update:
                if stop.HasField('stop_sequence'):
                    row = itinerary.get(stop.stop_sequence)
                    expected = row['stop_id'] if row is not None else None
                    if expected is None or (stop.stop_id and stop.stop_id != expected):
                        return (f'trip_id={trip.trip_id}, stop_sequence={stop.stop_sequence}: '
                                f'RT stop_id={stop.stop_id!r}, GTFS stop_id={expected!r}')
                else:
                    matches = [r for r in itinerary.values() if r['stop_id'] == stop.stop_id]
                    if stop.stop_id and not matches:
                        return f'trip_id={trip.trip_id}: RT stop_id={stop.stop_id!r} fehlt im GTFS-Fahrtverlauf'
                    row = matches[0] if len(matches) == 1 else None
                if row is not None and stop.stop_id:
                    witnessed = True
                # Same route/stop pattern can also belong to a different departure.
                # time - delay is the source's scheduled time, not a plausibility
                # limit on early running or large delays. Require both fields.
                if row is not None and start is not None:
                    for name in ('arrival', 'departure'):
                        event = getattr(stop, name)
                        if (stop.HasField(name) and event.HasField('time') and event.HasField('delay')
                                and row[name] is not None and event.time - event.delay != start + row[name]):
                            return f'trip_id={trip.trip_id}: {name}-Sollzeit aus RT time-delay widerspricht GTFS'

    if require_witness and not witnessed:
        return 'ID-Konflikt noch nicht durch einen passenden Fahrtverlauf oder neuen Import aufgelöst'
    return None


class IdentityGuard:
    """Check once per snapshot/database generation, shared by board readers."""
    def __init__(self, path):
        self.path = path
        self.lock = Lock()
        self.snapshot = None
        self.signature = None
        self.conflict = None

    def accepts(self, snapshot):
        with self.lock:
            stat = self.path.stat()
            signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if self.snapshot is not snapshot or self.signature != signature:
                with connect(self.path) as db:
                    self.conflict = identity_conflict(
                        db, snapshot, require_witness=bool(self.conflict and self.signature == signature))
                self.snapshot, self.signature = snapshot, signature
                if self.conflict:
                    log.warning('GTFS-Realtime-Snapshot wegen inkompatibler Fahrplan-IDs verworfen '
                                '(Feedzeit %s; %s). Passenden GTFS-Import und RT-Feed verwenden; '
                                'keine Prognosen/Ausfälle/Alerts aus diesem Snapshot.',
                                snapshot.timestamp, self.conflict)
            return self.conflict is None
