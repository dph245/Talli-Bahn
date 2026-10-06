# Talli · ÖPNV auf einen Blick

Minimalistischer Abfahrts- und Ankunftsmonitor mit FastAPI, SQLite und HTML/CSS/JavaScript. Eine Bahnhofstafel für Bus und Bahn, ohne Konten, Karte, Reiseplanung oder Tickets.

## Lokal starten

Python 3.11 oder neuer:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
uvicorn app.main:app --reload
```

Öffnen: http://localhost:8000. Ohne importierte Datenbank startet eine **deutlich gekennzeichnete Demo mit fiktiven Verbindungen**. Die Beispieldaten sind keine Reiseauskunft. API-Dokumentation: `/docs`.

Alternativ:

```bash
docker compose up --build -d
```

Mit Docker unter http://localhost:8080 öffnen. Der Host-Port ist über `HOST_PORT` konfigurierbar; im Container bleibt Port 8000:

```bash
HOST_PORT=8081 docker compose up --build -d
```

Für eine dauerhafte Einstellung `HOST_PORT=8081` in `.env` setzen (Vorlage: `.env.example`). Logs: `docker compose logs -f`; stoppen: `docker compose down`.

## Echte Fahrplandaten

1. Passenden GTFS-Deutschland-Datensatz als ZIP von [GTFS.de](https://www.gtfs.de/de/feeds/) herunterladen. Lizenz, Attribution, Gültigkeit und Nutzungsbedingungen des gewählten Feeds beachten. Ein deutschlandweiter Feed kann erhebliche Downloadzeit und lokalen Speicher benötigen.
2. ZIP lokal importieren:

   ```bash
   python -m app.import_gtfs /pfad/zum/latest.zip
   ```

3. Anwendung im GTFS-Modus starten:

   ```bash
   TRANSIT_PROVIDER=gtfs uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

Der Import liest CSV-Dateien gestreamt, erstellt Such- und Fahrplanindizes und ersetzt die Datenbank erst nach erfolgreichem Abschluss atomar. Ein fehlerhafter Import erhält den bisherigen Datenbestand. Erneute Imports aktualisieren laufende GTFS-Instanzen bei der nächsten Anfrage. Nach dem Wechsel von Demo zu GTFS die Anwendung neu starten. Beim Wechsel der Quelle eine passende Haltestelle neu auswählen; Favoriten sind an die IDs der Datenquelle gebunden.

Vor der Aktivierung prüft `app.gtfs_quality` die fertige Kandidaten-SQLite und,
falls vorhanden, die bisherige aktive SQLite ausschließlich lesend. EFA-Katalog,
Discovery-Cache und andere abgeleitete Dateien werden weder benötigt noch geändert.
IDs werden nur innerhalb einer Datenbank verwendet. Verglichen werden normalisierte
Agency-Namen, Linienbezeichnungen und Verkehrsmittel; gleich bezeichnete Routen
werden aggregiert. Leere Linienbezeichnungen liefern keine blockierenden Linienbefunde.

Die Qualitätsprüfung unterscheidet Warnungen von Ablehnungen:

- Auffällige Zeitfolge: mindestens acht verschiedene normalisierte Haltenamen,
  mindestens 80 % der Abfahrten mit derselben Zeit wie der erste Halt und mindestens
  sechs aufeinanderfolgende identische Zeiten. Fehlende Zeiten zählen nicht als Null.
  Einzelne solche Trips erzeugen nur Warnungen.
- Ein Zeitmuster blockiert erst ab 50 Trips und mindestens 10 % einer Linie;
  jeder gezählte Trip muss mindestens acht verschiedene Halte mit dieser Startzeit
  und regulär erlaubtem Ein- **und** Ausstieg besitzen. Der gute Referenzfeed enthält
  Bedarfsverkehre mit langen gleichen Zeitfolgen und einseitigem Ein-/Ausstieg;
  solche Muster sind allein kein Ablehnungsgrund. Auch nach dieser Einschränkung
  enthält die gute Referenz bei mindestens 10 % Anteil Linien mit bis zu 37 auffälligen Trips;
  deshalb wurde die zunächst vorgeschlagene Schwelle von 20 auf 50 kalibriert.
- Fahrtverkürzung: Warnung ab 25 % weniger Haltereignissen insgesamt und je Trip;
  starkes Signal ab 40 %. Voraussetzung: vorher mindestens 20 Trips und weiterhin
  mindestens 40 % der bisherigen Tripanzahl.
