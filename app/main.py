from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Literal
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from .models import Board, Stop
from .providers.base import TransitProvider
from .providers.factory import create_provider

STATIC = Path(__file__).parent / "static"


def create_app(provider: TransitProvider | None = None):
    application = FastAPI(title="Talli · Abfahrtsmonitor", version="0.1.0")
    if provider is None:
        provider = create_provider()
    application.state.provider = provider

    @application.get("/api/stops", response_model=list[Stop])
    async def stops(q: str = Query(default="", max_length=120)):
        return await run_in_threadpool(provider.search, q.strip())

    @application.get("/api/board", response_model=Board)
    async def board(stop_id: str = Query(min_length=1, max_length=300),
                    kind: Literal["departures", "arrivals"] = "departures"):
        try:
            return await provider.board(stop_id, kind, datetime.now(ZoneInfo("Europe/Berlin")))
        except KeyError:
            raise HTTPException(404, "Haltestelle nicht gefunden") from None

    @application.middleware("http")
    async def cache_control(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/") or request.url.path == "/sw.js":
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @application.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @application.get("/sw.js", include_in_schema=False)
    def worker():
        return FileResponse(STATIC / "sw.js", media_type="application/javascript")

    application.mount("/static", StaticFiles(directory=STATIC), name="static")
    return application


app = create_app()
