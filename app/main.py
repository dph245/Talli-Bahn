from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Literal
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from .models import Board, Stop, NearbyStop, NearbyPosition
from .providers.base import TransitProvider
from .providers.factory import create_provider

STATIC = Path(__file__).parent / "static"


def create_app(provider: TransitProvider | None = None):
    if provider is None:
        provider = create_provider()
    @asynccontextmanager
    async def lifespan(application):
        if hasattr(provider, "start"):
            await provider.start()
        try:
            yield
        finally:
            if hasattr(provider, "stop"):
                await provider.stop()

    application = FastAPI(title="Talli · Abfahrtsmonitor", version="0.1.0", lifespan=lifespan)
    application.state.provider = provider

    async def version():
        getter = getattr(provider, 'dataset_version', None)
        return await run_in_threadpool(getter) if getter else None

    async def check_version(expected):
        current = await version()
        if expected != current:
            raise HTTPException(409, 'Fahrplandaten geändert. Bitte Haltestelle neu auswählen.')
        return current

    @application.get("/api/dataset")
    async def dataset():
        return {"version": await version()}

    @application.get("/api/stops", response_model=list[Stop])
    async def stops(q: str = Query(default="", max_length=120)):
        generation = await version()
        result = await run_in_threadpool(provider.search, q.strip())
        await check_version(generation)
        return [s.model_copy(update={'dataset_version': generation}) for s in result]

    @application.post("/api/stops/nearby", response_model=list[NearbyStop])
    async def nearby(position: NearbyPosition):
        search = getattr(provider, 'nearby', None)
        if search is None:
            raise HTTPException(503, 'Umgebungssuche nicht verfügbar')
        try:
            generation = await version()
            result = await run_in_threadpool(search, position.lat, position.lon)
            await check_version(generation)
            return [s.model_copy(update={'dataset_version': generation}) for s in result]
        except ValueError:
            raise HTTPException(503, 'Umgebungssuche nicht verfügbar') from None

    @application.get("/api/board", response_model=Board)
    async def board(stop_id: str = Query(min_length=1, max_length=300),
                    kind: Literal["departures", "arrivals"] = "departures",
                    realtime: bool = True,
                    dataset_version: str | None = Query(default=None, max_length=150)):
        try:
            generation = await version()
            if dataset_version is not None:
                await check_version(dataset_version)
            loader = provider.board if realtime else getattr(provider, "static_board", provider.board)
            result = await loader(stop_id, kind, datetime.now(ZoneInfo("Europe/Berlin")))
            await check_version(generation)
            return result.model_copy(update={'stop': result.stop.model_copy(update={'dataset_version': generation})})
        except KeyError:
            await check_version(generation)
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
