"""Primary timetable plus optional realtime, normalized before returning."""
import asyncio
from pathlib import Path
from ..models import Board
from .gtfs_static import GTFSStaticProvider
from .gtfs_realtime import RealtimeFeed, apply_update, apply_alerts
from .time_window import visible_departures


class GTFSProvider(GTFSStaticProvider):
    def __init__(self, path: Path, realtime: RealtimeFeed):
        super().__init__(path)
        self.realtime = realtime

    async def board_candidates(self, stop_id, kind, now):
        (station, departures), snapshot = await asyncio.gather(
            asyncio.to_thread(self.scheduled, stop_id, kind, now), self.realtime.snapshot())
        for departure in departures:
            apply_update(departure, kind, snapshot.updates)
        alerts = apply_alerts(departures, snapshot.alerts, stop_id, now)
        source = "GTFS + GTFS-Realtime" if any(d.source != "GTFS" or d.alerts for d in departures) or alerts else "GTFS"
        return Board(stop=station, kind=kind, journeys=departures, updated_at=now, source=source, alerts=alerts)

    async def board(self, stop_id, kind, now):
        board = await self.board_candidates(stop_id, kind, now)
        board.journeys = visible_departures(board.journeys, now)
        return board
