from datetime import datetime
from ..models import Departure

DEPARTURE_GRACE_SECONDS = 60
CANCELLATION_GRACE_SECONDS = 300


def display_time(departure: Departure) -> datetime:
    # A cancelled journey has no predicted departure; retain its timetable slot.
    return departure.scheduled if departure.cancelled else departure.realtime or departure.scheduled


def visible_departures(departures: list[Departure], now: datetime) -> list[Departure]:
    # Epoch comparisons also work across a repeated hour at the DST boundary.
    start = now.timestamp()
    visible = []
    for departure in departures:
        grace = CANCELLATION_GRACE_SECONDS if departure.cancelled else (
            DEPARTURE_GRACE_SECONDS if departure.realtime is not None else 0)
        if start - grace <= display_time(departure).timestamp() <= start + 7200:
            visible.append(departure)
    return sorted(visible, key=lambda d: display_time(d).timestamp())
