# Abfahrtsabfragen am Deutschland-Datensatz

Messung am 29.09.2026 mit `data/gtfs.sqlite`: laut Importstatistik 38.219.693
`stop_times`, 1.825.816 `trips`, 676.885 `stops`. Die Datenbank wird weiterhin
nur lesend geöffnet; kein erneuter Import und keine Indexmigration erforderlich.

## Befund und Query-Pläne

`EXPLAIN QUERY PLAN` der tatsächlich ausgeführten Abfahrts- und Ankunftsabfragen
für `204963` (Wolfenbüttel, Bahnhof, einschließlich untergeordneter Halte):

```text
SEARCH st USING INDEX times_stop_departure (stop_id=? AND departure>? AND departure<?)
SEARCH st USING INDEX times_stop_arrival (stop_id=? AND arrival>? AND arrival<?)
SEARCH t USING INDEX sqlite_autoindex_trips_1 (trip_id=?)
SEARCH endpoint USING INDEX times_trip_sequence (trip_id=?)
SEARCH calendar_dates USING INDEX calendar_dates_date (date=?)
```

Routen, Halte und Betreiber werden ebenfalls per Primärschlüssel gelesen.
Scans gibt es nur über `calendar` (2.299 Zeilen) und die daraus berechnete kleine
Menge aktiver Verkehrstage. Keine vollständigen Scans über `stop_times` oder
`trips`. Die vorhandenen Indizes sind passend; weitere große Indizes würden
hier unnötig Speicher und Importzeit kosten. Der Import führt bereits `ANALYZE` aus.

Bisher wurden für jede Fahrt sowohl Ursprung als auch Endhaltestelle abgefragt.
Jetzt wird bei Ankünften nur der Ursprung ermittelt; bei Abfahrten die
Endhaltestelle nur dann, wenn weder Stop- noch Trip-HeadSign vorhanden ist.
Die reine Fahrplanabfrage liest nur die nächsten zwei Stunden. Der Echtzeitpfad
behält den zweistündigen Rückblick, damit stark verspätete Fahrten sichtbar bleiben.

## Lokale Messwerte

20 Wiederholungen pro Variante, lokaler warmer Dateisystemcache, festes Zeitfenster
29.09.2026 12:00 Europe/Berlin. Verglichen wurden `scheduled`, Zeitfensterfilter
und Sortierung vor/nach der Änderung; jeweils dieselben 79 sichtbaren Ereignisse.
Keine Netzwerk-, HTTP- oder Browserzeit enthalten, kein Cold-Cache-Benchmark.

| Abfrage | Vorher, Median | Nachher, Median |
| --- | ---: | ---: |
| Abfahrten | 4,99 ms | 2,93 ms |
| Ankünfte | 4,87 ms | 3,26 ms |

Der erste untersuchende Abfahrtslauf mit EXPLAIN-Instrumentierung benötigte
19 ms für 150 Kandidaten im bisherigen Vierstundenfenster. Damit war SQLite
in diesem lokalen Test nicht die Ursache sekundenlanger Wartezeiten.

## Fahrplan und Echtzeit

Bisher wartete `/api/board` mit `asyncio.gather` auf Fahrplan **und** HTTP-Download
von GTFS-Realtime (HTTP-Timeout 8 Sekunden), bevor überhaupt eine Tafel zurückkam.

Der Browser ruft jetzt zuerst `/api/board?...&realtime=false` auf und rendert
unmittelbar dessen Antwort. Danach folgt `realtime=true`; die Antwort ersetzt
die Tafel einschließlich Prognosen, Ausfällen und ggf. verspäteten Fahrten.
Fehler im zweiten Abruf lassen den geladenen Fahrplan stehen. Haltestellenwechsel
brechen alte Abrufe ab; zusätzlich schützt die bestehende Anfragenummer vor
verspäteten Antworten. Ohne Parameter wird ebenfalls der Echtzeitcache genutzt.
Auch bei aktiviertem RIS-Zusatzprovider umgeht der erste Abruf alle Netzquellen.

