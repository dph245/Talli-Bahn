# Favoriten nach Neuimport und Haltestellenstruktur Bad Harzburg

Untersuchung am 05.10.2026. Nur Analyse und gesicherte Befunde: keine automatische
Migration, keine Zusammenlegung, keine Änderung am Produktcode oder Import.
Die vorherige [EFA-Evaluation](../efa-discovery/README.md) wurde zugrunde gelegt.

## 1. Ursache der ungültigen Favoriten

`app/static/app.js` speichert pro Browser-Origin:

| localStorage-Key | Inhalt | Prüfung |
| --- | --- | --- |
| `talli:favorites` | Array aus `{id, name}` | Nur String-Typen der beiden Felder |
| `talli:last-stop` | Ein `{id, name}` | Dieselbe reine Formprüfung |
| `talli:theme` | `dark` / `light` | Keine Haltestellenidentität |
| `talli:auto` | Boolean | Keine Haltestellenidentität |

`selectStop` reduziert auch Such-/Umgebungstreffer ausdrücklich auf `id` und
`name`. Keine Quellkennung, Importgeneration, Koordinaten, parent_station,
Haltestellenart, externe stabile Identität oder Gruppenstruktur werden bewahrt.
Favoritenvergleich und Entfernen erfolgen allein nach `id`. Die alte Auswahl
wird beim Start nach der allgemeinen Suchabfrage ungeprüft wiederverwendet.

`/api/board` erhält nur `stop_id`, niemals den erwarteten Namen oder die
Datenversion. Existiert die ID nicht, kommt bereits heute ein 404 mit sichtbarem
Hinweis zur Neuauswahl. **Existiert die alte ID unter neuer Bedeutung weiter,
liefert die API dagegen regulär HTTP 200.** Eine leere Tafel führt zu „Hier ist
nichts los“, eine bediente fremde Station kann sogar falsche Fahrten anzeigen.
`showBoard` vergleicht `board.stop.name` nicht mit der gespeicherten Auswahl;
der Titel bleibt der alte Name aus `selectStop`.

Der Service Worker speichert nur die Anwendungshülle und nimmt `/api/` explizit
vom Cache aus. API und Fetch verwenden `no-store`. Damit ist dies kein Beleg für
einen veralteten Tafelcache, sondern ein fehlender Identitäts-/Versionsvergleich.
Ein bloßer EFA-Katalog-Neubau bei unverändertem GTFS benennt Favoriten-IDs nicht
um. Der problematische Schritt ist der Austausch des GTFS-ID-Namensraums.

### Konkreter Beleg Braunschweig

Ein gesicherter früherer EFA-Cache enthält die GTFS-Gruppe
`203787 = Braunschweig, Hauptbahnhof` samt Unterhalten. Im aktuellen Import gilt:

| ID | Aktuelle Bedeutung |
| --- | --- |
| `203787` | **Bad Bevensen Am Britzenberg**, eigenständige Station |
| `563662` | **Braunschweig, Hauptbahnhof**, neue Stationsgruppe |
| `562185` (früher Bahn-Unterhalt Braunschweig Hbf) | **Mönchsroth, Abzw. Mönchsroth** |

Rein lokale Fahrplanabfrage für 05.10.2026, 12–14 Uhr:
`203787` liefert zwei Abfahrten der Buslinie 7070, `563662` dagegen 119
Abfahrtskandidaten aus Bahn und Straßenbahn. Das belegt die Gefahr einer stillen
Fehlzuordnung unabhängig von der konkreten Uhrzeit des Nutzerberichts.

### Goslar und Beweisgrenzen

Die aktuelle Suche liefert `155197 = Goslar, Bahnhof` und separat
`630925 = Goslar, Bahnhof Nordseite`. Die erste Gruppe hat bei derselben
12–14-Uhr-Abfrage 76 Abfahrtskandidaten einschließlich Bahn und Bus.
Der frühere Bahn-Unterhalt `414153 = Goslar` (aus der RB43-Untersuchung) bezeichnet
heute `Bad Hersfeld Bad Hersfeld Friedloser Straße II`.

Die tatsächlichen Browser-Favoriten des Nutzers sind hier nicht zugänglich.
Insbesondere ist die damals im Goslar-Favoriten gespeicherte Gruppen-ID nicht
belegt; `414153` wird **nicht** als angebliche Favoriten-ID ausgegeben. Der
allgemeine Fehlerpfad und ein konkreter alter Braunschweiger Gruppenbezug sind
belegt. Löschen und Neuanlegen ersetzt die alte numerische Referenz durch die
neue und erklärt, warum das den beobachteten Fehler behebt.

