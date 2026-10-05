# Belege zur Quellenanzeige (05.10.2026)

Die öffentliche Attribution steht zentral unter `/datenquellen`, erreichbar über
den Footer. Texte: `app/data_sources.py`; Layout: `app/static/data-sources.html`.
Keine zusätzlichen Attributionsblöcke in der Abfahrtstafel. Vorhandene Hinweise
im README (Importanleitung), `docs/rb43-2026-10-05/README.md` und die Erkennung
reiner Herkunfts-Alerts in `gtfs_realtime.py` bleiben an ihren bisherigen Stellen.

## Geprüfte Quellen

- [gtfs.de: Feeds](https://www.gtfs.de/de/feeds/): Die als „Creative Commons 4.0“
  bezeichnete Lizenz verlinkt tatsächlich auf
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
  [ÖPNV Deutschland](https://gtfs.de/de/feeds/de_nv/) nennt den NeTEx-Datensatz
  von DELFI e.V. als Grundlage. Die Anzeige nennt beide Beteiligten.
- [gtfs.de: Realtime](https://www.gtfs.de/de/realtime/): ausdrücklich
  [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), einschließlich
  verlinkter Liste der Datenlieferanten. Keine Übertragung dieser Lizenz auf EFA.
- VRB-EFA: Im Repository einschließlich der vorhandenen EFA-Evaluation und
  Rohantworten keine API-Lizenz oder verbindliche Attributionsvorgabe gefunden.
  Geprüft wurden außerdem die [VRB-Fahrplanauskunft](https://auskunft.vrb-online.de/)
  und der [Website-Service](https://auskunft.vrb-online.de/?context=websiteservice).
  Der angebotene Website-Service belegt keine offene Nachnutzungslizenz für
  `bsvg.efa.de/vrbstd_relaunch/XML_DM_REQUEST`. Keine Lizenz aus anderen EFA-
  Installationen oder einer Software-/Dokumentationslizenz abgeleitet.
  Die Anzeige nennt die Quelle und die nicht belegte Nachnutzungslizenz.
- [transport.rest: Betreiber-Dokumentation](https://v6.db.transport.rest/):
  Auskunftsdaten der Deutschen Bahn, Zugriff ohne Authentifizierung, begrenzte
  Abrufraten und Nutzungshinweise. Keine offene Lizenz der gelieferten Daten in
  dieser Dokumentation ausgewiesen. Eine Softwarelizenz des Wrappers wird
  ausdrücklich nicht als Datenlizenz behauptet. Talli nutzt diese Quelle nur
  zur Ergänzung von Gleisangaben.

## Auswahl der angezeigten Quellen

Die aufgelöste Compose-Konfiguration aktiviert GTFS-RT, VRB-EFA und
transport.rest, DB RIS ist deaktiviert. Beim Kontrollversuch war der Container
bereits gestoppt (Exited 0); deshalb keine neue Behauptung über erfolgreiche
Upstream-Abrufe zu diesem Zeitpunkt. Er wurde für diese Aufgabe nicht gestartet.

Die Seite orientiert sich an der tatsächlich instanziierten Provider-Kette:
GTFS-Basis, gtfs.de-Realtime nur bei dessen Host, EFA nur bei vorhandenem
EFA-Provider und transport.rest nur bei aktivem Wrapper über GTFS. Eine temporär
fehlende Prognose blendet die zugehörige Attribution nicht aus. Im Demomodus
erscheint stattdessen ein Hinweis auf fiktive Daten. DB RIS wird im derzeitigen
Betrieb nicht aufgeführt; bei dessen Aktivierung sind zunächst die konkret
vereinbarten Nutzungsbedingungen zu prüfen und die Anzeige zu ergänzen.

Die Seite lädt selbst keine externen Skripte, Schriftarten oder Logos und
funktioniert ohne JavaScript. Ein kleines lokales Skript übernimmt lediglich
die gespeicherte Hell-/Dunkel-Einstellung. Die Zusammenstellung wird nicht im
Service-Worker gecacht, damit deaktivierte Quellen nicht veraltet weiterstehen.
