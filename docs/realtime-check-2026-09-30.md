# Konservative Echtzeitprüfung, 30.09.2026

## Messung am tatsächlich konfigurierten GTFS.de-Endpunkt

URL: `https://realtime.gtfs.de/realtime-free.pb`. Einzelmessungen von diesem
Rechner, keine Lasttests. Zeiten und Größen sind Momentaufnahmen.

Vollständiger GET mit `curl --compressed --max-time 120`: Der Client bot die
von seiner Installation unterstützte Kompression an (gzip/deflate/br/zstd).
`size_download` zählt den übertragenen Response-Body, nicht HTTP-/TLS-Overhead;
Dateigröße nach möglicher Dekompression separat geprüft.

| Merkmal | Gemessener Wert |
| --- | --- |
| HTTP / Serverzeit | 200 / 30.09.2026 18:14:45 UTC |
| Tatsächlich übertragener Body | 26.063.106 Bytes (26,06 MB / 24,86 MiB) |
| Gespeicherter Protobuf-Body | ebenfalls 26.063.106 Bytes |
| Gesamtdauer | 17,486 s |
| `Content-Length` | 26063106 |
| `Content-Encoding` | fehlt; in dieser Antwort keine Kompression |
| `ETag` | `"18db102-65cb744c14aeb"` |
| `Last-Modified` | `Wed, 30 Sep 2026 18:14:22 GMT` |
| `Cache-Control` / `Vary` | beide fehlen |
| Protobuf-Headerzeit | 18:14:19 UTC |
| Inhalt | FullDataset, 99.655 Entities, davon 44.086 TripUpdates |

Die genannten 15–18 MB sind also keine feste Obergrenze. Fehlendes
`Cache-Control` bedeutet hier insbesondere **nicht**, dass Conditional GETs
unmöglich sind.

Zwei getrennte Conditional-GET-Messungen um 18:15:38 UTC: jeweils zuerst HEAD,
danach GET mit dem gerade gelesenen Validator, `Accept-Encoding: gzip, deflate`.
HEAD meldete inzwischen 25.490.390 Bytes, ETag `"184f3d6-65cb7484dac86"` und
Last-Modified `Wed, 30 Sep 2026 18:15:22 GMT`.

| GET-Requestheader | Status | Übertragener Body | Dauer |
| --- | --- | ---: | ---: |
| `If-None-Match: "184f3d6-65cb7484dac86"` | 304 | 0 Bytes | 0,018 s |
| `If-Modified-Since: Wed, 30 Sep 2026 18:15:22 GMT` | 304 | 0 Bytes | 0,019 s |

Beide Antworten enthielten diese Validatoren, aber weder Content-Encoding noch
Cache-Control. Damit ist die Unterstützung beider Bedingungen gemessen.
Die vorgeschalteten HEADs dienten nur der Messung und werden **nicht** in den
Poller eingebaut. Bei geänderten Inhalten bleibt ein voller Download nötig;
eine regelmäßige Ersparnis bei 60-Sekunden-Polling ist nicht nachgewiesen.
Bei ständig 26,06 MB und exakt einem Abruf pro Minute wären es rechnerisch
37,53 GB pro Tag. Tatsächlich wartet Talli erst nach Abschluss des Downloads
60 Sekunden und bei Fehlern länger.

## Kleine Änderungen und bewusst beibehaltenes Verhalten

- Der bestehende zentrale Hintergrundworker sendet jetzt `If-None-Match`,
  ersatzweise `If-Modified-Since`. Validatoren werden nur nach erfolgreichem
  Parsen eines vollständigen Snapshots übernommen. Fehler überschreiben weder
  Snapshot noch Validatoren. Ein gültiger neuer 200-Body ohne Validatoren löscht
  die alten Validatoren. Keine zusätzliche HEAD-Anfrage im Betrieb.
- 304 spart Body und Parsing, verändert aber **nicht** den Feedzeitstempel.
  Nach 300 Sekunden Feedalter liefern Cacheleser keine Prognosen/Ausfälle mehr.
  Auch individuelle TripUpdate-Zeitstempel werden weiterhin geprüft.
- 60 Sekunden Pause nach Abschluss, vorhandener Backoff (120/240/480/900 s),
  `Retry-After`, ein Uvicorn-Worker und rein lesende Clientzugriffe bleiben bestehen.
  Keine neuen Provider, Frameworks oder Änderungen an transport.rest.
- Für Sichtbarkeit und Sortierung gilt bei Prognosen die Echtzeit, mit 60 Sekunden
  Karenz danach. Ohne Prognose gilt weiter die Sollzeit ohne Karenz. Ausfälle
  bleiben separat fünf Minuten nach Sollzeit sichtbar und werden nach Sollzeit
  einsortiert; eine frühere Prognose verlängert sie nicht.