## 2. Aktuelles GTFS-Modell Bad Harzburg

Alle drei genannten Datensätze sind **eigenständige Stationsgruppen**:
`location_type=1`, `parent_station=''`. Ihre Unterhalte sind `location_type=0`
(im ZIP leeres Feld, beim Import zu 0 normalisiert). Es gibt keine gemeinsame
übergeordnete Station und keine Parent-Verknüpfung zwischen diesen Gruppen.

| Gruppe / Unterhalt | Name | Parent | Breite / Länge |
| --- | --- | --- | --- |
| **421683** | Bad Harzburg, Bahnhof | — | 51.887870 / 10.554953 |
| 149802 | Bad Harzburg, Bahnhof | 421683 | 51.887480 / 10.555262 |
| 357898 | Bad Harzburg, Bahnhof Parkdeck | 421683 | 51.887054 / 10.556624 |
| **380494** | Bad Harzburg | — | 51.888370 / 10.554219 |
| 170535 | Bad Harzburg | 380494 | 51.888370 / 10.554219 |
| **255810** | Bahnhof, Bad Harzburg | — | 51.887480 / 10.555262 |
| 227540 | Bahnhof, Bad Harzburg | 255810 | 51.887480 / 10.555262 |

Koordinaten kommen aus dem vorhandenen `latest.zip`; Namen und Parents wurden
für alle sieben Datensätze gegen SQLite geprüft. SQLite selbst speichert keine
Koordinaten. Der VRB-Katalog ist nach Größe und mtime_ns an die aktuelle SQLite
gebunden; die Signaturen stimmen auch im laufenden Container überein.

### Bedienung im gesamten Import

- **421683:** `149802` bedient Buslinien 810, 820, 821; `357898` bedient 871,
  873, 874, 875. Alle `route_type=3`, Agency `Tarifverb Region Braunschweig`.
  Keine Bahnfahrt in dieser Gruppe.
- **380494:** `170535` bedient RB42 und RE10 (erixx) sowie RB82
  (DB Regio AG Nord), jeweils `route_type=2`. Zusätzlich gibt es eine
  **RE10-Route mit `route_type=3`**, Agency erixx, 39 Fahrten über die gesamte
  Feedlaufzeit. „Nur Eisenbahn“ trifft also auf die beobachtete Tafel, nicht auf
  sämtliche Daten dieser Gruppe zu. Die Busroute ist nur an bestimmten Tagen
  zwischen 05. und 14.10. aktiv; die Bezeichnung als Ersatzverkehr wäre ohne
  weitere Quelldaten eine Interpretation.
- **255810:** `227540` hat **zwei RB82-Fahrten mit `route_type=3`**, Agency
  `DB Nord`, Route `20221`, Service `4747`. Ausschließlich
  `calendar_dates: 20261003 / exception_type=1`, kein regulärer Kalender.
  Fahrt `976135`: Ereignis 22:22, Ziel Kreiensen Bahnhof, Einbeck.
  Fahrt `1888851`: Ereignis 22:30, Ziel Bahnhof, Bad Harzburg.
  **Am 05.10. fährt hier laut Feed nichts.** Dies ist weder ein leerer
  Stammdatensatz noch ein nachgewiesener Fehler im Tafelmatching.

Die Root-Koordinaten der Bahn- und Busgruppe liegen nur ca. **75 m** auseinander.
Bus-Unterhalt `149802` und die dritte Gruppe `255810` haben sogar **identische
Koordinaten**, obwohl sie getrennt modelliert sind. Name plus räumliche Nähe ist
hier ausdrücklich kein Beweis für die Austauschbarkeit von Favoriten oder
Haltestellengruppen.

### Parkdeck und Suche

„Bad Harzburg, Bahnhof Parkdeck“ ist **noch vorhanden**, nun als Unterhalt
`357898` der Busgruppe `421683`. Früher war es der Name der übergeordneten Gruppe
`439072`, die auch den Bahn-Unterhalt `454966` enthielt. Die aktuelle ID `439072`
bezeichnet dagegen Bad Muskau Bahnbrücke; `454966` bezeichnet Galla, Kreuzung.

