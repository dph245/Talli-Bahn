"""Stream a local GTFS ZIP into an atomically replaced, indexed SQLite database."""
import argparse
import csv
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid
from zipfile import ZipFile
from .database import SCHEMA
from .gtfs_quality import check_quality, QualityError


def seconds(value):
    if not value:
        return None
    h, m, s = map(int, value.split(":"))
    if h < 0 or not 0 <= m < 60 or not 0 <= s < 60:
        raise ValueError(f"Ungültige GTFS-Zeit: {value}")
    return h * 3600 + m * 60 + s


def rows(archive, name, required=False):
    if name not in archive.namelist():
        if required:
            raise ValueError(f"{name} fehlt im GTFS-Archiv")
        return
    with archive.open(name) as raw:
        yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))


def import_feed(archive_path: Path, db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="gtfs-", suffix=".sqlite", dir=db_path.parent)
    os.close(fd)
    db = sqlite3.connect(temporary)
    try:
        db.executescript(SCHEMA)
        with ZipFile(archive_path) as z, db:
            agencies = list(rows(z, "agency.txt", True))
            zones = {r.get("agency_timezone") for r in agencies}
            if zones != {"Europe/Berlin"}:
                raise ValueError("Dieser Deutschland-Import unterstützt agency_timezone=Europe/Berlin.")
            if "frequencies.txt" in z.namelist() and next(rows(z, "frequencies.txt"), None):
                raise ValueError("Taktbasierte frequencies.txt wird noch nicht unterstützt; Import abgebrochen.")
            agency_ids = {r.get("agency_id") or "default" for r in agencies}
            if len(agency_ids) != len(agencies):
                raise ValueError("agency_id muss eindeutig sein")
            db.executemany("INSERT INTO agencies VALUES (?,?)",
                           ((r.get("agency_id") or "default", r["agency_name"]) for r in agencies))
            def agency_for(route):
                agency = route.get("agency_id") or (next(iter(agency_ids)) if len(agency_ids) == 1 else "")
                if agency not in agency_ids:
                    raise ValueError("Route ohne gültige agency_id")
                return agency
            db.executemany("INSERT INTO stops VALUES (?,?,?,?,?,?)", (
                (r["stop_id"], r["stop_name"], r["stop_name"].casefold(), r.get("parent_station", ""),
                 r.get("platform_code", ""), int(r.get("location_type") or 0))
                for r in rows(z, "stops.txt", True)))
            db.executemany("INSERT INTO routes VALUES (?,?,?,?,?)", (
                (r["route_id"], r.get("route_short_name", ""), r.get("route_long_name", ""), int(r["route_type"]), agency_for(r))
                for r in rows(z, "routes.txt", True)))
            db.executemany("INSERT INTO trips VALUES (?,?,?,?,?)", (
                (r["trip_id"], r["route_id"], r["service_id"], r.get("trip_headsign", ""), int(r["direction_id"]) if r.get("direction_id") else None)
                for r in rows(z, "trips.txt", True)))
            # Optional passenger-facing train identity; older v2 imports remain readable.
            db.executemany("INSERT INTO trip_names VALUES (?,?)", (
                (r["trip_id"], r["trip_short_name"].strip())
                for r in rows(z, "trips.txt", True) if r.get("trip_short_name", "").strip()))
            db.executemany("INSERT INTO stop_times VALUES (?,?,?,?,?,?,?,?)", (
                (r["trip_id"], r["stop_id"], int(r["stop_sequence"]), seconds(r.get("arrival_time")),
                 seconds(r.get("departure_time")), r.get("stop_headsign", ""),
                 int(r.get("pickup_type") or 0), int(r.get("drop_off_type") or 0))
                for r in rows(z, "stop_times.txt", True)))
            days = "monday tuesday wednesday thursday friday saturday sunday".split()
            db.executemany("INSERT INTO calendar VALUES (?,?,?,?,?,?,?,?,?,?)", (
                (r["service_id"], *(int(r[d]) for d in days), r["start_date"], r["end_date"])
                for r in rows(z, "calendar.txt")))
            db.executemany("INSERT INTO calendar_dates VALUES (?,?,?)", (
                (r["service_id"], r["date"], int(r["exception_type"])) for r in rows(z, "calendar_dates.txt")))
            if not db.execute("SELECT 1 FROM calendar UNION ALL SELECT 1 FROM calendar_dates LIMIT 1").fetchone():
                raise ValueError("calendar.txt oder calendar_dates.txt muss Verkehrstage enthalten.")
            maximum = db.execute("SELECT MAX(MAX(COALESCE(arrival,0), COALESCE(departure,0))) FROM stop_times").fetchone()[0] or 86400
            db.execute("INSERT INTO metadata VALUES ('max_time', ?)", (str(maximum),))
            db.execute("INSERT INTO metadata VALUES ('source', ?)", (archive_path.name,))
            db.execute("INSERT INTO metadata VALUES ('import_id', ?)", (uuid.uuid4().hex,))
        db.execute("ANALYZE")
        db.close()
        report = check_quality(Path(temporary), db_path if db_path.exists() else None)
        os.replace(temporary, db_path)
        return report
    except BaseException:
        db.close()
        Path(temporary).unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--database", type=Path, default=Path(os.getenv("DATABASE_PATH", "data/gtfs.sqlite")))
    args = parser.parse_args()
    try:
        report = import_feed(args.archive, args.database)
    except QualityError as error:
        parser.exit(1, str(error) + '\n')
    print(report.render())
    print(f"GTFS importiert: {args.database}")


if __name__ == "__main__":
    main()