GTFS-RT wird einmal pro Feedabruf in ein Dictionary mit `(trip_id, start_date)`
eingeordnet. Das Fahrtmatching benötigt keine SQLite-Abfragen; nur die Halte der
gefundenen Fahrt werden nach Sequenz bzw. Stop-ID geprüft. Der gemeinsame
30-Sekunden-Hintergrundworker und dessen Lock verhindern doppelte Downloads. Protobuf-
Parsing und Aufbau des deutschlandweiten Dictionaries laufen jetzt im Workerthread,
um den API-Eventloop währenddessen nicht zu blockieren. Frischeprüfung,
Verkehrstag, NO_DATA, Ausfälle und die Unterscheidung zwischen fehlender Prognose
und explizit null Minuten Verspätung bleiben erhalten.

Live-Downloadzeiten des externen Feeds wurden in dieser Messung nicht erfasst.
Die Entkopplung wurde mit gezielt blockierter bzw. fehlschlagender Echtzeit getestet.

## Prüfung

```sh
.venv/bin/pytest -q
.venv/bin/python tests/browser_smoke.py
```

41 Backendtests bestanden. Browserprüfung mit Chromium: statische Tafel während
offenem Echtzeitabruf bedienbar und bei Echtzeitfehler weiterhin sichtbar;
22 vollständige Zeilen bei 1920×1080, 34 bei 2560×1440 (Testtafel mit 40 Fahrten).
Suche, Favoriten, Filter, Ankünfte, Details, Hell/Dunkel, mobile Breite und Offline-
Anzeige wurden ebenfalls geprüft. Fehlende Prognosen heißen „Plan“.

## Nachprüfung des Livefeeds

Beim anschließenden Praxistest dauerte ein Feeddownload im Container 48,5 Sekunden
(29.383.169 Bytes, 51.759 Fahrtupdates, 63.463 Meldungen). Der bisherige gemeinsame
Browser-Timeout von 20 Sekunden brach die wartende Boardanfrage vorher ab. Ein
direkter Boardabruf lieferte nach 56,6 Sekunden sechs Prognosen für Wolfenbüttel.

Der Feed wird jetzt als gemeinsame Hintergrundaufgabe geladen. Boardanfragen
geben sofort den vorhandenen Cache oder `realtime_status=loading` zurück. Die
Oberfläche fragt während des Ladens nach zwei Sekunden erneut ab, auch wenn Auto
ausgeschaltet ist. Ein Browserabbruch beendet den Feeddownload nicht. Eine
Gesamtgrenze von 120 Sekunden begrenzt den Download; HTTP-Lesephasen bleiben auf
acht Sekunden begrenzt. Nur tatsächlich gescheiterte oder deaktivierte Abrufe
melden `unavailable`. Frischeprüfung gilt weiterhin auch für den Cache.

Meldungen erhalten beim Feedimport einen Index nach Haltestelle, Fahrt, Route,
Betreiber oder Verkehrsmittel. Nur passende Kandidaten und globale Meldungen
werden anschließend exakt auf Selektoren und Gültigkeit geprüft. Das vermeidet
den bisherigen Vergleich jeder lokalen Fahrt mit allen 63.000 Meldungen. Diese
Verarbeitung läuft ebenfalls außerhalb des API-Eventloops. Regressionstests
vergleichen indexierte Ergebnisse mit der vollständigen Selektorprüfung.

## Zentraler Upstream-Cache

Der FastAPI-Lifespan startet und beendet genau einen Feedworker im Backend.
Dieser lädt sofort beim Start, danach frühestens 30 Sekunden nach Ende des
vorigen Versuchs. Langsame Downloads überlappen nicht, ausgefallene Downloads
werden nicht durch Client-Anfragen erneut angestoßen. `current_snapshot` liest
nur den Cache, auch wenn dieser leer oder veraltet ist. Ein noch frischer Cache
bleibt bei einem fehlgeschlagenen Refresh nutzbar; die 180-Sekunden-Frischegrenze
bleibt wirksam. Der RIS-Zusatzprovider reicht den Lifecycle an den GTFS-Provider
weiter. Der Docker-Start legt einen Uvicorn-Worker explizit fest.

Regressionstests prüfen 30 Sekunden Mindestpause, automatisches Laden ohne
Clients, wiederholte und parallele reine Cachezugriffe sowie Shutdown-Cancellation.
