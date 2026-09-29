"""Contract implemented by every selectable timetable source."""
from datetime import datetime
from typing import Protocol
from ..models import Board, BoardKind, Stop


class TransitProvider(Protocol):
    def search(self, query: str) -> list[Stop]: ...
    async def board(self, stop_id: str, kind: BoardKind, now: datetime) -> Board: ...
