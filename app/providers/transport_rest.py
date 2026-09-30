"""Conservative platform enrichment using the public transport.rest API."""
import asyncio
from collections import Counter
from datetime import datetime
import logging
import re
import time
import unicodedata

import httpx

log = logging.getLogger(__name__)
BASE_URL = "https://v6.db.transport.rest"


def place_key(value):
    text = " ".join(unicodedata.normalize("NFC", value).casefold().split())
    # GTFS Germany sometimes repeats the city: "Hamburg, Hamburg Hbf".
    city, separator, name = text.partition(", ")
    return name if separator and (name == city or name.startswith(city + " ")) else text


def line_key(value):
    return re.sub(r"\s+", "", value).casefold()


def event_key(line, scheduled, destination):
    return line_key(line), scheduled.timestamp(), place_key(destination)


def enrich_platforms(board, rows, station_id):
    candidates = {}
    for row in rows:
        try:
            if row["stop"]["id"] != station_id or row["line"]["mode"] != "train":
                continue
            scheduled = datetime.fromisoformat(row["plannedWhen"].replace("Z", "+00:00"))
            if scheduled.tzinfo is None:
                continue
            target = row.get("direction") if board.kind == "departures" else (row.get("provenance") or row.get("origin", {}).get("name"))
            key = event_key(row["line"]["name"], scheduled, target)
            candidates.setdefault(key, []).append(row)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    counts = Counter(event_key(d.line, d.scheduled, d.destination) for d in board.journeys if d.mode == "rail")
    changed = False
    for departure in board.journeys:
        if departure.mode != "rail" or "DB RIS::Boards" in departure.source:
            continue
        key = event_key(departure.line, departure.scheduled, departure.destination)
        matches = candidates.get(key, [])
        if counts[key] != 1 or len(matches) != 1:
            continue
        row = matches[0]
        current, planned = row.get("platform"), row.get("plannedPlatform")
        current = current.strip() if isinstance(current, str) else ""
        planned = planned.strip() if isinstance(planned, str) else ""
        if not (current or planned):
            continue
        # A planned-only observation cannot supersede an existing current track.
        if current:
            departure.platform = current
        elif not departure.platform:
            departure.platform = planned
        departure.scheduled_platform = planned or departure.scheduled_platform
        departure.source += " + transport.rest"
        changed = True
    if changed:
        board.source += " + transport.rest"


class TransportRestProvider:
    def __init__(self, primary, base_url=BASE_URL):
        self.primary = primary
        self.base_url = base_url.rstrip("/")
        self.cache = {}
        self.lock = asyncio.Lock()

    async def start(self):
        await self.primary.start()

    async def stop(self):
        await self.primary.stop()

    def search(self, query):
        return self.primary.search(query)

    async def static_board(self, stop_id, kind, now):
        return await self.primary.static_board(stop_id, kind, now)

    async def _json(self, client, path, params):
        response = await client.get(self.base_url + path, params=params)
        response.raise_for_status()
        if int(response.headers.get("Age", "0")) > 180:
            raise ValueError("Stale platform response")
        return response.json()

    async def fetch(self, stop, kind, now):
        key = (stop.id, kind)
        async with self.lock:
            cached = self.cache.get(key)
            if cached and time.monotonic() - cached[0] < 60:
                return cached[1]
            result = None
            try:
                async with asyncio.timeout(8), httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
                    places = await self._json(client, "/locations", {
                        "query": place_key(stop.name), "results": 10, "addresses": "false", "poi": "false"})
                    if not isinstance(places, list):
                        raise ValueError("Invalid station response")
                    matches = {p["id"] for p in places if p.get("type") in ("stop", "station")
                               and place_key(p.get("name", "")) == place_key(stop.name)
                               and isinstance(p.get("id"), str) and re.fullmatch(r"[0-9]+", p["id"])}
                    if len(matches) == 1:
                        station_id = matches.pop()
                        payload = await self._json(client, f"/stops/{station_id}/{kind}", {
                            "when": now.isoformat(), "duration": 120, "results": 500,
                            "includeRelatedStations": "false", "remarks": "false"})
                        rows = payload.get(kind) if isinstance(payload, dict) else payload
                        if not isinstance(rows, list):
                            raise ValueError("Invalid board response")
                        result = station_id, rows
            except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError, AttributeError):
                log.warning("transport.rest-Gleise nicht verfügbar; primären Fahrplan beibehalten")
            cutoff = time.monotonic() - 60
            self.cache = {k: v for k, v in self.cache.items() if v[0] > cutoff}
            self.cache[key] = time.monotonic(), result
            return result

    async def board(self, stop_id, kind, now):
        board = await self.primary.board(stop_id, kind, now)
        if any(d.mode == "rail" for d in board.journeys):
            extra = await self.fetch(board.stop, kind, now)
            if extra is not None:
                station_id, rows = extra
                enrich_platforms(board, rows, station_id)
        return board
