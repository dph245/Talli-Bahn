"""RIS::Boards v1 (OpenAPI 1.8.2), normalized at the adapter boundary.

Official specification:
https://developers.deutschebahn.com/db-api-marketplace/apis/product/ris-boards-transporteure/api/ris-boards-transporteure
"""
import asyncio
import logging
import time
from datetime import timedelta, timezone
from hashlib import sha256
from typing import Literal
import httpx
from pydantic import AwareDatetime, BaseModel, Field, ValidationError
from ..models import Board, Departure, ServiceAlert, Stop
from .base import TransitProvider
from .time_window import visible_departures

log = logging.getLogger(__name__)
BASE_URL = "https://apis.deutschebahn.com/db/apis/ris-boards/v1"
RAIL_TYPES = {"HIGH_SPEED_TRAIN", "INTERCITY_TRAIN", "INTER_REGIONAL_TRAIN", "REGIONAL_TRAIN", "CITY_TRAIN"}


class _Place(BaseModel):
    name: str


class _Transport(BaseModel):
    type: str
    journeyDescription: str
    destination: _Place | None = None
    differingDestination: _Place | None = None
    origin: _Place | None = None
    differingOrigin: _Place | None = None


class _Administration(BaseModel):
    operatorName: str | None = None


class _RISStop(BaseModel):
    journeyID: str
    departureID: str | None = None
    arrivalID: str | None = None
    timeSchedule: AwareDatetime
    time: AwareDatetime
    timeType: Literal["SCHEDULE", "PREVIEW", "REAL"]
    canceled: bool
    platform: str
    platformSchedule: str | None = None
    transport: _Transport
    administration: _Administration
    messages: list[dict] = Field(default_factory=list)
    disruptions: list[dict] = Field(default_factory=list)


def normalize_messages(messages: list[dict], disruptions: list[dict]) -> list[ServiceAlert]:
    texts = [message.get("text") or message.get("textShort") for message in messages]
    for disruption in disruptions:
        descriptions = disruption.get("descriptions") or {}
        description = descriptions.get("DE") or descriptions.get("de") or descriptions.get("EN") or next(iter(descriptions.values()), {})
        texts.append(description.get("text"))
    result = []
    for text in dict.fromkeys(t for t in texts if isinstance(t, str) and t.strip()):
        result.append(ServiceAlert(id="ris:" + sha256(text.encode()).hexdigest()[:16],
                                   header=text, source="DB RIS::Boards"))
    return result


def normalize_board(payload: dict, stop: Stop, kind, now) -> Board:
    rows = payload[kind]
    if not isinstance(rows, list):
        raise ValueError("RIS board must contain a list")
    departures = []
    for row in rows:
        record = _RISStop.model_validate(row)
        if record.transport.type not in RAIL_TYPES:
            continue
        identifier = record.departureID if kind == "departures" else record.arrivalID
        target = (record.transport.differingDestination or record.transport.destination) if kind == "departures" else (record.transport.differingOrigin or record.transport.origin)
        if not identifier or target is None:
            raise ValueError("Incomplete RIS board event")
        departures.append(Departure(
            id=f"ris:{identifier}", stop_id=stop.id, trip_id=record.journeyID,
            line=record.transport.journeyDescription, mode="rail", destination=target.name,
            operator=record.administration.operatorName, scheduled=record.timeSchedule,
            realtime=record.time if record.timeType in ("PREVIEW", "REAL") else None,
            cancelled=record.canceled, platform=record.platform or None,
            scheduled_platform=record.platformSchedule,
            alerts=normalize_messages(record.messages, record.disruptions), source="DB RIS::Boards"))
    return Board(stop=stop, kind=kind, journeys=visible_departures(departures, now), updated_at=now,
                 source="DB RIS::Boards", alerts=normalize_messages([], payload.get("disruptions") or []))


class DBRISProvider:
    """Station search delegates to GTFS; explicit station IDs map to EVA numbers."""
    def __init__(self, primary: TransitProvider, station_map: dict[str, str], client_id: str,
                 api_key: str, base_url: str = BASE_URL):
        self.primary = primary
        self.station_map = station_map
        self.headers = {"DB-Client-Id": client_id, "DB-Api-Key": api_key, "Accept": "application/vnd.de.db.ris+json"}
        self.base_url = base_url.rstrip('/')
        self.lock = asyncio.Lock()
        self.cache = {}

    def search(self, query):
        return self.primary.search(query)

    async def fetch_board(self, stop: Stop, kind, now) -> Board | None:
        eva = self.station_map.get(stop.id)
        if not eva:
            return None
        key = (stop.id, kind)
        async with self.lock:
            entry = self.cache.get(key)
            if entry and time.monotonic() - entry[0] < 10:
                return entry[1].model_copy(deep=True) if entry[1] else None
            result = None
            try:
                async with httpx.AsyncClient(timeout=8) as client:
                    response = await client.get(f"{self.base_url}/public/{kind}/{eva}", headers=self.headers, params={
                        "timeStart": (now.astimezone(timezone.utc) - timedelta(hours=2)).isoformat(),
                        "timeEnd": (now.astimezone(timezone.utc) + timedelta(hours=2)).isoformat(),
                        "includeStationGroup": "false", "includeMessagesDisruptions": "true",
                        "filterTransports": ','.join(sorted(RAIL_TYPES)), "languages": "DE", "maxViaStops": 0})
                    response.raise_for_status()
                if int(response.headers.get('Age', '0')) > 180:
                    raise ValueError("Stale RIS response")
                result = normalize_board(response.json(), stop, kind, now)
            except (httpx.HTTPError, ValidationError, ValueError, KeyError, TypeError, AttributeError):
                log.warning("RIS::Boards nicht verfügbar; primären Fahrplan beibehalten")
            # Bound memory to recently queried stations and suppress rapid retries on errors.
            cutoff = time.monotonic() - 10
            self.cache = {k: v for k, v in self.cache.items() if v[0] > cutoff}
            self.cache[key] = (time.monotonic(), result)
            return result.model_copy(deep=True) if result else None

    async def board(self, stop_id, kind, now):
        primary = await self.primary.board(stop_id, kind, now)
        return await self.fetch_board(primary.stop, kind, now) or primary
