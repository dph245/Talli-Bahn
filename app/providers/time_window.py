from datetime import datetime
from ..models import Departure


def visible_departures(departures: list[Departure], now: datetime) -> list[Departure]:
    # Epoch comparisons also work across a repeated hour at the DST boundary.
    start = now.timestamp()
    visible = [d for d in departures if start <= (d.realtime or d.scheduled).timestamp() <= start + 7200]
    return sorted(visible, key=lambda d: (d.realtime or d.scheduled).timestamp())
