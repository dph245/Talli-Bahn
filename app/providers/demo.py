from datetime import datetime, timedelta
from ..models import Board, BoardKind, Departure, Stop


class DemoProvider:
    stations = [Stop(id="demo-berlin", name="Berlin Hauptbahnhof"), Stop(id="demo-alex", name="Berlin Alexanderplatz"),
                Stop(id="demo-hamburg", name="Hamburg Hauptbahnhof"), Stop(id="demo-muenchen", name="München Hauptbahnhof")]

    def search(self, query):
        return [s for s in self.stations if query.casefold() in s.name.casefold()]

    async def board(self, stop_id, kind: BoardKind, now: datetime):
        stop = next((s for s in self.stations if s.id == stop_id), None)
        if stop is None:
            raise KeyError(stop_id)
        services = [
            ("S 7", "rail", "Ahrensfelde", "Potsdam Hbf", "15", 0),
            ("RE 1", "rail", "Frankfurt (Oder)", "Magdeburg Hbf", "11", 5),
            ("M10", "tram", "Warschauer Straße", "Turmstraße", "A", 0),
            ("ICE 804", "rail", "Hamburg-Altona", "München Hbf", "8", 0),
            ("U 5", "subway", "Hönow", "Hönow", "1", 0),
            ("120", "bus", "Märkisches Viertel", "U Leopoldplatz", "B", 3),
            ("S 5", "rail", "Strausberg Nord", "Westkreuz", "15", -1),
            ("RE 8", "rail", "Wismar", "Flughafen BER", "6", 0),
            ("M5", "tram", "Zingster Straße", "Landsberger Allee", "A", 2),
            ("ICE 597", "rail", "München Hbf", "Hamburg-Altona", "3", 12),
            ("245", "bus", "Zoologischer Garten", "Friedrichstraße", "C", 0),
            ("RB 23", "rail", "Flughafen BER", "Golm", "12", 0),
        ]
        anchor = now.replace(second=0, microsecond=0)
        journeys = []
        for i, (line, mode, destination, origin, platform, delay) in enumerate(services):
            scheduled = anchor + timedelta(minutes=3 + i * 3)
            predicted = i not in (7, 11)
            journeys.append(Departure(id=f"demo-{i}", trip_id=f"demo-{i}", stop_id=stop.id,
                line=line, mode=mode, destination=destination if kind == "departures" else origin,
                scheduled=scheduled, realtime=scheduled + timedelta(minutes=delay) if predicted else None,
                platform=platform, scheduled_platform=platform, cancelled=i == 6,
                operator="Beispielverkehr", source="Beispieldaten"))
        journeys.sort(key=lambda j: j.realtime or j.scheduled)
        return Board(stop=stop, kind=kind, journeys=journeys, updated_at=now, source="Beispieldaten", demo=True,
                     notice="Demomodus · Fiktive Verbindungen und Echtzeitwerte, nicht für Reisen verwenden.")
