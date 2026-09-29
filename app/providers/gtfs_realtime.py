"""GTFS-RT data stays in this adapter; only normalized fields leave it."""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import re
from urllib.parse import urlsplit
import time
import httpx
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2 as gtfs
from ..models import Departure, BoardKind, ServiceAlert, alert_content_key

log = logging.getLogger(__name__)
DEFAULT_FEED_URL = "https://realtime.gtfs.de/realtime-free.pb"
FEED_INTERVAL = 10
MAX_AGE = 180
DOWNLOAD_TIMEOUT = 120


@dataclass
class Snapshot:
    updates: dict = field(default_factory=dict)
    alerts: list = field(default_factory=list)
    timestamp: int = 0
    alert_index: dict | None = None

    def fresh(self):
        return self.timestamp > 0 and -60 <= time.time() - self.timestamp <= MAX_AGE


class RealtimeFeed:
    def __init__(self, url: str | None, token: str | None = None):
        self.url = url
        self.token = token
        self.lock = asyncio.Lock()
        self.checked = float('-inf')
        self.cached = Snapshot()
        self.refresh_task = None

    async def current_snapshot(self):
        """Poll the shared download without tying its lifetime to a board request."""
        if not self.url:
            return Snapshot(), "unavailable"
        if (self.refresh_task is None or self.refresh_task.done()) and time.monotonic() - self.checked >= FEED_INTERVAL:
            self.refresh_task = asyncio.create_task(self.snapshot())
        if self.cached.fresh():
            return self.cached, "available"
        if self.refresh_task is not None and not self.refresh_task.done():
            return Snapshot(), "loading"
        return Snapshot(), "unavailable"

    async def snapshot(self) -> Snapshot:
        if not self.url:
            return Snapshot()
        async with self.lock:
            if time.monotonic() - self.checked < FEED_INTERVAL:
                return self.cached if self.cached.fresh() else Snapshot()
            try:
                headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
                async with asyncio.timeout(DOWNLOAD_TIMEOUT), httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                    response = await client.get(self.url, headers=headers)
                    response.raise_for_status()
                self.cached = await asyncio.to_thread(parse_snapshot, response.content, self.url)
            except (httpx.HTTPError, DecodeError, ValueError, TimeoutError) as error:
                # Missing realtime is a normal timetable fallback, not an API/UI error.
                self.cached = Snapshot()
                log.warning("GTFS-Realtime nicht verfügbar (%s); verwende Sollzeiten", type(error).__name__)
            self.checked = time.monotonic()
            return self.cached


# GTFS.de currently encodes attribution as trip-scoped INFO alerts, not metadata.
# Full-string matching deliberately preserves mixed attribution/disruption messages.
_PROVENANCE = re.compile(
    r"(?:Echtzeitdaten (?:aufbereitet|verarbeitet) von GTFS\.de, bereitgestellt (?:von|vom) "
    r"|Real[- ]?time data (?:processed|prepared) by GTFS\.de, (?:provided|supplied) by )"
    r"[^.!?;:\n]+(?:\.[^\s.!?;:\n]+)*\.?", re.IGNORECASE)


def is_provenance_alert(alert, source_url):
    if urlsplit(source_url or "").hostname != "realtime.gtfs.de":
        return False
    if (alert.cause != gtfs.Alert.UNKNOWN_CAUSE or alert.effect != gtfs.Alert.UNKNOWN_EFFECT
            or alert.severity_level != gtfs.Alert.INFO or alert.active_period
            or any(t.text.strip() for t in alert.header_text.translation)
            or any(t.text.strip() for t in alert.url.translation)):
        return False
    if not alert.informed_entity or any(
        not s.trip.trip_id or s.stop_id or s.route_id or s.agency_id or s.HasField("route_type")
        for s in alert.informed_entity
    ):
        return False
    texts = [t.text.strip() for t in alert.description_text.translation if t.text.strip()]
    return bool(texts) and all(_PROVENANCE.fullmatch(text) for text in texts)


def parse_snapshot(content, source_url=DEFAULT_FEED_URL):
    feed = gtfs.FeedMessage()
    feed.ParseFromString(content)
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
        if entity.HasField("alert") and not is_provenance_alert(entity.alert, source_url):
            snapshot.alerts.append((entity.id, entity.alert))
    snapshot.alert_index = index_alerts(snapshot.alerts)
    return snapshot


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
    board_seen = set()
    journey_seen = [set(alert_content_key(a) for a in d.alerts) for d in departures]
    for entity_id, alert in entries:
        normalized = normalized_alert(entity_id, alert)
        key = alert_content_key(normalized)
        if active(alert, now) and any(selector_matches(s, None, {station_id}) for s in alert.informed_entity):
            if key not in board_seen:
                board_alerts.append(normalized)
                board_seen.add(key)
        for departure, seen in zip(departures, journey_seen):
            if active(alert, departure.realtime or departure.scheduled) and any(
                selector_matches(s, departure, {station_id, departure.stop_id}) for s in alert.informed_entity
            ):
                if key not in seen:
                    departure.alerts.append(normalized)
                    seen.add(key)
    return board_alerts


def index_alerts(entries):
    """Index each OR selector by one restrictive field; final matching stays exact."""
    index = {}
    for position, (_, alert) in enumerate(entries):
        for selector in alert.informed_entity:
            if selector.stop_id:
                key = ("stop", selector.stop_id)
            elif selector.trip.trip_id:
                key = ("trip", selector.trip.trip_id)
            elif selector.route_id or selector.trip.route_id:
                key = ("route", selector.route_id or selector.trip.route_id)
            elif selector.agency_id:
                key = ("agency", selector.agency_id)
            elif selector.HasField("route_type"):
                key = ("type", selector.route_type)
            else:
                key = ("global", "")
            index.setdefault(key, set()).add(position)
    return index


def matching_alert_entries(snapshot, departures, station_id):
    if snapshot.alert_index is None:
        return snapshot.alerts
    keys = {("global", ""), ("stop", station_id)}
    for departure in departures:
        keys.update({("stop", departure.stop_id), ("trip", departure.trip_id),
                     ("route", departure.route_id), ("agency", departure.agency_id),
                     ("type", departure.route_type)})
    positions = set()
    for key in keys:
        positions.update(snapshot.alert_index.get(key, ()))
    return [snapshot.alerts[position] for position in sorted(positions)]
