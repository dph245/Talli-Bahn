"""Optional VRB predictions for verified GTFS stop groups; never creates journeys."""
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import logging
import re
import sqlite3
import time
from typing import NamedTuple

import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator

from ..database import connect
from .efa_mapping import MappingStore, STOPFINDER, normalized

log = logging.getLogger(__name__)
URL = "https://bsvg.efa.de/vrbstd_relaunch/XML_DM_REQUEST"
INTERVAL = 60
MAX_AGE = 300


class ParentLocation(BaseModel):
    id: str = ""


class Location(BaseModel):
    id: str
    name: str
    properties: dict = Field(default_factory=dict)
    parent: ParentLocation = Field(default_factory=ParentLocation)

    def platform(self):
        for key in ("platformName", "platform"):
            value = self.properties.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None


class Prediction(NamedTuple):
    estimated: datetime
    platform: str | None = None


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

    @model_validator(mode="before")
    @classmethod
    def empty_departure_monitor(cls, data):
        # Observed rapidJSON empty result: BROKER/-4030, with stopEvents omitted.
        # Keep missing/malformed events in other responses as validation failures.
        if isinstance(data, dict) and "stopEvents" not in data:
            messages = data.get("systemMessages")
            if isinstance(messages, list) and messages and all(
                isinstance(message, dict) and message.get("type") == "error"
                and message.get("module") == "BROKER" and message.get("code") == -4030
                for message in messages
            ):
                return {**data, "stopEvents": []}
        return data


def destination(value):
    # Observed GTFS presentation suffix only; no fuzzy matching or name rewriting.
    return re.sub(r"\s*\| Haltestelle \d+$", "", value).strip()


def match_predictions(events, departures, dhid, name, assigned=()):
    predictions = {}
    conflicts = set()
    identities = [(dhid, name), *((a['dhid'], a['name']) for a in assigned)]
    for event in events:
        planned, estimated = event.departureTimePlanned, event.departureTimeEstimated
        if (not any((event.location.id == stop or event.location.parent.id == stop)
                    and event.location.name == stop_name for stop, stop_name in identities)
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
        platform = event.location.platform()
        if key in predictions:
            if predictions[key].estimated != estimated:
                conflicts.add(key)
            if predictions[key].platform != platform:
                platform = None
        predictions[key] = Prediction(estimated, platform)
    return {key: value for key, value in predictions.items() if key not in conflicts}


class EFAFeed:
    def __init__(self, static):
        self.static = static
        try:
            self.mapping = MappingStore(static.path)
        except (OSError, ValueError, KeyError):
            log.warning("VRB-EFA-Mapping nicht lesbar; verwende GTFS")
            self.mapping = None
        self.cached = {}
        self.tasks = {}
        self.checked = {}
        self.lock = asyncio.Lock()
        self.last_refresh = float("-inf")
        self.stopped = False

    def resolve_group(self, name, group_id=None):
        with connect(self.static.path) as db:
            if group_id is not None:
                station = db.execute("SELECT stop_name, parent_station FROM stops WHERE stop_id=?", (group_id,)).fetchone()
                if station and not station['parent_station'] and normalized(station['stop_name']) == normalized(name):
                    return group_id
                return None
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
        if self.mapping is None:
            return
        group = self.mapping.group(board.stop.id, board.stop.name)
        if group is None:
            return
        key = group['id']
        task = self.tasks.get(key)
        if task is not None and not task.done():
            return
        cached_at = self.cached.get(key, (float("-inf"), {}))[0]
        checked_at = self.checked.get(key, float("-inf"))
        if time.monotonic() - max(cached_at, checked_at) < INTERVAL:
            return
        self.tasks[key] = asyncio.create_task(self._refresh(group), name="vrb-efa-refresh")

    async def _get(self, client, url, params):
        # Discovery and departure requests share the same sequential rate limiter.
        await asyncio.sleep(max(0, 1.1 - (time.monotonic() - self.last_refresh)))
        try:
            return await client.get(url, params=params)
        finally:
            self.last_refresh = time.monotonic()

    async def _refresh(self, group):
        async with self.lock:
            try:
                async with httpx.AsyncClient(timeout=8) as client:
                    result = await asyncio.to_thread(self.mapping.read, group)
                    if result is None:
                        response = await self._get(client, STOPFINDER, {
                            'name_sf': group['name'], 'type_sf': 'stop', 'locationServerActive': '1',
                            'outputFormat': 'rapidJSON', 'coordOutputFormat': 'WGS84[dd.ddddd]',
                        })
                        response.raise_for_status()
                        result = await asyncio.to_thread(self.mapping.save, group, response.json())
                    if result['status'] == 'UNIQUE':
                        options = {'assigned': result['assigned']} if result.get('assigned') else {}
                        events = await self.refresh_stop(client, result['dhid'], result['name'], group['id'], **options)
                        if events is not None:
                            await asyncio.to_thread(self.mapping.record_platforms, group, result, events)
            except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
                log.warning("VRB-EFA-Mapping fehlgeschlagen (%s); verwende GTFS", type(error).__name__)
            finally:
                self.checked[group['id']] = time.monotonic()

    async def refresh_stop(self, client, dhid, name, group_id=None, *, assigned=()):
        try:
            group = await asyncio.to_thread(self.resolve_group, name, group_id) if group_id is not None else await asyncio.to_thread(self.resolve_group, name)
            if group is None:
                return
            response = await self._get(client, URL, {
                "name_dm": dhid, "type_dm": "stop", "useRealtime": "1", "limit": "20",
                "outputFormat": "rapidJSON", "mode": "direct",
            })
            response.raise_for_status()
            events = Response.model_validate_json(response.content).stopEvents
            # Full group, before visibility filtering or any realtime updates. scheduled()
            # resolves active service dates (including exceptions and >24h GTFS times).
            _, departures = await asyncio.to_thread(
                self.static.scheduled, group, "departures", datetime.now(timezone.utc))
            predictions = match_predictions(events, departures, dhid, name, assigned)
            self.cached[group_id or dhid] = (time.monotonic(), predictions)
            return events
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
            if len(values) != 1 or departure.cancelled:
                continue
            prediction = next(iter(values))
            changed = False
            if departure.realtime is None:
                departure.realtime = prediction.estimated.astimezone(departure.scheduled.tzinfo)
                changed = True
            # Fill gaps only: preserve existing platform observations, including GTFS.
            # A GTFS-RT time does not prevent independently filling a missing platform.
            if prediction.platform and not (departure.platform or "").strip():
                departure.platform = prediction.platform
                changed = True
            if not changed:
                continue
            departure.source += " + VRB-EFA"
            applied = True
        if applied:
            board.source += " + VRB-EFA"
            board.realtime_status = "available"
