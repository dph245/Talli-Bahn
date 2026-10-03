import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from ..database import connect, validate_schema
from ..models import Board, BoardKind, Departure, Stop
from .search import SEARCH_NAME_SQL, like_pattern, search_terms
from .nearby import NearbyCatalog

BERLIN = ZoneInfo("Europe/Berlin")


def service_start(day):
    # GTFS defines the service-day origin as local noon minus twelve elapsed hours.
    noon = datetime(day.year, day.month, day.day, 12, tzinfo=BERLIN)
    return noon.astimezone(timezone.utc) - timedelta(hours=12)


def mode_for(route_type):
    if route_type == 0 or 900 <= route_type < 1000:
        return "tram"
    if route_type == 1 or route_type in (400, 401, 402):
        return "subway"
    if route_type == 2 or 100 <= route_type < 200 or route_type in (300, 403, 404):
        return "rail"
    if route_type in (3, 11) or 700 <= route_type < 900:
        return "bus"
    if route_type == 4 or 1000 <= route_type < 1100 or route_type == 1200:
        return "ferry"
    return "other"


class GTFSStaticProvider:
    def __init__(self, path: Path):
        validate_schema(path)
        self.path = path
        self.nearby_catalog = NearbyCatalog(path)

    def nearby(self, lat, lon):
        return self.nearby_catalog.search(lat, lon)

    def search(self, query):
        terms = search_terms(query)
        phrase = ' '.join(terms)
        conditions = ' AND '.join(r"name LIKE ? ESCAPE '\'" for _ in terms) or '1=1'
        with connect(self.path) as db:
            rows = db.execute(f"""WITH stations AS (
                SELECT stop_id, stop_name, {SEARCH_NAME_SQL} AS name FROM stops
                WHERE location_type IN (0,1) AND (parent_station='' OR parent_station IS NULL)
                ) SELECT stop_id, stop_name FROM stations WHERE {conditions}
                ORDER BY CASE WHEN name = ? THEN 0 WHEN name LIKE ? ESCAPE '\\' THEN 1 ELSE 2 END,
                length(stop_name), stop_name, stop_id LIMIT 20""",
                (*('%' + like_pattern(term) + '%' for term in terms), phrase, like_pattern(phrase) + '%')).fetchall()
        return [Stop(id=r["stop_id"], name=r["stop_name"]) for r in rows]

    def scheduled(self, stop_id, kind, now, lookback_hours=2):
        with connect(self.path) as db:
            station = db.execute("SELECT * FROM stops WHERE stop_id=?", (stop_id,)).fetchone()
            if not station:
                raise KeyError(stop_id)
            children = db.execute("SELECT stop_id FROM stops WHERE parent_station=?", (stop_id,)).fetchall()
            ids = [stop_id, *(r[0] for r in children)]
            placeholders = ",".join("?" for _ in ids)
            maximum = int(db.execute("SELECT value FROM metadata WHERE key='max_time'").fetchone()[0])
            journeys = []
            time_column = "departure" if kind == "departures" else "arrival"
            boarding_column = "pickup_type" if kind == "departures" else "drop_off_type"
            for offset in range(-(maximum // 86400 + 1), 2):
                day = now.astimezone(BERLIN).date() + timedelta(days=offset)
                date = day.strftime("%Y%m%d")
                start = service_start(day)
                lower = int((now - timedelta(hours=lookback_hours) - start).total_seconds())
                upper = int((now + timedelta(hours=2) - start).total_seconds())
                if lower > maximum or upper < 0:
                    continue
                weekday = "monday tuesday wednesday thursday friday saturday sunday".split()[day.weekday()]
                # Resolve only the endpoint needed, and only when no headsign exists.
                endpoint_order = "ASC" if kind == "arrivals" else "DESC"
                endpoint = f"""(SELECT s2.stop_name FROM stop_times endpoint
                    JOIN stops s2 ON s2.stop_id=endpoint.stop_id
                    WHERE endpoint.trip_id=st.trip_id
                    ORDER BY endpoint.stop_sequence {endpoint_order} LIMIT 1)"""
                destination_sql = endpoint if kind == "arrivals" else (
                    f"COALESCE(NULLIF(st.stop_headsign,''), NULLIF(t.trip_headsign,''), {endpoint})")
                sql = f"""WITH active AS (
                    SELECT service_id FROM calendar WHERE {weekday}=1 AND start_date<=? AND end_date>=?
                    UNION SELECT service_id FROM calendar_dates WHERE date=? AND exception_type=1
                    EXCEPT SELECT service_id FROM calendar_dates WHERE date=? AND exception_type=2
                ) SELECT st.*, t.trip_headsign, r.route_short_name, r.route_long_name, r.route_type, r.route_id, r.agency_id,
                    a.agency_name, t.direction_id,
                    s.platform_code,
                    {destination_sql} AS destination
                FROM stop_times st JOIN trips t ON t.trip_id=st.trip_id
                JOIN routes r ON r.route_id=t.route_id LEFT JOIN agencies a ON a.agency_id=r.agency_id JOIN stops s ON s.stop_id=st.stop_id
                WHERE st.stop_id IN ({placeholders}) AND st.{time_column} BETWEEN ? AND ?
                AND st.{boarding_column}<>1 AND t.service_id IN (SELECT service_id FROM active)"""
                for r in db.execute(sql, (date, date, date, date, *ids, lower, upper)):
                    destination = r["destination"]
                    journeys.append(Departure(
                        id=f"{date}:{r['trip_id']}:{r['stop_sequence']}", trip_id=r["trip_id"],
                        stop_id=r["stop_id"], sequence=r["stop_sequence"], service_date=date,
                        route_id=r["route_id"], agency_id=r["agency_id"], route_type=r["route_type"],
                        direction_id=r["direction_id"], operator=r["agency_name"],
                        line=r["route_short_name"] or r["route_long_name"] or "–",
                        mode=mode_for(r["route_type"]), destination=destination or "Unbekannt",
                        scheduled=(start + timedelta(seconds=r[time_column])).astimezone(BERLIN),
                        platform=r["platform_code"] or None, scheduled_platform=r["platform_code"] or None, source="GTFS"))
        return Stop(id=station["stop_id"], name=station["stop_name"]), journeys

    async def static_board(self, stop_id, kind, now):
        station, journeys = await asyncio.to_thread(self.scheduled, stop_id, kind, now, 0)
        journeys = [j for j in journeys if now <= j.scheduled <= now + timedelta(hours=2)]
        journeys.sort(key=lambda j: j.scheduled)
        return Board(stop=station, kind=kind, journeys=journeys, updated_at=now,
                     source="GTFS")

    async def board(self, stop_id, kind, now):
        return await self.static_board(stop_id, kind, now)