- Gruppenverlust: Warnung ab 5 % und 50 Gruppen, starkes Signal ab 10 % und 100.
  Gruppen werden nach Agency und gemeinsam für die bestehende EFA-Agency-Auswahl
  gezählt, **vor** dem Koordinatenfilter des EFA-Katalogs. SQLite enthält keine Koordinaten.
  Ein starkes Signal verlangt auch den entsprechenden Verlust verschiedener
  normalisierter Gruppennamen; bloße Parent-Zusammenlegungen genügen nicht.
- Gemeinsam blockieren starke Fahrtverkürzung und Gruppenverlust derselben Agency,
  wenn beide zusätzlich im gemeinsamen Kalenderfenster bestätigt werden. Verwendet
  werden die ersten maximal 14 Tage der überlappenden Kalenderhorizonte, einschließlich
  `calendar_dates`; weniger als sieben gemeinsame Tage erlauben nur Vergleichswarnungen.
  Trip-/Ereigniszahlen im Fenster zählen tatsächliche Betriebstagvorkommen.

Der CLI-Bericht zeigt Entscheidung, Referenz, Gruppenzahlen, Änderungen und höchstens
fünf Tripbeispiele; weitere Befunde werden begrenzt ausgegeben und gezählt. Warnungen
erlauben die Aktivierung. Ablehnung oder technischer Prüffehler entfernen die temporäre
Datenbank, liefern einen Fehlerstatus und lassen den bisherigen Bestand unverändert.
Ohne Referenz laufen nur die Zeitmusterprüfungen. Es gibt keine automatische Reparatur.

Synthetische Tests: `.venv/bin/pytest -q tests/test_gtfs_quality.py`.
Der optionale Integrationstest importiert beide lokalen Referenz-ZIPs in ein isoliertes
temporäres Verzeichnis unter `data/` (rund 10 GB Platzbedarf), prüft die Ablehnung des
03.10. und den unveränderten SHA-256 der zuvor akzeptierten Datenbank:
`TALLI_GTFS_SNAPSHOT_TESTS=1 .venv/bin/pytest -q -s tests/test_gtfs_quality.py -k real_snapshot`.

Unterstützt: `stops`, `routes`, `trips`, `stop_times`, `calendar`, `calendar_dates`, Stationsgruppen, Gleis/Steig, Ein-/Ausstiegsverbote, Ankunfts-/Abfahrtszeiten, Verkehrstage, Tagesausnahmen, Zeiten über 24:00 und die GTFS-Servicezeitdefinition an Zeitumstellungstagen. Ziel bei Abfahrt ist der Haltestellen-/Fahrtzieltext, bei Ankunft die Starthaltestelle der Fahrt.

Der Import ist auf `Europe/Berlin` ausgerichtet. Taktbasierte `frequencies.txt` werden mit einer verständlichen Fehlermeldung abgelehnt. Unbestimmte Zwischenzeiten werden nicht interpoliert und erscheinen deshalb nicht in der Tafel. Der vollständige Deutschland-Datensatz wurde hier nicht heruntergeladen oder auf Importdauer/Platzbedarf vermessen.

## Datenstrategie und Echtzeit

GTFS Deutschland ist die primäre Basis für Haltestellen, Linien, Fahrten, Sollzeiten und Betreiber (`agency.txt` + `routes.agency_id`). **Nach diesem Schema-Update bestehende GTFS-Datenbanken erneut importieren**; alte Datenbanken werden mit einem verständlichen Fehler abgelehnt. Der atomare Import erhält bis zum Abschluss den bisherigen Datenbestand.

