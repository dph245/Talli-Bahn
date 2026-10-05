# RB43 / gemeldete Zugnummer 81561 / 05.10.2026

## Befund und Beweisgrenze

Der lokale Import und der am 05.10.2026 um **16:15:51 UTC (18:15:51 MESZ)**
erzeugte GTFS-RT-Snapshot haben inkompatible Fahrtidentitäten. Unter gleichen
`trip_id` stehen unterschiedliche Haltefolgen. Das bisherige Matching vertraute
der ID und nahm bei vorhandener `stop_sequence` keine Rücksicht auf eine
widersprechende `stop_id`. Damit können Stopzeiten einer fremden Fahrt auf eine
lokale Fahrt übertragen werden. Fahrtbezogene Alerts verwenden denselben
ID-Namensraum und können dadurch ebenfalls sachfremd erscheinen.

Dies ist ein nachgewiesener Fehlerpfad und ein nachgewiesener aktueller
Datenkonflikt, **keine gesicherte Rekonstruktion der konkreten Morgen-Entity**.
Der Morgen-Snapshot wurde nicht gespeichert. Im Abend-Snapshot existieren weder
TripUpdates noch trip_id-bezogene Alerts für `1029743`. Eine konkrete damalige
Entity-ID oder deren ursprüngliche Busfahrt lässt sich deshalb nicht belegen.
Auch ob ein Wechsel der veröffentlichten IDs oder eine andere Inkonsistenz beim
Anbieter zugrunde liegt, lässt sich mit diesen Artefakten nicht unterscheiden.
Eine abweichende Region darf nicht aus einer numerischen GTFS-ID geraten werden.

Vorher gelesen: [vorhandene EFA-Evaluation](../efa-discovery/README.md) und
[Echtzeitprüfung vom 30.09.2026](../realtime-check-2026-09-30.md).
Keine erneute Haltestellen-Discovery, keine Änderung an deren Kriterien.

## 1. Lokale GTFS-Fahrt

`trip_id=1029743`, `route_id=18234`, `agency_id=211` / **erixx**,
`service_id=269`, Linie **RB43**, Ziel **Goslar**. Montag 05.10.2026 liegt im
Kalenderintervall 26.09.–26.10.; keine Tagesausnahme für diese Fahrt.

| Sequenz | Halt | stop_id | Soll-Abfahrt MESZ |
| --- | --- | --- | --- |
| 0 | Braunschweig Hbf | 562185 | 10:24 |
| 1 | Wolfenbüttel | 612924 | 10:33 |
| 2 | Börßum | 520538 | 10:43 |
| 3 | Schladen(Harz) | 378001 | 10:47 |
| 4 | Vienenburg | 214836 | 11:00 |
| 5 | Oker | 113006 | 11:07 |
| 6 | Goslar | 414153 | 11:12 |

Die Zuordnung zur gemeldeten Zugnummer 81561 ergibt sich aus dem Feldbericht
und diesem exakt passenden Fahrtverlauf. Der lokale Feed belegt die Zugnummer
nicht unabhängig: `latest.zip/trips.txt` enthält nur
`route_id,service_id,trip_id`, kein `trip_short_name`. Die SQLite-Metadaten nennen
`latest_geamt.zip` als Importquelle; der Datensatz dieser Fahrt stimmt mit dem
vorhandenen `latest.zip` überein. Dateinamen allein beweisen keine Feedversion.
Die lokale Datenbank stammt laut Dateizeit vom 29.09.2026.

## 2. Tatsächliche Realtime-Auswahl und Messung

Bisher: `updates[(departure.trip_id, departure.service_date)]`, ersatzweise
`updates[(departure.trip_id, "")]`. Der Betriebstag wäre `20261005`.
Die `FeedEntity.id` dient nicht dem Fahrtmatching. Ein Update mit passender
Trip-ID würde selbst ohne route_id angenommen. Anschließend wurde allein die
Stopsequenz benutzt, falls vorhanden; nur sonst wurde die Stop-ID verglichen.
Keine Linie, Zugnummer oder Zeitfenster kommen bei diesem GTFS-RT-Match vor.

Ein einzelner neuer Download von `https://realtime.gtfs.de/realtime-free.pb`
wurde vollständig gegen die vorhandene SQLite geprüft. Von 60.555 TripUpdates:

- 59.820: lokale Trip-ID vorhanden, aber mindestens ein mitgeliefertes Paar
  `(stop_sequence, stop_id)` widerspricht dem lokalen Fahrtverlauf.
- 47: lokale Trip-ID vorhanden, kein solcher Widerspruch. Das bedeutet nicht,
  dass alle übrigen Identitätsmerkmale geprüft bzw. korrekt sind.
- 688: Trip-ID nicht lokal enthalten.

Konkretes Beispiel: Entity **`1471395tu`**, Trip-ID **`1471395`**.
Lokal: Route `253`, Linie `86`, Agency `LVB`, Sequenz 0 = Stop `67717`.
Realtime: Sequenz 0 = Stop `51483`, insgesamt eine andere Haltefolge.

