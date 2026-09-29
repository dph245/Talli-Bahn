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

Standard-Echtzeitquelle ist `https://realtime.gtfs.de/realtime-free.pb`. Der [GTFS.de-Feed](https://www.gtfs.de/de/realtime/) enthält TripUpdates und ServiceAlerts, passt zu den dort angebotenen statischen Feeds und wird alle **10 Sekunden** erneuert. Die Anwendung ruft ihn bei Bedarf höchstens einmal je zehn Sekunden pro Serverprozess ab. Die Oberfläche aktualisiert ebenfalls alle zehn Sekunden. Ohne offene Tafel wird nicht dauerhaft gepollt; mehrere Uvicorn-Worker haben jeweils einen eigenen Cache.

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

Der Adapter unterstützt Protobuf-FULL_DATASET-Snapshots, Ankunfts-/Abfahrtsprognosen als Zeitstempel oder Verspätung, explizite Fahrtverspätungen, Fahrt-Ausfälle und ausgelassene Halte. Feed-/Trip-Zeitstempel älter als 180 Sekunden werden nicht verwendet, auch nicht aus dem Cache. Ein Feed ohne Zeitstempel wird ignoriert.

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

- Haltestellensuche mit Tastaturbedienung (`/` öffnet die Suche)
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
