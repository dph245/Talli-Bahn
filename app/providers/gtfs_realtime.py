"""GTFS-RT data stays in this adapter; only normalized fields leave it."""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import time
import httpx
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2 as gtfs
from ..models import Departure, BoardKind, ServiceAlert

log = logging.getLogger(__name__)
DEFAULT_FEED_URL = "https://realtime.gtfs.de/realtime-free.pb"
FEED_INTERVAL = 10
MAX_AGE = 180


@dataclass
class Snapshot:
    updates: dict = field(default_factory=dict)
    alerts: list = field(default_factory=list)
    timestamp: int = 0

    def fresh(self):
        return self.timestamp > 0 and -60 <= time.time() - self.timestamp <= MAX_AGE


class RealtimeFeed:
    def __init__(self, url: str | None, token: str | None = None):
        self.url = url
        self.token = token
        self.lock = asyncio.Lock()
        self.checked = float('-inf')
        self.cached = Snapshot()

    async def snapshot(self) -> Snapshot:
        if not self.url:
            return Snapshot()
        async with self.lock:
            if time.monotonic() - self.checked < FEED_INTERVAL:
                return self.cached if self.cached.fresh() else Snapshot()
            try:
                headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
                async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                    response = await client.get(self.url, headers=headers)
                    response.raise_for_status()
                feed = gtfs.FeedMessage()
                feed.ParseFromString(response.content)
                snapshot = Snapshot(timestamp=feed.header.timestamp)
                if not feed.IsInitialized() or not snapshot.fresh():
                    raise ValueError("Feed unvollständig oder veraltet")
                if feed.header.incrementality == gtfs.FeedHeader.DIFFERENTIAL:
                    raise ValueError("Differenzielle Feeds werden nicht unterstützt")
                for entity in feed.entity:
                    if entity.is_deleted:
                        continue
                    if entity.HasField("trip_update"):
                        update = entity.trip_update
                        if update.timestamp and not -60 <= time.time() - update.timestamp <= MAX_AGE:
                            continue
                        snapshot.updates[(update.trip.trip_id, update.trip.start_date)] = update
                    if entity.HasField("alert"):
                        snapshot.alerts.append((entity.id, entity.alert))
                self.cached = snapshot
            except (httpx.HTTPError, DecodeError, ValueError):
                # Missing realtime is a normal timetable fallback, not an API/UI error.
                self.cached = Snapshot()
                log.warning("GTFS-Realtime nicht verfügbar; verwende Sollzeiten")
            self.checked = time.monotonic()
            return self.cached


def apply_update(departure: Departure, kind: BoardKind, updates: dict):
    update = updates.get((departure.trip_id, departure.service_date))
    if update is None:
        update = updates.get((departure.trip_id, ""))
    if update is None:
        return
    if update.timestamp and not -60 <= time.time() - update.timestamp <= MAX_AGE:
        return
    if update.trip.schedule_relationship == gtfs.TripDescriptor.CANCELED:
        departure.cancelled = True
        departure.source = "GTFS + GTFS-Realtime"
        return
    if update.trip.schedule_relationship != gtfs.TripDescriptor.SCHEDULED:
        return
    event_name = "departure" if kind == "departures" else "arrival"
    for stop in update.stop_time_update:
        matches = stop.stop_sequence == departure.sequence if stop.HasField("stop_sequence") else stop.stop_id == departure.stop_id
        if not matches:
            continue
        if stop.schedule_relationship == gtfs.TripUpdate.StopTimeUpdate.SKIPPED:
            departure.cancelled = True
            departure.source = "GTFS + GTFS-Realtime"
            return
        if stop.schedule_relationship == gtfs.TripUpdate.StopTimeUpdate.NO_DATA:
            return
        event = getattr(stop, event_name)
        if stop.HasField(event_name):
            if event.HasField("time"):
                departure.realtime = datetime.fromtimestamp(event.time, timezone.utc).astimezone(departure.scheduled.tzinfo)
            elif event.HasField("delay"):
                departure.realtime = (departure.scheduled.astimezone(timezone.utc) + timedelta(seconds=event.delay)).astimezone(departure.scheduled.tzinfo)
        if departure.realtime is not None:
            departure.source = "GTFS + GTFS-Realtime"
        return
    if update.HasField("delay"):
        departure.realtime = (departure.scheduled.astimezone(timezone.utc) + timedelta(seconds=update.delay)).astimezone(departure.scheduled.tzinfo)
        departure.source = "GTFS + GTFS-Realtime"


def translated(value) -> str:
    translations = list(value.translation)
    for language in ("de", "en", ""):
        for item in translations:
            if item.language.lower().split('-')[0] == language:
                return item.text
    return translations[0].text if translations else ""


def active(alert, instant: datetime) -> bool:
    stamp = instant.timestamp()
    return not alert.active_period or any(
        (not period.HasField("start") or period.start <= stamp)
        and (not period.HasField("end") or stamp < period.end)
        for period in alert.active_period)


def selector_matches(selector, departure: Departure | None, stop_ids: set[str]) -> bool:
    # Fields inside a selector are AND-ed; the selector list is OR-ed.
    if selector.stop_id and selector.stop_id not in stop_ids:
        return False
    if selector.agency_id and (departure is None or selector.agency_id != departure.agency_id):
        return False
    if selector.route_id and (departure is None or selector.route_id != departure.route_id):
        return False
    if selector.HasField("route_type") and (departure is None or selector.route_type != departure.route_type):
        return False
    if selector.HasField("trip"):
        if departure is None:
            return False
        trip = selector.trip
        if trip.trip_id and trip.trip_id != departure.trip_id:
            return False
        if trip.route_id and trip.route_id != departure.route_id:
            return False
        if trip.start_date and trip.start_date != departure.service_date:
            return False
        if trip.HasField("direction_id") and trip.direction_id != departure.direction_id:
            return False
        # Frequency/start-time-specific selectors cannot be matched safely yet.
        if trip.start_time:
            return False
    return True


def normalized_alert(entity_id, alert):
    return ServiceAlert(id=f"gtfs-rt:{entity_id}", header=translated(alert.header_text) or "Verkehrsmeldung",
                        description=translated(alert.description_text), source="GTFS-Realtime")


def apply_alerts(departures: list[Departure], entries: list, station_id: str, now: datetime):
    board_alerts = []
    for entity_id, alert in entries:
        if active(alert, now) and any(selector_matches(s, None, {station_id}) for s in alert.informed_entity):
            board_alerts.append(normalized_alert(entity_id, alert))
        for departure in departures:
            if active(alert, departure.realtime or departure.scheduled) and any(
                selector_matches(s, departure, {station_id, departure.stop_id}) for s in alert.informed_entity
            ):
                departure.alerts.append(normalized_alert(entity_id, alert))
    return board_alerts
