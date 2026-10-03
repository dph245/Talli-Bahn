# ValidationError bei leerer EFA-Tafel, 03.10.2026

## Request und gesicherte Antwort

Die vorhandene Mapping-Evaluation in README.md wurde zuerst gelesen; keine
erneute Gebietsevaluation und keine Änderung am Mapping oder Fahrtenmatching.
Der lokale Katalog ordnet GTFS `629402` der Gruppe
`Wolfenbüttel, Birkenweg` mit den Unterhalten `413453` und `475632` zu.
MappingStore.read liefert aus dem bestehenden Seed `UNIQUE`, DHID
`de:03158:1677`. Die beobachtete Plattform `de:03158:1677:1:1` ist **nicht**
das Requestziel. Der Request aus EFAFeed.refresh_stop lautet:

```text
https://bsvg.efa.de/vrbstd_relaunch/XML_DM_REQUEST?name_dm=de%3A03158%3A1677&type_dm=stop&useRealtime=1&limit=20&outputFormat=rapidJSON&mode=direct
```

Es werden keine Datums-/Zeitparameter gesendet; EFA verwendet den Abrufzeitpunkt.
Reproduktion mit dem vorhandenen Probe-Skript:

```sh
python3 docs/efa-discovery/probe.py board de:03158:1677 birkenweg-after-service-board
```

Am 03.10.2026 um 19:35:19 Europe/Berlin: HTTP 200, 704 Bytes.
Die unveränderte Antwort wurde **vor der Codeänderung** in
`birkenweg-after-service-board.json` gespeichert. `requests.jsonl` enthält URL,
Zeitpunkte, Status und Größe sowie den vorherigen gescheiterten Sandbox-DNS-Versuch.
Das Probe-Skript verwendet vorhandene Dateien wieder und überschreibt sie nicht.
Dies ist eine lokale Reproduktion, keine nachträglich rekonstruierte
Produktionsantwort; deren genauer Zeitpunkt und Inhalt fehlen.

Die Antwort identifiziert die richtige Haltestelle in `locations` und enthält:

```json
{"systemMessages":[{"type":"error","module":"BROKER","code":-4030,"text":"no matching departure found"}]}
```

`stopEvents` fehlt vollständig. Der bisherige Validator wirft reproduzierbar:

```text
1 validation error for Response
stopEvents
  Field required [type=missing, ...]
```

EFA kennzeichnet den leeren Suchtreffer selbst als `error`; diese fachliche
Leerantwort ist hier kein Transport- oder Schemafehler. Eine explizite Liste
`stopEvents: []` wurde bereits vorher akzeptiert.

Der lokale GTFS-Import hat zum Abrufzeitpunkt keine zukünftigen Abfahrten im
Tafelfenster. Er enthält am 03.10. allerdings noch 18:03 (792), 18:22 (792),
18:28 (799) und 18:48 (799). „Nach 18 Uhr keine Fahrten“ lässt sich deshalb
nicht für den gesamten Zeitraum ab 18:00 aus diesem Import bestätigen.
Es wurden keine Fahrplandaten oder Feiertagsregeln geändert.

## Kleine Korrektur und Regression

Response ergänzt ausschließlich bei fehlendem `stopEvents` und einer nicht
leeren Liste ausschließlich aus `type=error`, `module=BROKER`, `code=-4030`
eine leere Eventliste. Fehlendes Feld ohne diesen Nachweis, unbekannte oder
gemischte Fehlermeldungen, `null` und ungültige Events bleiben ValidationErrors.
Kein allgemeiner Default für fehlende Felder und kein Überspringen defekter Events.

Der vorhandene erfolgreiche Refreshpfad schreibt dann einen frischen leeren
Prognosecache; alte Prognosen werden entfernt und keine Warnung erzeugt.
GTFS bleibt die alleinige Quelle der Fahrten: Eine leere EFA-Antwort löscht
keine GTFS-Fahrten. Echte Fehler bewahren weiterhin den bisherigen Cache.

Der Regressionstest verwendet die gespeicherten Originalbytes über MockTransport
und prüft Requestparameter, leeres Ergebnis, Cacheersetzung und ausbleibende
Warnung. Sechs weitere Varianten sichern explizite Leerlisten sowie die
beibehaltenen Fehler-/Cachepfade ab.

Prüfung: `.venv/bin/pytest -q` — **132 bestanden**, eine bestehende
Starlette-TestClient-Deprecation-Warnung.

## Braunschweig Hbf

Im lokalen Katalog gehört der Bahnsteig `562185` (`Braunschweig Hbf`) zur
Gruppe `203787`, `Braunschweig, Hauptbahnhof`. Dafür lag lokal noch kein
Discovery-Ergebnis vor. Eine gezielte Stopfinder-Abfrage mit den bestehenden
Parametern ergibt nach unveränderter Klassifikation `UNIQUE`, `de:03101:178`,
Abstand 0,22 m. Rohantwort: `braunschweig-hbf-stopfinder.json`.
Es wurde kein Mappingcache geändert.

Die anschließende Tafelabfrage um 19:36:01 mit denselben Parametern wie oben,
aber `name_dm=de:03101:178`, ist in `braunschweig-hbf-board.json` gesichert:
HTTP 200, 20 Events, bereits mit dem ursprünglichen Validator fehlerfrei.
19 Events besitzen eine geschätzte Zeit; 17 werden durch match_predictions
gegen die lokale GTFS-Gruppe gematcht. Einzelmessung und URLs stehen in
`empty-departures-validation.json` und `requests.jsonl`.

Der Fehlerpfad ist für alle Haltestellen gleich: Ein ValidationError verwirft
die komplette neue Antwort. Vorhandene Prognosen bleiben maximal MAX_AGE=300 s
nutzbar; danach fehlt ohne erfolgreiche Aktualisierung der EFA-Zusatz.
`checked` drosselt weitere Versuche auf mindestens 60 s bei tatsächlicher Nutzung.
Eine wiederholte -4030-Antwort ohne stopEvents könnte daher auch am Hbf den
bisherigen Fehler auslösen. Ein einzelner Fehlschlag muss EFA nicht sofort
verschwinden lassen. Birkenweg und Hbf haben getrennte Cache-/Taskschlüssel;
eine leere Birkenweg-Antwort löscht keine Hbf-Prognosen.

**Für das beobachtete zeitweise Verschwinden am Hbf ist diese Ursache nicht
belegt.** Die aktuelle Hbf-Probe reproduziert sie nicht, und die damalige
Antwort fehlt. Auch andere ungültige Eventfelder können denselben allgemeinen
ValidationError-Pfad auslösen und bleiben durch diese gezielte Korrektur
unverändert. Der Quellenzusatz erscheint zudem nur, wenn EFA tatsächlich eine
Zeit oder fehlende Plattform ergänzt. Null passende Prognosen, Vorrang von
GTFS-RT, das Limit von 20 Events oder abgelaufene Prognosen können den Zusatz
auch ohne Parserfehler entfallen lassen. Das vorhandene Logging unterscheidet
diese historischen Ursachen nicht hinreichend für eine gesicherte Diagnose.
