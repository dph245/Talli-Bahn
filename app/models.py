from datetime import datetime
import math
import re
import unicodedata
from typing import Literal
from pydantic import BaseModel, Field, computed_field

Mode = Literal["rail", "subway", "tram", "bus", "ferry", "other"]
BoardKind = Literal["departures", "arrivals"]


class Stop(BaseModel):
    id: str
    name: str
    dataset_version: str | None = None


class NearbyStop(Stop):
    distance_m: int


class NearbyPosition(BaseModel):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)


class ServiceAlert(BaseModel):
    id: str
    header: str
    description: str = ""
    source: str


def alert_content_key(alert: ServiceAlert):
    # Entity IDs and source names do not define the passenger-visible content.
    return tuple(" ".join(unicodedata.normalize("NFC", value).split())
                 for value in (alert.header, alert.description))


def train_number(value):
    # Do not turn line names, trip codes or opaque provider IDs into train numbers.
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value.strip()):
        return None
    return value.strip().lstrip("0") or "0"


class Departure(BaseModel):
    """Source-independent stop event, also used for arrivals.

    realtime=None is normal: use scheduled. Cancellation and platform updates
    are independent observations and do not invent a time prediction.
    Matching identifiers stay inside the backend and are excluded from JSON.
    """
    id: str
    stop_id: str
    trip_id: str = Field(default="", exclude=True)
    train_number: str | None = Field(default=None, exclude=True)
    sequence: int = Field(default=0, exclude=True)
    service_date: str = Field(default="", exclude=True)
    route_id: str = Field(default="", exclude=True)
    agency_id: str = Field(default="", exclude=True)
    route_type: int | None = Field(default=None, exclude=True)
    direction_id: int | None = Field(default=None, exclude=True)
    line: str
    mode: Mode
    destination: str
    operator: str | None = None
    scheduled: datetime
    realtime: datetime | None = None
    cancelled: bool = False
    platform: str | None = None
    scheduled_platform: str | None = None
    alerts: list[ServiceAlert] = Field(default_factory=list)
    source: str

    @computed_field
    @property
    def delay_minutes(self) -> int | None:
        if self.realtime is None:
            return None
        return math.ceil((self.realtime.timestamp() - self.scheduled.timestamp()) / 60)


class Board(BaseModel):
    stop: Stop
    kind: BoardKind
    journeys: list[Departure]
    updated_at: datetime
    source: str
    realtime_status: Literal["loading", "available", "unavailable"] | None = None
    demo: bool = False
    notice: str | None = None
    alerts: list[ServiceAlert] = Field(default_factory=list)
