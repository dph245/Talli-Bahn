"""Primary timetable plus optional realtime, normalized before returning."""
import asyncio
from pathlib import Path
from ..models import Board
from .gtfs_static import GTFSStaticProvider
from .gtfs_realtime import RealtimeFeed, apply_update, apply_alerts, matching_alert_entries
from .time_window import visible_departures


class GTFSProvider(GTFSStaticProvider):
    def __init__(self, path: Path, realtime: RealtimeFeed):
        super().__init__(path)
        self.realtime = realtime

    async def start(self):
        await self.realtime.start()

    async def stop(self):
        await self.realtime.stop()

    async def board_candidates(self, stop_id, kind, now):
        (station, departures), (snapshot, realtime_status) = await asyncio.gather(
            asyncio.to_thread(self.scheduled, stop_id, kind, now), self.realtime.current_snapshot())
        return await asyncio.to_thread(self.enrich, station, departures, snapshot, realtime_status, stop_id, kind, now)

    def enrich(self, station, departures, snapshot, realtime_status, stop_id, kind, now):
        for departure in departures:
            apply_update(departure, kind, snapshot.updates)
        alerts = apply_alerts(departures, matching_alert_entries(snapshot, departures, stop_id), stop_id, now)
        source = "GTFS + GTFS-Realtime" if any(d.source != "GTFS" or d.alerts for d in departures) or alerts else "GTFS"
        return Board(stop=station, kind=kind, journeys=departures, updated_at=now, source=source, alerts=alerts, realtime_status=realtime_status)

    async def board(self, stop_id, kind, now):
        board = await self.board_candidates(stop_id, kind, now)
        board.journeys = visible_departures(board.journeys, now)
        return board
