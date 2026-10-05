"""GTFS owns the board; RIS enriches explicitly mapped rail events only."""
from .gtfs import GTFSProvider
from .db_ris import DBRISProvider
from .time_window import visible_departures


class SupplementedProvider:
    def __init__(self, primary: GTFSProvider, ris: DBRISProvider, trip_map: dict[str, str]):
        self.primary = primary
        self.ris = ris
        self.trip_map = trip_map

    async def start(self):
        await self.primary.start()

    async def stop(self):
        await self.primary.stop()

    def dataset_version(self):
        return self.primary.dataset_version()

    def search(self, query):
        return self.primary.search(query)

    def nearby(self, lat, lon):
        return self.primary.nearby(lat, lon)

    async def static_board(self, stop_id, kind, now):
        return await self.primary.static_board(stop_id, kind, now)

    async def board(self, stop_id, kind, now):
        board = await self.primary.board_candidates(stop_id, kind, now)
        extra = await self.ris.fetch_board(board.stop, kind, now)
        if extra is not None:
            matched = False
            for departure in board.journeys:
                if departure.mode != "rail":
                    continue
                ris_id = self.trip_map.get(departure.trip_id)
                # A trip ID plus exact scheduled instant identifies the dated stop event.
                candidates = [d for d in extra.journeys if ris_id and d.trip_id == ris_id
                              and d.scheduled.timestamp() == departure.scheduled.timestamp()]
                if len(candidates) != 1:
                    continue
                supplement = candidates[0]
                if departure.realtime is None:
                    departure.realtime = supplement.realtime
                departure.cancelled = departure.cancelled or supplement.cancelled
                departure.platform = supplement.platform or departure.platform
                departure.operator = departure.operator or supplement.operator
                departure.alerts.extend(supplement.alerts)
                departure.source += " + DB RIS::Boards"
                matched = True
            board.alerts.extend(extra.alerts)
            if matched or extra.alerts:
                board.source += " + DB RIS::Boards"
        board.journeys = visible_departures(board.journeys, now)
        return board