Die Suche zeigt nur Roots (`parent_station` leer), maximal 20 Treffer, ohne
Bedingung auf aktuelle Bedienung. Sie durchsucht keine Unterhaltnamen als
Suchalias. Deshalb findet „Bad Harzburg Parkdeck“ aktuell nichts. Die drei
Stationsgruppen werden nicht wegen ähnlicher Namen zusammengelegt. Die
Tafelabfrage verwendet ausschließlich ausgewählte ID plus direkte Kinder.
Die dritte Gruppe kann trotz abgelaufener Bedienung in der Suche erscheinen.

Eine spätere Suchverbesserung könnte Unterhaltnamen ausschließlich über deren
expliziten Parent zur aktuellen Gruppe auflösen: Parkdeck → `421683`.
Das wäre eine Suchalias-Funktion, keine geografische Zusammenlegung mit `380494`
oder `255810`. In dieser Untersuchung nicht implementiert.

## 3. Warum die Bahngruppe kein EFA-Mapping bekommt

Der aktuelle Kataloggenerator nimmt Gruppen auf, die durch die Agencies
`Tarifverb Region Braunschweig` oder `Regionalbus Braunschweig` bedient werden.
Er übernimmt danach deren direkte Kinder. Die Zugehörigkeit zum geografischen
VRB-Gebiet allein ist kein Aufnahmekriterium.

- `421683` ist im Katalog mit den Kindern `149802` und `357898` enthalten.
- `380494` fehlt: bedient von erixx und DB Regio AG Nord.
- `255810` fehlt: bedient von DB Nord.
- Es gibt keinen passenden Seed für die beiden fehlenden Gruppen.

Mit `MappingStore.group` wurde sowohl lokal als auch im Container nachgewiesen:
Busgruppe → Gruppe, Bahngruppe → `None`, dritte Gruppe → `None`.
EFA ist im Container aktiviert. Die Katalogsignatur ist gültig. Es handelt sich
also **nicht** um einen veralteten Katalog oder einen gescheiterten EFA-Request:
für die Bahngruppe wird überhaupt keine Discovery gestartet.

Eine bloße Erweiterung der Agency-Liste reicht ebenfalls nicht:
`classify` verlangt zusätzlich einen expliziten Ort vor dem Komma und einen
vollständig belegten passenden Namen. **„Bad Harzburg“** enthält keinen solchen
qualifizierten Namen; „Bahnhof, Bad Harzburg“ würde sogar „Bahnhof“ als Ort
interpretieren. Diese konservativen Regeln stammen aus der früheren Evaluation.

Die damalige Lösung für Parkdeck war fachlich an die **damalige GTFS-Gruppe**
gebunden: geprüfte primäre DHID `de:03153:4948`, zusätzlich explizit zugeordnete
DHID `de:03153:4946` über den Unterhalt `Bad Harzburg, Bahnhof`. Damit waren
RB42-Events innerhalb der damaligen gemeinsamen GTFS-Gruppe nutzbar.
Der neue Bahn-Parent ist separat und besitzt diesen Namensnachweis nicht mehr.
Die alte Zuordnung darf nicht an die neue Busgruppe gekoppelte Bahnfahrten
herstellen oder auf `380494` kopiert werden.

**Bewertung:** Kein EFA für `380494` ist unter dem aktuellen Modell korrekt und
konservativ, aber eine erkennbare Abdeckungslücke gegenüber dem alten Import.
Für eine Erweiterung braucht es einen unabhängigen, belastbaren Nachweis der
Zuordnung der Bahngruppe zu einer EFA-Haltestelle, etwa eine gemeinsame stabile
Referenz oder ein ausdrücklich geprüftes und importgebundenes Mapping.
Die bekannte DHID `de:03153:4946` und deren alte Bahn-Produktklassen sind ein
Kandidat für eine solche Prüfung, kein ausreichender automatischer Beweis.
Auch danach bleiben die drei GTFS-Gruppen separat; EFA ergänzt nur Fahrten der
jeweiligen Gruppe unter den bestehenden Fahrtmatching-Kriterien.

Keine neue EFA-Discovery war erforderlich, um diese beiden Vorbedingungen zu
belegen. Es wurden keine aktuellen EFA-Prognosen angefordert und keine Aussage
über deren momentane Lieferfähigkeit abgeleitet. Die vom Nutzer beobachtete
EFA-Ergänzung der Busgruppe wurde nicht durch eine neue Live-Abfrage reproduziert.

## 4. Sichere Grundlage für eine spätere Favoritenkorrektur