[evidence.json](evidence.json) enthält Feedzeit, SHA-256, Downloadgröße, Zähler,
diese vollständige Beispiel-Entity und ihren lokalen Vergleichsdatensatz.
Der vollständige Download liegt nur unter `/tmp/talli-rb43-realtime.pb`
(nicht versioniert; nach temporärer Bereinigung nicht dauerhaft verfügbar).
Der Auszug stammt vom öffentlichen [GTFS.de-Realtime-Feed](https://gtfs.de/de/realtime/),
Lizenz laut Anbieter CC BY-SA 4.0. Der Anbieter dokumentiert grundsätzlich
Kompatibilität mit seinen statischen Feeds; das ersetzt keine Prüfung konkreter
Import-/Snapshot-Kombinationen.

## 3. GTFS, EFA und weitere Quellen

GTFS erzeugt die Fahrten einschließlich aktiver Betriebstage und Sollzeiten.
GTFS-RT ergänzt Zeiten, Ausfälle und Alerts. VRB-EFA ergänzt danach fehlende
Prognosen und Plattformen, erzeugt aber keine Fahrten und übernimmt **keine Alerts**.
EFA verlangt bestätigte Haltestellenzuordnung, identische Linie und sekundengenau
identische Sollzeit. Bei mehreren Kandidaten entscheidet zusätzlich das Ziel;
verbleibende Mehrdeutigkeit wird verworfen. Kein unscharfes Zeitfenster, keine
Gleichsetzung von EFA-tripCode und GTFS-trip_id. GTFS-RT hatte und hat bei
akzeptierter Identität Vorrang. transport.rest ergänzt nur Gleise, keine Zeiten
oder Meldungen. DB RIS ist standardmäßig deaktiviert und nutzt explizite Mappings.

## 4. Warum fremde Alerts möglich waren

Die vorhandene Selector-Logik prüfte Agency, Route, Trip und Stop innerhalb eines
Selectors bereits als UND; verschiedene Selector-Einträge sind ODER-verknüpft.
Ein Alert mit nur `trip.trip_id` verlangt jedoch keine zusätzliche Agency.
Das ist für einen kompatiblen Feed korrekt, kann aber bei neu belegten IDs die
falsche lokale Fahrt treffen. Dasselbe Grundproblem betrifft Route-/Agency-IDs.
Eine Textfilterung auf Stuttgart oder Gelenkbus würde die Ursache verdecken.
Mangels Morgen-Payload bleibt offen, welcher konkrete Selector damals griff.
Zusätzlich fehlte die Prüfung des eigenständigen `EntitySelector.direction_id`;
sie ist nun ergänzt (nicht als Ursache dieses Falls behauptet).

## 5. Korrektur und Betriebswirkung

`IdentityGuard` prüft jeden neuen Snapshot gegen die importierten Fahrten, bevor
Zeiten, Ausfälle oder Alerts übernommen werden. Geprüft werden vorhandene
Route-/Richtungsangaben sowie Stopsequenzen und Stop-IDs im **ganzen** Snapshot,
auch außerhalb der aktuell angezeigten Tafel. Wenn Realtime sowohl `time` als
auch `delay` und einen Betriebstag liefert, muss `time - delay` zur statischen
Sollzeit passen. Dadurch werden auch andere Fahrten auf derselben Haltefolge
erkannt; negative oder große Verspätungen werden nicht pauschal ausgeschlossen.

Ein belegter Widerspruch verwirft den **gesamten Snapshot einschließlich Alerts**:
Deren gemeinsamer ID-Namensraum ist dann nicht mehr vertrauenswürdig. Im Log
stehen Feedzeit und konkreter ID-Widerspruch. Die API meldet GTFS-RT als
`unavailable`; GTFS-Fahrplan und unabhängige EFA-Ergänzung bleiben verfügbar.
Diese konservative Entscheidung kann auch bei einem einzelnen fehlerhaften
Upstream-Datensatz gültige andere Echtzeitdaten vorübergehend zurückhalten.
Sie verhindert aber, dass Meldungen ohne prüfbaren Fahrtverlauf weiter an
möglicherweise neu belegte Route-/Agency-IDs gebunden werden.

Die Prüfung läuft einmal pro Snapshot und Datenbankgeneration im vorhandenen
Hintergrundthread der Anreicherung, mit gebündelten SQL-Abfragen und Cache unter
Lock. Ein atomarer Neuimport invalidiert die Prüfung. Ein nachgewiesener Konflikt
wird nicht durch einen späteren reinen Alert-Snapshot aufgehoben; dafür ist ein
neuer Import oder wieder ein widerspruchsfreier Snapshot mit belegtem Halt nötig.
Unbekannte Trip-IDs in regionalen Teilimporten sind allein kein Konflikt.
Fehlende optionale Felder werden nicht erfunden. Ohne Vergleichsmerkmale kann
dieser Mechanismus die Identität nicht beweisen; passende Quellversionen bleiben
Voraussetzung, gerade für reine Alert-Feeds.

Auch das einzelne Stop-Matching lehnt nun eine widersprechende Stop-ID ab, und
widersprechende doppelte TripUpdates für denselben Schlüssel werden nicht mehr
nach Eingangsreihenfolge überschrieben.

**Betrieb:** Für Wiederherstellung von GTFS-RT müssen statischer Import und
Realtime-Quelle tatsächlich zusammenpassen. Den alten ZIP lediglich erneut zu
importieren behebt die belegte Inkompatibilität nicht. Einen aktuellen passenden
Datensatz importieren, daraus den VRB-Katalog neu erzeugen und den Dienst neu
starten; bei weiterhin widersprechenden IDs die Kombination beim Datenanbieter
klären. Hier wurden weder die 4,7-GB-Datenbank ersetzt noch Container neu gestartet.
Der Fix liegt im Arbeitsbaum und muss für den laufenden Dienst gebaut werden.

## 6. Zugnummer als zusätzliches Kriterium

Vorhandene EFA-Rohantworten enthalten bei RB43 zum Beispiel
`transportation.properties.trainNumber="81577"`. Diese Information wurde bisher
beim Parsen verworfen. Sie wird jetzt berücksichtigt.

Künftige Imports bewahren optionales GTFS-`trip_short_name` in `trip_names` auf.
Rein numerische Angaben bei Bahnfahrten werden intern als Zugnummer verwendet;
das Feld ist laut [GTFS-Spezifikation](https://gtfs.org/documentation/schedule/reference/#tripstxt)
für einen Fahrtnamen, etwa eine Zugnummer, vorgesehen. Alte Schema-v2-Datenbanken
bleiben ohne diese optionale Tabelle lesbar. Es wird keine Zugnummer aus einer
Trip-ID, einem Liniennamen oder EFA-tripCode geraten. GTFS-RT TripDescriptor
liefert in der verwendeten Standardschnittstelle keine separate Zugnummer.

Beim EFA-Matching schließen vorhandene unterschiedliche Zugnummern einen
Kandidaten aus; eine gleiche Nummer kann Mehrdeutigkeit auflösen. Haltestelle,
Linie, Sollzeit und Eindeutigkeit bleiben Voraussetzungen. Fehlt die Nummer auf
einer Seite, bleibt der bisherige Fallback bestehen. Dieser konkrete lokale
Basisfeed kann damit weiterhin nicht nach 81561 verglichen werden.

## 7. Regression und anfängliche Unsichtbarkeit

[Regressionstests](../../tests/test_realtime_identity.py) verwenden die
[extrahierten lokalen Fahrplandaten](../../tests/fixtures/rb43-20261005.json).
Die falschen Realtime-Zeiten und Meldungen werden **synthetisch rekonstruiert**,
nicht als angeblich gesicherter Morgen-Payload ausgegeben. Geprüft werden der
ganze Fahrtverlauf, fremde Stop-IDs trotz gleicher Sequenzen, andere Sollzeiten
auf gleicher Haltefolge, korrekte positive/negative Verspätungen, fremde
Agency-/Route-/Richtungs-Selectoren, Konflikte außerhalb der Tafel, Neuimport,
Snapshotwechsel, doppelte Updates sowie der alte Import ohne Zugnummern.
EFA-Tests sichern Nummerngleichheit, Widerspruch, Disambiguierung und fehlende
Nummern ab.

Die anfängliche Unsichtbarkeit passt zum bestehenden Sichtbarkeitsmechanismus:
Ohne Prognose verschwindet eine Fahrt nach ihrer Sollzeit. Eine später geladene
falsche Zukunftsprognose kann sie wieder sichtbar machen. Das ist eine plausible
Erklärung, kein durch historische Requests belegter Ablauf. Auch die angezeigten
+22/+13 gegenüber den berichteten Minutenzeiten lassen sich ohne Sekunden nicht
exakt nachrechnen; die Anzeige rundet Verzögerungen mit `ceil` auf volle Minuten.

Validierung: `.venv/bin/pytest -q` **204 bestanden** (175 vor den neuen
Regressionen), eine unveränderte Starlette/httpx-Deprecation-Warnung. Der finale
Gesamtlauf erfolgte außerhalb der Sandbox, nachdem ein Sandbox-TestClient-Lauf
hängen blieb. Keine UI-Änderungen; kein zusätzlicher Browser-Test erforderlich.
`git diff --check` ohne Befund. Die neue Identitätsprüfung wurde zusätzlich auf
den tatsächlich heruntergeladenen Snapshot angewandt und wies ihn mit dem
oben dokumentierten Konflikt `1471395 / Sequenz 0 / 51483 statt 67717` zurück
(lokale Einzelmessung 0,050 s bis zum ersten Konflikt; kein Benchmark eines
vollständig kompatiblen Feeds). Die historische Analyse umgeht ausschließlich
die inzwischen abgelaufene Frischeprüfung; im Produkt bleibt diese aktiv.
