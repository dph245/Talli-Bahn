# VRB-Mapping: vorhandene Evaluation und Umsetzung (03.10.2026)

Gezielte Folgeuntersuchung: [ValidationError bei leerer EFA-Tafel und Vergleich
mit Braunschweig Hbf](empty-departures.md), einschließlich gesicherter Rohantworten.

## Ausgangslage und übernommene Erkenntnisse

Die ursprünglichen Rohdateien unter /tmp sind nach dem Reboot verloren. Folgende
vom Auftraggeber überlieferten Ergebnisse werden übernommen, nicht erneut erhoben:
96 GTFS-Gruppen mit Namenspräfix Wolfenbüttel; 83 UNIQUE, 13 AMBIGUOUS, 0 NONE;
96 sequenzielle Requests, mindestens 1,1 s Abstand. UNIQUE erforderte identischen
Ort und Namen sowie höchstens 100 m Distanz. 37 Plattform-DHIDs an 34 Gruppen
wurden aus vorhandenen Antworten erfasst, ohne vollständige Plattform-Discovery.
Die damalige Auswahl war keine administrative Abgrenzung von Stadt/Ortsteilen.

Die 13 nicht automatisch übernommenen Fälle waren vier Mast-Gruppen bei Friedhof
und Westring sowie Dr.H.-Jasper-Str., Durchgang/Frankfurter Str.,
E.-Moritz-Arndt-Straße, EKZ Halchtersche Str., Ecke Leopoldstr.,
Gebrüder-Welger-Str., Halberstädter Str., Salzdahlumer Str. und Th.-Heuss-Gymn.
Teilweise war nur die Schreibweise abweichend, trotz gleicher Koordinaten.
Diese bleiben konservativ unbestätigt. Keine Abkürzungs-/Mast-Normalisierung.

## Bestehende Architektur und kleinste Erweiterung

GTFS stop/group IDs bleiben Such-, Auswahl-, Favoriten- und Tafelidentitäten.
Die vorhandene Oberfläche speichert last-stop und Favoriten bereits im
localStorage; sie wurde nicht geändert. GTFS erzeugt weiterhin allein Fahrten.
Bisher adressierte EFA vier feste Plattform-DHIDs. Nun liegt die Zuordnung in
app/providers/efa_mapping.py, getrennt vom Fahrtenmatching.

1. Ein rein lokaler Vorbereitungsschritt erzeugt einen VRB-Katalog aus der
   vorhandenen SQLite und den Koordinaten aus stops.txt des passenden GTFS-ZIP.
   Die aktuelle SQLite selbst bleibt unverändert. Auswahl über bediente Routen
   der Agencies `Tarifverb Region Braunschweig` und `Regionalbus Braunschweig`,
   nicht über Wolfenbütteler Namenspräfixe. Namen sind mit --agency konfigurierbar.
   Das ist eine Feed-/Verkehrsgebietsabgrenzung, keine geografische VRB-Grenze.
   Ergebnis am vorhandenen Feed: 4.639 Gruppen, keine fehlenden Koordinaten.
2. Erst eine tatsächlich angefragte Gruppe löst im Hintergrund Discovery aus,
   sofern weder ein bekanntes Mapping noch eine persistierte Antwort vorliegt.
   Stopfinder und Departure Monitor teilen den vorhandenen sequenziellen Worker
   und den Mindestabstand von 1,1 s. Kein Start-/Batch-Polling.
3. Strenger Vergleich: Ort aus dem GTFS-Präfix vor dem Komma, gleicher vollständiger
   Name (Unicode-NFC, casefold, Leerzeichen um Kommas), Distanz <=100 m.
   Ohne Koordinaten oder eindeutig prüfbaren Ort kein UNIQUE. Abkürzungen werden
   nicht expandiert. AMBIGUOUS kann auch einen plausiblen Einzelkandidaten bedeuten.
4. Rohantwort und Klassifikation werden atomar im persistenten Cache gespeichert,
   auch NONE/AMBIGUOUS. Negative Ergebnisse lösen keine erneute Discovery aus.
   Bei gewünschter Neubewertung gezielt die betreffende Cachedatei entfernen.
5. UNIQUE adressiert die EFA-Haltestellen-DHID. Ein Event darf zur Haltestelle
   selbst oder über seine explizite parent.id zu ihr gehören. DHID-Präfixe werden
   nicht geraten. Beobachtete Plattform-DHIDs werden separat persistiert und
   sind ausschließlich Diagnosedaten, keine zusätzliche Matching-Bedingung.
6. Fahrtenmatching bleibt aktivem GTFS-Betriebstag, Linie, exakter Sollzeit und
   nötigenfalls haltestellenspezifischem Ziel verpflichtet. GTFS-RT-Zeit und
   vorhandene Plattformwerte behalten Vorrang. Keine zusätzlichen Fahrten.

