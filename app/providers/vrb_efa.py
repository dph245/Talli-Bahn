"""Optional VRB predictions for verified GTFS stop groups; never creates journeys."""
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import logging
import re
import sqlite3
import time

import httpx
from pydantic import BaseModel, Field, ValidationError

from ..database import connect

log = logging.getLogger(__name__)
URL = "https://bsvg.efa.de/vrbstd_relaunch/XML_DM_REQUEST"
INTERVAL = 60
MAX_AGE = 300
# Only platforms observed in the probe. Names are checked against the loaded GTFS.
STOPS = {
    "de:03158:1677:1:1": "Wolfenbüttel, Birkenweg",
    "de:03158:461:2:E": "Wolfenbüttel, Kornmarkt",
    "de:03158:458:1:A": "Wolfenbüttel, Bahnhof",
    "de:03101:255:1:B": "Braunschweig, Helmstedter Str.",
}


class Location(BaseModel):
    id: str
    name: str


class Destination(BaseModel):
    name: str = ""


class Transportation(BaseModel):
    number: str
    destination: Destination = Field(default_factory=Destination)


class Event(BaseModel):
    location: Location
    transportation: Transportation
    departureTimePlanned: datetime
    departureTimeEstimated: datetime | None = None
    realtimeStatus: list[str] = Field(default_factory=list)
    isRealtimeControlled: bool = False


class Response(BaseModel):
    stopEvents: list[Event]


def destination(value):
    # Observed GTFS presentation suffix only; no fuzzy matching or name rewriting.
    return re.sub(r"\s*\| Haltestelle \d+$", "", value).strip()


def match_predictions(events, departures, dhid, name):
    predictions = {}
    conflicts = set()
    for event in events:
        planned, estimated = event.departureTimePlanned, event.departureTimeEstimated
        if (event.location.id != dhid or event.location.name != name
                or estimated is None or planned.utcoffset() is None or estimated.utcoffset() is None
                or not (event.isRealtimeControlled or "MONITORED" in event.realtimeStatus)):
            continue
        candidates = [d for d in departures if d.line == event.transportation.number
                      and d.scheduled.timestamp() == planned.timestamp()]
        if len(candidates) > 1:
            target = destination(event.transportation.destination.name)
            candidates = [d for d in candidates if target and destination(d.destination) == target]
        if len(candidates) != 1:
            continue
        key = candidates[0].id  # GTFS service date, trip and stop sequence, never EFA IDs.
        if key in predictions and predictions[key] != estimated:
            conflicts.add(key)
        predictions[key] = estimated
    return {key: value for key, value in predictions.items() if key not in conflicts}


class EFAFeed:
    def __init__(self, static):
        self.static = static
        self.cached = {}
        self.tasks = {}
        self.checked = {}
        self.lock = asyncio.Lock()
        self.last_refresh = float("-inf")
        self.stopped = False

    def resolve_group(self, name):
        with connect(self.static.path) as db:
            rows = db.execute("SELECT stop_id, parent_station FROM stops WHERE stop_name=?", (name,)).fetchall()
            roots = {r['parent_station'] or r['stop_id'] for r in rows}
            if len(roots) != 1:
                return None
            root = roots.pop()
            station = db.execute("SELECT parent_station FROM stops WHERE stop_id=?", (root,)).fetchone()
            return root if station is not None and not station['parent_station'] else None

    async def start(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        self.tasks.clear()

    def request_refresh(self, board):
        if self.stopped or board.kind != "departures":
            return
        for dhid, name in STOPS.items():
            if board.stop.name != name:
                continue
            task = self.tasks.get(dhid)
            if task is not None and not task.done():
                continue
            cached_at = self.cached.get(dhid, (float("-inf"), {}))[0]
            checked_at = self.checked.get(dhid, float("-inf"))
            if time.monotonic() - max(cached_at, checked_at) < INTERVAL:
                continue
            # No await between checking and registering: concurrent board requests
            # share one pending refresh per DHID on the server event loop.
            self.tasks[dhid] = asyncio.create_task(
                self._refresh(dhid, name), name="vrb-efa-refresh")

    async def _refresh(self, dhid, name):
        async with self.lock:
            await asyncio.sleep(max(0, 1.1 - (time.monotonic() - self.last_refresh)))
            try:
                async with httpx.AsyncClient(timeout=8) as client:
                    await self.refresh_stop(client, dhid, name)
            finally:
                # Throttle failures too; only a later board request can retry.
                self.checked[dhid] = self.last_refresh = time.monotonic()

    async def refresh_stop(self, client, dhid, name):
        try:
            group = await asyncio.to_thread(self.resolve_group, name)
            if group is None:
                return
            response = await client.get(URL, params={
                "name_dm": dhid, "type_dm": "stop", "useRealtime": "1", "limit": "20",
                "outputFormat": "rapidJSON", "mode": "direct",
            })
            response.raise_for_status()
            events = Response.model_validate_json(response.content).stopEvents
            # Full group, before visibility filtering or any realtime updates. scheduled()
            # resolves active service dates (including exceptions and >24h GTFS times).
            _, departures = await asyncio.to_thread(
                self.static.scheduled, group, "departures", datetime.now(timezone.utc))
            predictions = match_predictions(events, departures, dhid, name)
            self.cached[dhid] = (time.monotonic(), predictions)
        except (httpx.HTTPError, ValidationError, ValueError, KeyError, sqlite3.Error) as error:
            log.warning("VRB-EFA-Aktualisierung fehlgeschlagen (%s); verwende frischen Cache oder GTFS", type(error).__name__)

    def enrich(self, board):
        self.request_refresh(board)
        if board.kind != "departures":
            return
        predictions = {}
        for checked, values in self.cached.values():
            if 0 <= time.monotonic() - checked <= MAX_AGE:
                for key, value in values.items():
                    predictions.setdefault(key, set()).add(value)
        applied = False
        for departure in board.journeys:
            values = predictions.get(departure.id, set())
            if len(values) != 1 or departure.realtime is not None or departure.cancelled:
                continue
            departure.realtime = next(iter(values)).astimezone(departure.scheduled.tzinfo)
            departure.source += " + VRB-EFA"
            applied = True
        if applied:
            board.source += " + VRB-EFA"
            board.realtime_status = "available"