1. **Versionsgebundene Referenz:** Quell-Namensraum und GTFS-Importgeneration
   zusätzlich zur Stop-ID speichern. Dafür einen expliziten Import-Identifier
   oder Inhaltsfingerprint vorsehen, nicht `latest.zip` als vermeintliche
   Version. Einen reinen EFA-Katalog-Neubau getrennt behandeln.
2. **Validierung vor Tafelnutzung:** Backend prüft gespeicherte Identität und
   liefert unterscheidbare Zustände wie gültig, ersetzt, mehrdeutig oder nicht
   mehr vorhanden. Eine vorhandene numerische ID allein genügt nicht.
   Erwarteten Namen gegen aktuelle Antwort zu prüfen wäre eine erste
   Konflikterkennung, aber noch kein Identitätsbeweis bei gleichen Namen.
3. **Alte `{id,name}`-Favoriten:** Ohne alte Generation/Metadaten nicht still als
   gültig einstufen. Bei Widerspruch oder fehlender Beweisbarkeit den Favoriten
   erhalten und sichtbar zur Neuauswahl markieren. Passende Suchkandidaten
   anbieten, aber den Ersatz ausdrücklich auswählen lassen. `last-stop` muss
   denselben Weg durchlaufen; sonst kehrt der Fehler beim Reload zurück.
4. **Automatische Migration nur bei belastbarem Bezug:** stabile externe
   Identität mit eindeutiger Versionsabbildung oder explizit validierte
   Alt→Neu-Zuordnung. Für künftige Importe alte Stationsmetadaten aufbewahren:
   Koordinaten, Parent-/Kind-Struktur, Bedienung und externe Referenzen. Diese
   Merkmale helfen bei Prüfung und Kandidatenbildung, ersetzen aber keine
   Eindeutigkeit. Gruppensplits/-zusammenlegungen benötigen besondere Behandlung.
5. **Keine Leere als Invaliditätskriterium:** `255810` ist ein Gegenbeispiel:
   korrekt vorhandene Station, aber nur vergangene Verkehrstage. Auch
   Nachtpausen und das Zwei-Stunden-Fenster dürfen keine Migration auslösen.

Für die spätere Implementierung notwendige Regressionen: neu belegte alte ID,
fehlende ID, unveränderte Identität nach Neuimport, unbekannte Legacy-Generation,
Gruppensplit wie Bad Harzburg, gleiche Koordinaten bei verschiedenen Parents,
404 versus echte leere Tafel, `last-stop` nach Reload und unveränderter GTFS-
Import bei bloßem EFA-Katalog-Neubau. Kein Löschen aller Browserdaten erforderlich.

## Belege und Reproduktion

- [current-data.json](current-data.json): aktuelle Strukturen, ZIP-Koordinaten,
  Routen, Bedienungsnachweise, Suchergebnisse und statische Beispieltafeln.
- [historical-efa-cache.json](historical-efa-cache.json): ausschließlich die zwei
  relevanten alten Cacheeinträge aus dem Docker-Volume (Braunschweig / Parkdeck).
  Historische Rohantworten, keine aktuellen Betriebszustände.
- [analyze.py](analyze.py): wiederholbare lokale Analyse, ohne Netzwerk oder
  Änderungen an Datenbank/Katalog/Cache. Die referenzierten IDs sind bewusst
  fallbezogen; nach einem weiteren Import kann sich ihre Bedeutung ändern.

```sh
PYTHONPATH=. .venv/bin/python docs/favorites-and-stops-2026-10-05/analyze.py
```

Die Analyse wurde erfolgreich ausgeführt. Produktcode und Browserdaten blieben
unverändert; daher keine neue Produkt-Testsuite oder Migration ausgeführt.

## Anschließende Umsetzung: Versionsbindung ohne automatische Migration

Nach ausdrücklichem Folgeauftrag umgesetzt: Importkennung in GTFS-Metadaten,
Version in Such-/Umgebungstreffern und gespeicherten Referenzen, serverseitige
409-Prüfung vor Tafelladung und nach konkurrierendem Import, sichtbare manuelle
Neuauswahl für Legacy-/Alt-Favoriten und `last-stop`. EFA-Katalog und
Bad-Harzburg-Gruppen werden dadurch nicht verändert. Die Bedienung ist im
[Haupt-README](../../README.md#favoriten-bei-einem-gtfs-neuimport) dokumentiert.
Die ursprüngliche Untersuchung oben beschreibt den Zustand vor dieser Änderung.
