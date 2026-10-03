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