- Beim Refresh derselben Tafel wird eine verfügbare Echtzeittafel nicht mehr
  zwischenzeitlich durch die statische Antwort ersetzt. Diese bleibt als
  Fehlerfallback verfügbar. Erstladen und Haltestellen-/Richtungswechsel zeigen
  weiterhin zuerst den Fahrplan. Die nächste Backendantwort ohne frischen
  Snapshot ersetzt die Prognosen durch Plan.
- Der Claim lautet „Abfahrten. Ohne Drama.“.

Der bestehende SQL-Kandidatenrückblick bleibt konservativ bei zwei Stunden.
Die Korrektur gilt für diese Kandidaten; bereits mehr als zwei Stunden
zurückliegende Sollzeiten werden weiterhin nicht geladen. Eine unbegrenzte
Nachsuche für extreme Verspätungen wäre eine gesonderte Änderung mit Querymessung.

## Regionale Feeds: Ergebnis, keine Integration

Der [VBN dokumentiert einen regionalen GTFS-RT-Feed](https://www.vbn.de/service/entwicklerinfos/open-data-und-open-service/)
für einen Teil der Verbindungen, mit 60-Sekunden-Aktualisierung, Protobuf und JSON.
Die statischen Referenzdaten kommen von Connect; die geografische Abdeckung
statischer Daten ist keine Garantie für Echtzeit an jeder Haltestelle.

Der dokumentierte Protobuf-Pfad ließ sich auch per HTTPS abrufen:
`https://gtfsr.vbn.de/gtfsr_connect.bin`. Messung 18:15:59 UTC:

- HTTP 200, 943.123 Bytes übertragen und gespeichert, 1,139 s.
- Content-Encoding fehlt, Cache-Control `private`; ETag und Last-Modified fehlen.
- Protobuf gültig, FullDataset, Headerzeit 18:15:37 UTC, 5.241 TripUpdates.
- **0 von 5.235 eindeutigen Trip-IDs** stimmen exakt mit der vorhandenen
  GTFS.de-Datenbank `data/gtfs.sqlite` überein (lesende Primärschlüsselabfragen).

Damit existiert eine deutlich kleinere regionale Quelle, aber ein reiner
URL-Tausch würde mit dem bestehenden Fahrtmatching diese Stichprobe nicht
zuordnen. Abdeckung von Wolfenbüttel/VRB, erixx und BSVG sowie passende statische
IDs müssten vor einem Wechsel gesondert validiert werden.

[Connect beschreibt seine regionalen GTFS-Solldaten](https://connect-fahrplanauskunft.de/datenbereitstellung/).
[GTFS.de führt VBN als Datenquelle für Niedersachsen/Bremen/NAH.SH auf](https://gtfs.de/de/realtime/).
Bei der Suche nach eigenständigen öffentlichen GTFS-RT-Endpunkten für VRB,
BSVG und erixx wurde kein belastbar dokumentierter zusätzlicher Endpunkt gefunden.
Das ist kein Nachweis, dass es keine vereinbarten oder zugangsbeschränkten
Schnittstellen gibt. Keine neue Provider-Architektur und keine Kontaktaufnahme.

## Prüfung

Ausgangslage: **85 pytest-Tests bestanden**. Nach der Änderung: **95 bestanden**.
Die bestehende Starlette/httpx-Deprecation-Warnung bleibt unverändert.

Neue Regressionen prüfen Conditional Requests, Validatoren nach ungültigem
Payload, 304 ohne Validator, unveränderte Frischegrenze bei 304, verspätete
Abfahrten und Ankünfte über die Sollzeit hinweg, Prognosesortierung, vorzeitige
Abfahrt, Karenzgrenzen, Ausfälle und Planfallback. Bestehende Tests prüfen weiter
parallel lesende Clients ohne Upstream-Abruf, Lifecycle, Backoff und Ausfälle.

Der Browser-Smoke-Test wurde um Claim, sichtbare verspätete Fahrt während des
zweistufigen Refreshs und Planfallback nach fehlgeschlagener Echtzeit erweitert.
Er ist mit Chromium vollständig bestanden, einschließlich der bisherigen
Suche-, Filter-, Offline-, Mobilansicht- und Detailprüfungen.

```sh
.venv/bin/pytest -q
.venv/bin/python tests/browser_smoke.py
```

In dieser Umgebung musste die Prüfung außerhalb der Sandbox laufen: Der
unveränderte TestClient hing dort im Threadstart; Chromium scheiterte an einer
verbotenen Socketoperation. Es wurden keine Abhängigkeiten aktualisiert.