Standard-Echtzeitquelle ist `https://realtime.gtfs.de/realtime-free.pb`. Der [GTFS.de-Feed](https://www.gtfs.de/de/realtime/) enthält TripUpdates und ServiceAlerts, passt zu den dort angebotenen statischen Feeds und wird alle **10 Sekunden** erneuert. Ein zentraler Hintergrundworker lädt den Feed beim Backend-Start und wartet nach jedem abgeschlossenen Abruf standardmäßig **60 Sekunden** vor dem nächsten Versuch (`GTFS_RT_INTERVAL_SECONDS`, mindestens 30). Bei aufeinanderfolgenden Fehlern steigt die Pause auf 120, 240, 480 und maximal 900 Sekunden; eine längere `Retry-After`-Vorgabe bei HTTP 429/503 wird eingehalten. Nach einem erfolgreichen Abruf gilt wieder das normale Intervall. HTTP-Status und Wartezeit erscheinen im Fehlerlog. Der Worker läuft auch ohne offene Tafel. Client-Anfragen lesen ausschließlich den Cache und lösen niemals Upstream-Abrufe aus. Die Oberfläche kann weiterhin alle zehn Sekunden aktualisieren. Der Container startet explizit mit einem Uvicorn-Worker, damit alle Clients denselben Cache und Abrufprozess verwenden. Mehrere Backend-Replikate würden einen separaten, gemeinsam genutzten Cache/Collector benötigen.

```bash
TRANSIT_PROVIDER=gtfs uvicorn app.main:app
```

`GTFS_RT_URL` überschreibt die Quelle; ein explizit leerer Wert deaktiviert sie. Bei anderen statischen Datenbeständen muss ein Feed mit passenden Fahrt-/Haltestellen-IDs verwendet werden. `GTFS_RT_TOKEN` setzt optional einen Bearer-Header im Backend.

Alle Adapter liefern dasselbe interne `Departure`-Objekt (auch für Ankünfte). Provider-Rohdaten, interne Zuordnungsfelder und Zugangsdaten gelangen nicht ins Frontend. Beispiel der normalisierten API-Daten:

```json
{
  "id": "20260930:fahrt:2",
  "stop_id": "haltestelle",
  "line": "RE 1",
  "mode": "rail",
  "destination": "Frankfurt (Oder)",
  "operator": "Beispielbetreiber",
  "scheduled": "2026-09-30T12:00:00+02:00",
  "realtime": null,
  "delay_minutes": null,
  "cancelled": false,
  "platform": "7",
  "scheduled_platform": "7",
  "alerts": [],
  "source": "GTFS"
}
```

**`realtime: null` ist ein normaler Zustand.** Die Tafel zeigt `scheduled` mit „Nach Fahrplan“. Eine explizite Prognose mit null Minuten Verspätung wird als Zeitstempel übertragen und als pünktlich dargestellt. Ausfall, Gleiswechsel und Verkehrsmeldungen sind unabhängig von einer Zeitprognose. Leere/fehlende Echtzeitfeeds, Timeouts und veraltete Daten verhindern die Fahrplananzeige nicht und erzeugen keine Fehlermeldung in der Oberfläche; technische Abruffehler erscheinen nur im Backend-Log.

Der Adapter unterstützt Protobuf-FULL_DATASET-Snapshots, Ankunfts-/Abfahrtsprognosen als Zeitstempel oder Verspätung, explizite Fahrtverspätungen, Fahrt-Ausfälle und ausgelassene Halte. Bei Abruffehlern bleiben Echtzeitdaten aus dem Cache bis zu fünf Minuten nutzbar. Maßgeblich sind die Feed-/Trip-Zeitstempel, nicht der letzte Abrufversuch; älter als 300 Sekunden werden sie nicht verwendet. Danach zeigt die Tafel wieder Sollzeiten. Ein Feed ohne Zeitstempel wird ignoriert.

ServiceAlerts werden nach Haltestelle, Linie/Route, Betreiber, Verkehrsmittel und Fahrt/Verkehrstag/Richtung zugeordnet. Kriterien innerhalb eines Selektors gelten gemeinsam; mehrere Selektoren alternativ. Gültigkeitszeiträume werden berücksichtigt, deutsche Texte bevorzugt. Die Oberfläche zeigt nur normalisierte Meldungstexte und führt kein HTML aus Quellen aus.

Grenzen: keine DIFFERENTIAL-Feeds, zusätzlichen/ersetzten GTFS-RT-Fahrten, GTFS-RT-Gleiswechsel oder Ableitung von Prognosen aus vorherigen StopTimeUpdates. Die Fahrplanabfrage betrachtet Sollzeiten bis zwei Stunden vor jetzt und zeigt Ereignisse bis zwei Stunden in der Zukunft. Noch länger verspätete Fahrten können fehlen. Die Echtzeitintegration ist mit synthetischen Protobuf-Feeds getestet; eine produktive Abnahme mit vollständig importiertem Deutschland-Feed steht aus.

## Optional: DB RIS::Boards für Bahn

`app/providers/db_ris.py` implementiert die offiziellen Endpunkte `/public/departures/{evaNumbers}` und `/public/arrivals/{evaNumbers}` anhand der [RIS::Boards-OpenAPI 1.8.2](https://developers.deutschebahn.com/db-api-marketplace/apis/product/ris-boards-transporteure/api/ris-boards-transporteure). Sollzeit, Prognose (`timeType=PREVIEW/REAL`), aktuelles/Soll-Gleis, Ausfälle, Betreiber, Freitexte und Störungsinformationen werden in `Departure`/`ServiceAlert` normalisiert. `timeType=SCHEDULE` bleibt `realtime: null`.

Die zusätzliche Quelle ist standardmäßig deaktiviert. Benötigt werden ein entsprechender DB-API-Zugang sowie eine explizite Zuordnung der GTFS-Stations-IDs zu EVA-Nummern und der GTFS-Fahrt-IDs zu RIS-Journey-IDs. Namen werden nicht als Identitätsnachweis verwendet.

Beispiel für `data/ris-mapping.json` (Platzhalter durch geprüfte IDs ersetzen):

```json
{
  "stations": {"gtfs-stations-id": "8011160"},
  "trips": {"gtfs-trip-id": "ris-journey-id"}
}
```

In `.env`:

```dotenv
DB_RIS_ENABLED=true
DB_CLIENT_ID=deine-client-id
DB_API_KEY=dein-api-key
DB_RIS_MAPPING_PATH=data/ris-mapping.json
```

Danach `docker compose up --build -d` bzw. Uvicorn mit `--env-file .env` neu starten. Die Zugangsdaten bleiben im Backend. GTFS behält Haltestellensuche, Sollzeiten und Betreiber. RIS ergänzt ausschließlich eindeutig zugeordnete Bahn-Ereignisse mit identischer Sollzeit; vorhandene GTFS-RT-Prognosen haben Vorrang. Ausfallhinweise aus beiden Quellen werden beibehalten, aktuelle RIS-Gleise und Meldungen ergänzt. Nicht zugeordnete RIS-Fahrten werden nicht zusätzlich angehängt, damit keine Doppelanzeigen entstehen. Ein RIS-Ausfall lässt GTFS unverändert nutzbar. Stationsmeldungen werden auch ohne Fahrtzuordnung übernommen.

`DBRISProvider` liefert außerdem eigenständig normalisierte Bahn-Tafeln über die gemeinsame Provider-Schnittstelle; die produktive Factory verwendet ihn als Ergänzung. Kein RIS-Zugang oder produktives ID-Mapping ist im Repository enthalten. Der Adapter ist anhand synthetischer Antworten getestet, noch nicht mit authentifizierten Live-Anfragen abgenommen. Die ID-Zuordnung muss mit Feed-Updates gepflegt werden.

## Oberfläche

„In meiner Nähe“ im Suchfeld fragt den Browserstandort ausschließlich nach
einem bewussten Klick einmalig ab. Bis zu fünf Haltestellengruppen im Umkreis
von zwei Kilometern erscheinen in der bestehenden Ergebnisliste, sortiert nach
Luftlinie zur Gruppenposition. Die Auswahl öffnet die normale Tafel.
Es gibt keine automatische Standortabfrage beim Start und keine laufende Ortung.
Die Position wird nicht im Browser gespeichert; sie wird per POST an den eigenen
Server gesendet, dort nur für die Suche verwendet und nicht in die Anfrage-URL
geschrieben. Es werden keine Karten-, Geocoding- oder EFA-Dienste dafür aufgerufen.

Voraussetzung ist der vorhandene, zur GTFS-Datenbank passende VRB-Katalog
(`vrb-stops.json`, optional `VRB_EFA_CATALOG_PATH`). Die Funktion ist unabhängig
von `VRB_EFA_ENABLED` und benötigt keine bestätigten EFA-Mappings. Ihre Abdeckung
ist auf den Katalog beschränkt; die Namenssuche durchsucht weiterhin den gesamten
GTFS-Bestand. Auch ältere Kataloge mit Gruppenkoordinaten sind geeignet.
Bei fehlendem/veraltetem Katalog, verweigerter Standortfreigabe oder einem Timeout
bleibt die Namenssuche verfügbar. Im Demo-Modus gibt es keine Umgebungstreffer.
Browser-Geolocation benötigt HTTPS beziehungsweise localhost.

Die lokale API `POST /api/stops/nearby` erwartet `{"lat":52.16,"lon":10.54}`
und liefert Einträge mit `id`, `name` und `distance_m`. Koordinaten außerhalb der
gültigen Bereiche werden abgelehnt. Die Antworten werden nicht gecacht.
Browserprüfung mit simuliertem Standort: `PYTHONPATH=. python tests/browser_nearby.py`.

- Haltestellensuche mit Namensteilen in beliebiger Reihenfolge (z. B. `hbf braun`), Umlauten oder `ae/oe/ue` und `Hbf`/`Hauptbahnhof`; exakte Treffer zuerst, maximal 20 Ergebnisse. Tastaturbedienung: `/` öffnet die Suche.
- Favoriten, Design und Auto-Refresh-Einstellung in `localStorage`
- Abfahrten/Ankünfte, alle Verkehrsmittel auf einer Tafel
- Sollzeit, Prognose, Verspätung, Ausfall, Linie, Ziel/Herkunft und Gleis/Steig
- Filter nach Verkehrsmittel, Linie und Richtung/Herkunft
- Automatische Aktualisierung alle 10 Sekunden; pausiert im Hintergrund
- Responsive Dark-/Light-Ansicht, lokale Assets ohne externe Fonts/Tracker
- PWA-Manifest und Service Worker für die Offline-App-Hülle

Die API und Fahrplandaten werden **nicht offline gecacht**. Beim Netzverlust bleiben bereits geladene Daten nur im aktuellen Tab sichtbar und werden als veraltet markiert. Nach einem Offline-Neustart gibt es keine Fahrplandaten. Service Worker benötigen HTTPS oder localhost. Installationsunterstützung unterscheidet sich je nach Browser.

## Konfiguration

| Variable | Standard | Bedeutung |
| --- | --- | --- |
| `HOST_PORT` | `8080` | Veröffentlichter Port bei Docker Compose |
| `TRANSIT_PROVIDER` | `auto` | `auto`, `demo` oder `gtfs`; `auto` verwendet eine vorhandene Datenbank |
| `DATABASE_PATH` | `data/gtfs.sqlite` | Pfad zur SQLite-Datenbank |
| `GTFS_RT_URL` | `https://realtime.gtfs.de/realtime-free.pb` | TripUpdates + ServiceAlerts; leer deaktiviert Echtzeit |
| `GTFS_RT_TOKEN` | leer | Optionales Bearer-Token, bleibt im Backend |
| `DB_RIS_ENABLED` | `false` | Ergänzende RIS-Bahn-Daten aktivieren |
| `DB_CLIENT_ID` / `DB_API_KEY` | leer | DB-API-Zugangsdaten |
| `DB_RIS_MAPPING_PATH` | `data/ris-mapping.json` | Explizite Stations- und Fahrtzuordnung |

`.env.example` ist eine Vorlage; bei lokalem Start Variablen exportieren oder Uvicorn mit `--env-file .env` verwenden. Compose liest `.env` selbst. Beim Docker-Betrieb wird `./data` schreibgeschützt eingebunden; Importe erfolgen auf dem Host. Für öffentliche Bereitstellung HTTPS per Reverse Proxy konfigurieren.

## Architektur & Erweiterung

`app/providers/base.py` definiert das `TransitProvider`-Protokoll für Haltestellensuche und Tafel. Alle Provider liefern die gemeinsamen Modelle aus `app/models.py`: `Stop`, `Board`, `Departure` und `ServiceAlert`. `Departure` ist das normalisierte Haltereignis für Abfahrten und Ankünfte. Die API-Liste heißt weiterhin `journeys`; ihre Einträge entsprechen ausschließlich dem gemeinsamen `Departure`-Schema.

```text
app/providers/
    base.py           # TransitProvider-Schnittstelle
    factory.py        # Auswahl und Konfiguration der Quelle
    demo.py           # Fiktive Beispieldaten
    gtfs_static.py    # SQLite-Fahrplan, auch eigenständig nutzbar
    gtfs_realtime.py  # Echtzeit-Abruf und Prognosezuordnung
    gtfs.py           # Statischen Fahrplan und Echtzeit zusammenführen
    db_ris.py         # RIS::Boards → Departure
    supplemented.py   # Explizit zugeordnete RIS-Daten ergänzen

         ↓ Stop / Departure / ServiceAlert / Board
       FastAPI
         ↓
      Webfrontend
```

FastAPI und Oberfläche kennen keine quellenspezifische Logik. `factory.py` stellt den konfigurierten Provider zusammen; Tests oder andere Anwendungen können ihn über `create_app(provider=...)` direkt einsetzen. GTFS-Realtime ergänzt den statischen Fahrplan und ist keine eigenständige Haltestellensuche.

`transport_rest.py` ist nicht implementiert. Ein weiterer Adapter kann dasselbe Protokoll erfüllen und in der Factory ergänzt werden.

- `app/import_gtfs.py`: ZIP → SQLite
- `app/providers/`: Schnittstelle, Quellenauswahl, Fahrplan- und Echtzeitadapter
- `app/main.py`: HTTP-API und statische Assets
- `app/static/`: Frontend und PWA-Hülle
- `tests/test_transit.py`: Fahrplan-, Echtzeit-, Import- und API-Tests

## Tests

```bash
pytest -q
```

Optionaler Browser-Test (installiertes Chromium/Google Chrome oder `playwright install chromium`):

```bash
pip install playwright
PYTHONPATH=. python tests/browser_smoke.py
```

Prüft Suche, gespeicherte Favoriten, Filter, Ankünfte, Designwechsel, mobile Breite und Offline-/Online-Wechsel; Screenshots werden unter `/tmp/talli-desktop.png` und `/tmp/talli-mobile.png` abgelegt.

Referenzen: [GTFS Schedule](https://gtfs.org/documentation/schedule/reference/), [GTFS Realtime](https://gtfs.org/documentation/realtime/reference/), [FastAPI Static Files](https://fastapi.tiangolo.com/tutorial/static-files/).

### Kompakte Tafel und Ladeverhalten

Die [konservative Echtzeitprüfung vom 30.09.2026](docs/realtime-check-2026-09-30.md)
dokumentiert gemessene Feedgrößen, HTTP-Validatoren und regionale Alternativen.
Der zentrale Worker nutzt Conditional GETs; auch ein 304 verlängert die
Fünf-Minuten-Frischegrenze nicht. Prognosen bestimmen Sichtbarkeit und Sortierung
mit 60 Sekunden Karenz, Ausfälle bleiben fünf Minuten nach Sollzeit sichtbar.
Beim Refresh derselben Echtzeittafel bleibt diese bis zur neuen Antwort stehen;
bei einem Fehler erscheint der bereits geladene Sollfahrplan.

Die Oberfläche lädt den Fahrplan über `/api/board?stop_id=…&realtime=false`
und ergänzt Echtzeit anschließend über `realtime=true`. Ohne den Parameter
wird ebenfalls Echtzeit aus dem Feedcache ergänzt. Während der erste Feedabruf
läuft, meldet die API `realtime_status=loading`; die Oberfläche fragt automatisch
erneut nach, auch bei ausgeschalteter periodischer Aktualisierung. Jede Fahrt belegt eine kompakte
Tabellenzeile; ein Klick auf das Ziel öffnet die Details. „Plan“ bedeutet, dass
keine Echtzeitprognose vorhanden ist.

Query-Pläne, Messwerte am Deutschland-Datensatz und Testumfang stehen in
[docs/performance.md](docs/performance.md). Ein erneuter GTFS-Import ist für diese
Änderung nicht nötig.

### Ergänzende Gleise über transport.rest

`TRANSPORT_REST_ENABLED=true` aktiviert die öffentliche
[transport.rest-API](https://v6.db.transport.rest/api.html) ohne API-Schlüssel.
Docker Compose aktiviert die Ergänzung standardmäßig; bei direktem Uvicorn-Start
muss die Variable gesetzt werden. Mit `false` lässt sie sich abschalten.

GTFS bleibt die Fahrplanquelle. Die Ergänzung gilt für Bahn-Abfahrten und
-Ankünfte, wenn der Stationsname eindeutig aufgelöst werden kann und Linie,
exakter Sollzeitpunkt sowie Ziel/Herkunft in beiden Quellen eindeutig passen.
Lediglich Leerzeichen in Liniennamen und doppelte Ortspräfixe wie
„Hamburg, Hamburg Hbf“ werden normalisiert. Abweichende Namen, fehlende Herkunft,
mehrdeutige Fahrten und andere Haltestellen bleiben unberücksichtigt.
Aktuelles und geplantes Gleis werden übernommen; RIS-Angaben haben Vorrang.
Zeiten, Ausfälle und die Liste der Fahrten bleiben unverändert.

Abrufe und Fehler werden je Bahnhof und Tafel 60 Sekunden zwischengespeichert;
der Abruf hat ein Gesamtzeitlimit von acht Sekunden. Die erste statische Tafel
wartet nicht darauf. Bei Fehlern oder fehlenden Gleisen bleiben die bisherigen
Daten sichtbar. Die Quelle garantiert somit keine vollständige Gleisabdeckung.
Beim Integrationstest lieferte der öffentliche Live-Endpunkt HTTP 503; die
Zuordnung und Fehlerbehandlung sind mit synthetischen API-Antworten getestet.

### Optionale VRB-EFA-Echtzeit

`VRB_EFA_ENABLED=true` aktiviert die Ergänzung (direkter Start standardmäßig
`false`; Compose aktiviert sie). GTFS bleibt alleinige Fahrplanquelle.
Die frühere Beschränkung auf vier Steige wurde durch eine Mapping-Schicht ersetzt.

Vorbereitung für das gesamte im Feed erfasste VRB-/Regionalbus-Angebot:

```bash
.venv/bin/python -m app.prepare_efa_mapping latest_geamt.zip
```

Das erzeugt `data/vrb-stops.json` rein lokal aus SQLite und GTFS-Koordinaten.
Nach einem neuen GTFS-Import erneut ausführen und den Server neu starten.
Der aktuelle Katalog umfasst 4.639 Gruppen. Die Agency-Auswahl ist über
`--agency` konfigurierbar; sie ist keine administrative Gebietsgrenze.
Ohne Katalog bleiben zuvor verifizierte Mappings als Startdaten verfügbar.

Eine tatsächlich angefragte Gruppe wird bei Bedarf einmal über den EFA-Stopfinder
aufgelöst. Nur gleicher Ort, gleicher Name und höchstens 100 m Abstand ergeben
bei genau einem Kandidaten `UNIQUE`. Abkürzungen/Mast-Zusätze bleiben unbestätigt.
Rohantworten einschließlich negativer Ergebnisse werden persistent gespeichert.
Compose verwendet dafür das Volume `efa-cache`; der GTFS-Datenmount bleibt read-only.
Bei direktem Start sind `VRB_EFA_CATALOG_PATH` und `VRB_EFA_CACHE_PATH` optional
konfigurierbar (Standard: neben der GTFS-SQLite).

Tafelanfragen warten nicht auf EFA. Ein gemeinsamer Hintergrundworker führt
Discovery und Abfahrtsabfragen sequenziell mit mindestens 1,1 s Abstand aus;
gleichzeitige Anfragen derselben Gruppe teilen einen Task. Der Echtzeitcache
wird bei tatsächlicher Nutzung frühestens nach 60 s erneuert und verfällt nach
fünf Minuten. Kein permanentes Polling. Ein Prozess wie in Compose vorgesehen.

Der Departure Monitor erhält die eindeutig zugeordnete Haltestellen-DHID und
liefert auch Events ihrer explizit zugeordneten Steige. Das Fahrtenmatching bleibt
bei aktiven GTFS-Betriebstagen, Linie und exakter Sollzeit; bei Mehrdeutigkeit
muss der haltestellenspezifische Zielvergleich genau einen Kandidaten ergeben.
Keine Zeittoleranz, kein Fuzzy-Matching, keine Gleichsetzung von EFA- und GTFS-Fahrt-IDs.
GTFS-RT-Prognosen und vorhandene Steigwerte behalten Vorrang. Fehlende Plattformen
können aus demselben gematchten Event ergänzt werden (`platformName`, sonst `platform`).
Keine zusätzlichen Fahrten, keine Änderung an Ankünften oder `realtime=false`.

Methodik, bekannte Grenzen, bisherige Evaluation und die gezielt gespeicherten
Antworten für Bahnhof/Campestraße stehen in [docs/efa-discovery](docs/efa-discovery/README.md).
Die bekannte 96-Haltestellen-Evaluation wurde nicht wiederholt. Das EFA-Limit
von 20 Events begrenzt weiterhin die Echtzeitabdeckung großer Haltestellen.

### Prüfung der Realtime-Identitäten (05.10.2026)

Der [RB43-Feldtest und die Untersuchung](docs/rb43-2026-10-05/README.md) belegen
inkompatible Fahrt-IDs zwischen lokalem Import und aktuellem GTFS-RT-Snapshot.
Talli prüft deshalb vor der Übernahme eines Snapshots dessen Fahrtverläufe,
optionale Route/Richtung und rekonstruierbare Sollzeiten gegen GTFS. Bei einem
Widerspruch werden Prognosen, Ausfälle und Alerts dieses Snapshots gemeinsam
verworfen und der konkrete Konflikt geloggt. Fahrplan und unabhängige EFA-Daten
bleiben verfügbar. Statischer Import und Realtime-Quelle müssen zusammenpassen;
ein Neuimport aus dem alten ZIP behebt neu belegte IDs nicht.

Optionales numerisches `trip_short_name` bei Bahnfahrten wird bei neuen Imports
bewahrt und mit EFA-`trainNumber` verglichen. Unterschiedliche vorhandene Nummern
verhindern einen Match; fehlt eine Nummer, gilt der bisherige Fallback. Bestehende
Datenbanken bleiben lesbar. Der vorhandene Basisfeed enthält keine Zugnummern.

### Favoriten bei einem GTFS-Neuimport

Favoriten (`talli:favorites`) und letzte Auswahl (`talli:last-stop`) speichern
`id`, `name` und `dataset_version`. Jeder erfolgreiche GTFS-Import erhält eine
neue persistierte Importkennung; ein Serverneustart oder alleiniger Neubau des
EFA-Katalogs ändert sie nicht. Auch der erneute Import desselben ZIP gilt bewusst
als neue Generation. Bestehende Datenbanken ohne Importkennung erhalten eine
konservative Dateigenerationskennung, ohne Änderung oder erneuten Import der
Datenbank. Bei Kopieren/Ersetzen dieser älteren Datenbanken kann sie wechseln.

Nach einem Importwechsel und bei bisherigen Favoriten ohne Versionsangabe zeigt
Talli **„Neu auswählen“**. Der Eintrag bleibt erhalten und lädt keine Tafel unter
der möglicherweise neu belegten ID. Über „Haltestelle neu auswählen“ wird nach
dem alten Namen gesucht; erst die bewusste Auswahl eines aktuellen Treffers
ersetzt diesen Eintrag. Alternativ kann der alte Favorit entfernt werden.
Es gibt keine automatische Zuordnung nach Name, Koordinaten oder ähnlicher
Gruppenstruktur. Dieselbe Prüfung gilt für die letzte Auswahl nach einem Reload.
Eine leere Tafel bei einer gültigen aktuellen Referenz bleibt dagegen zulässig.

`GET /api/dataset` liefert die aktuelle Kennung. Such-/Umgebungstreffer und
`board.stop` enthalten `dataset_version`. Der Browser sendet diese als Parameter
an `/api/board`; bei abweichender Generation antwortet der Server mit HTTP 409,
bevor er die Fahrtabfrage startet. Ein Importwechsel während einer Such- oder
Tafelabfrage wird ebenfalls zurückgewiesen. Für bestehende API-Clients bleibt
der Parameter optional; diese müssen die Versionsbindung selbst übernehmen.
Demo-Daten verwenden einen eigenen Namensraum. Bereits geöffnete ältere
Browser-Versionen müssen nach dem Deployment neu geladen werden.

Hintergrund und Belege:
[Favoriten und Bad Harzburg](docs/favorites-and-stops-2026-10-05/README.md).
Regressionen: `tests/test_favorite_versions.py` und
`PYTHONPATH=. .venv/bin/python tests/browser_favorites.py`.

### Datenquellen & Lizenzen

Der Footer verlinkt auf `/datenquellen`. Dort stehen die Quellen- und
Lizenzhinweise zentral, passend zu den aktivierten Datenprovidern.
Belege und offene Punkte zur EFA-/API-Nachnutzung:
[Quellenprüfung](docs/data-sources.md). Bei Änderungen der Datenanbieter oder
Aktivierung weiterer Quellen diese Angaben mitprüfen.
