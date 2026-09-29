from datetime import datetime
import math
from typing import Literal
from pydantic import BaseModel, Field, computed_field

Mode = Literal["rail", "subway", "tram", "bus", "ferry", "other"]
BoardKind = Literal["departures", "arrivals"]


class Stop(BaseModel):
    id: str
    name: str


class ServiceAlert(BaseModel):
    id: str
    header: str
    description: str = ""
    source: str


class Departure(BaseModel):
    """Source-independent stop event, also used for arrivals.

    realtime=None is normal: use scheduled. Cancellation and platform updates
    are independent observations and do not invent a time prediction.
    Matching identifiers stay inside the backend and are excluded from JSON.
    """
    id: str
    stop_id: str
    trip_id: str = Field(default="", exclude=True)
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
    demo: bool = False
    notice: str | None = None
    alerts: list[ServiceAlert] = Field(default_factory=list)
