import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 2

SCHEMA = """
PRAGMA user_version=2;
CREATE TABLE stops (stop_id TEXT PRIMARY KEY, stop_name TEXT NOT NULL, search_name TEXT NOT NULL,
 parent_station TEXT, platform_code TEXT, location_type INTEGER);
CREATE INDEX stops_parent ON stops(parent_station);
CREATE INDEX stops_search ON stops(search_name);
CREATE TABLE agencies (agency_id TEXT PRIMARY KEY, agency_name TEXT NOT NULL);
CREATE TABLE routes (route_id TEXT PRIMARY KEY, route_short_name TEXT, route_long_name TEXT, route_type INTEGER, agency_id TEXT);
CREATE TABLE trips (trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT, trip_headsign TEXT, direction_id INTEGER);
CREATE TABLE stop_times (trip_id TEXT, stop_id TEXT, stop_sequence INTEGER, arrival INTEGER,
 departure INTEGER, stop_headsign TEXT, pickup_type INTEGER, drop_off_type INTEGER);
CREATE INDEX times_stop_departure ON stop_times(stop_id, departure);
CREATE INDEX times_stop_arrival ON stop_times(stop_id, arrival);
CREATE INDEX times_trip_sequence ON stop_times(trip_id, stop_sequence);
CREATE TABLE calendar (service_id TEXT PRIMARY KEY, monday INTEGER, tuesday INTEGER, wednesday INTEGER,
 thursday INTEGER, friday INTEGER, saturday INTEGER, sunday INTEGER, start_date TEXT, end_date TEXT);
CREATE TABLE calendar_dates (service_id TEXT, date TEXT, exception_type INTEGER);
CREATE INDEX calendar_dates_date ON calendar_dates(date);
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
"""


@contextmanager
def connect(path: Path):
    db = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()


def validate_schema(path: Path):
    with connect(path) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        raise RuntimeError("GTFS-Datenbankformat veraltet. Bitte GTFS-ZIP erneut mit python -m app.import_gtfs importieren.")
