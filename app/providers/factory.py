"""Application composition: configuration selects a provider, not API routes."""
import os
import json
import re
from pathlib import Path
from .base import TransitProvider
from .demo import DemoProvider
from .gtfs import GTFSProvider
from .gtfs_realtime import RealtimeFeed, DEFAULT_FEED_URL
from .db_ris import DBRISProvider
from .supplemented import SupplementedProvider
from .transport_rest import TransportRestProvider


def with_platforms(provider):
    if os.getenv("TRANSPORT_REST_ENABLED", "false").lower() == "true":
        return TransportRestProvider(provider)
    return provider


def create_provider() -> TransitProvider:
    path = Path(os.getenv("DATABASE_PATH", "data/gtfs.sqlite"))
    selected = os.getenv("TRANSIT_PROVIDER", "auto")
    if selected not in ("auto", "demo", "gtfs"):
        raise RuntimeError("TRANSIT_PROVIDER muss auto, demo oder gtfs sein")
    if selected == "gtfs" and not path.exists():
        raise RuntimeError("GTFS-Datenbank fehlt. Bitte zuerst python -m app.import_gtfs ausführen.")
    if selected != "demo" and path.exists():
        primary = GTFSProvider(path, RealtimeFeed(
            os.getenv("GTFS_RT_URL", DEFAULT_FEED_URL), os.getenv("GTFS_RT_TOKEN"),
            interval=int(os.getenv("GTFS_RT_INTERVAL_SECONDS", "60"))))
        if os.getenv("DB_RIS_ENABLED", "false").lower() != "true":
            return with_platforms(primary)
        client_id, api_key = os.getenv("DB_CLIENT_ID"), os.getenv("DB_API_KEY")
        if not client_id or not api_key:
            raise RuntimeError("DB_RIS_ENABLED benötigt DB_CLIENT_ID und DB_API_KEY")
        mapping_path = Path(os.getenv("DB_RIS_MAPPING_PATH", "data/ris-mapping.json"))
        mapping = json.loads(mapping_path.read_text())
        stations, trips = mapping.get("stations", {}), mapping.get("trips", {})
        if not isinstance(stations, dict) or not isinstance(trips, dict):
            raise ValueError("RIS-Mapping: stations und trips müssen Objekte sein")
        if any(not isinstance(eva, str) or not re.fullmatch(r"[0-9]{7}", eva) for eva in stations.values()):
            raise ValueError("RIS-Mapping: EVA-Nummern müssen siebenstellige Strings sein")
        if any(not isinstance(value, str) or not value for value in trips.values()):
            raise ValueError("RIS-Mapping: Fahrt-IDs müssen nichtleere Strings sein")
        ris = DBRISProvider(primary, stations, client_id, api_key)
        return with_platforms(SupplementedProvider(primary, ris, trips))
    return DemoProvider()