Der Katalog enthält Unterhalte als Aliase zur Gruppe. DB-Größe und mtime_ns
binden ihn an den lokalen Import; nach einem neuen Import Katalog neu erzeugen
und Server neu starten. EFA-Discovery-Caches sind nach Name+Koordinaten adressiert
und werden erneut gegen die Kriterien ausgewertet. Bekannte geprüfte Mappings
stehen als Daten in efa_seed.json; damit bleibt die bisherige Unterstützung
auch ohne neu vorbereiteten Katalog erhalten. Es gibt keine Haltestellenfälle
in UI oder Fahrtenlogik.

## Betrieb

```sh
.venv/bin/python -m app.prepare_efa_mapping latest_geamt.zip
# Schreibt data/vrb-stops.json, kein HTTP.
docker compose up -d --build
```

VRB_EFA_ENABLED aktiviert die Ergänzung. VRB_EFA_CATALOG_PATH und
VRB_EFA_CACHE_PATH können bei direktem Start gesetzt werden. Standard sind
vrb-stops.json und efa-cache neben der GTFS-SQLite. Compose liest den Katalog aus
dem bestehenden schreibgeschützten Datenmount und verwendet ein eigenes
persistentes, für den App-User beschreibbares Volume für Discovery-Ergebnisse.
Ein Serverprozess wie bisher; mehrere Prozesse würden getrennt abrufen.
Die erste Tafel wartet nicht auf EFA; weitere Tafelabrufe verwenden den Cache.
Fehler verursachen höchstens einen neuen Versuch nach 60 s tatsächlicher Nutzung.
Echtzeitcache maximal fünf Minuten; permanente Mapping-Caches sind keine
Echtzeitprognosen. Keine neuen Dependencies, keine GTFS-Migration.

## Gezielte neue Messungen

Drei erfolgreiche HTTP-Requests, alle sequenziell; ein zusätzlicher DNS-Versuch
wurde schon lokal von der Sandbox blockiert. Vollständige URLs, Größen und
Zeitpunkte: requests.jsonl. probe.py wiederverwendet gespeicherte Antworten.

- Campestraße Stopfinder: de:03158:460, GTFS 46207, Abstand 0,11 m, UNIQUE.
- Campestraße Haltestellentafel: 20 Events, 18 mit Echtzeit, alle 18 gegen den
  vorhandenen aktiven GTFS-Fahrplan eindeutig gematcht. Steige A und B beobachtet.
- Bahnhof Haltestellentafel: 20 Events, 17 mit Echtzeit, alle 17 gegen GTFS
  gematcht. Bestehender Steig A weiterhin enthalten; weitere Steige werden nun
  ebenfalls abgedeckt. Keine neue Stopfinder-Abfrage für den schon bekannten Bahnhof.

Rohantworten: campestrasse-stopfinder.json, campestrasse-board.json,
bahnhof-board.json. IDs, Koordinaten und Zähler: validation.json.
Die Voruntersuchung über 96 Stops wurde nicht wiederholt.

Der Auftrag benennt zuletzt Campestraße als zusätzlichen Testfall. Der zuvor
benannte `Wolfenbüttel, Neuer Weg` ist in der vorhandenen SQLite nicht vorhanden;
deshalb keine entsprechende externe Discovery und keine künstliche GTFS-Fahrt.

## Grenzen

Eine allgemeine Mapping-Fähigkeit ist keine bereits geprüfte Vollabdeckung:
noch nicht verwendete Gruppen haben noch kein EFA-Ergebnis. Abweichende Namen,
fehlendes Komma/Ort, fehlende Koordinaten und Mehrdeutigkeiten bleiben ohne EFA.
Außerhalb der gewählten GTFS-Agencies liegende VRB-Leistungen können fehlen;
Agency-Auswahl dann explizit erweitern. Das EFA-Limit von 20 Events pro
Haltestellentafel kann an großen Knoten weniger Fahrten je Steig abdecken als
die frühere Einzelsteigabfrage. Keine automatischen Zusatzrequests eingeführt.
Die beobachteten Plattformlisten sind nicht vollständig garantiert.

## Tests und geänderte Dateien

`.venv/bin/pytest -q`: **125 bestanden**, eine bestehende Starlette-TestClient-
Deprecation-Warnung. 12 neue Mapping-Testfälle prüfen strenge Klassifikation,
Koordinaten/Ort/Abkürzungen, Persistenz, Importbindung, Katalog-Gruppierung,
Rate-Limit/Discovery-Wiederverwendung und die gespeicherten Bahnhof-/Campestraße-
Eventidentitäten. Die bisherigen EFA-Tests wurden an Gruppen-Tasks angepasst;
Fahrtenmatching, Quellenpriorität, Fehlerfall und nicht blockierende Tafeln bleiben geprüft.

Code: app/providers/efa_mapping.py, app/providers/efa_seed.json,
app/prepare_efa_mapping.py, app/providers/vrb_efa.py.
Betrieb: Dockerfile, compose.yaml, pyproject.toml (Seed-Paketdatei), .env.example.
Tests: tests/test_efa_mapping.py, tests/test_vrb_efa.py.
Dokumentation: README.md und dieses Verzeichnis einschließlich Rohantworten.
Der lokale, reproduzierbare Katalog liegt unter data/vrb-stops.json.
Die bereits bestehende Benutzeränderung an .gitignore wurde nicht verändert.
