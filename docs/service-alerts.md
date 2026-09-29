# ServiceAlerts: Provenienz und Deduplizierung

## Untersuchung vor der Filteränderung

Untersucht wurde ein vollständiger Abruf von
`https://realtime.gtfs.de/realtime-free.pb` am 29.09.2026
(`FeedHeader.timestamp=1790706896`). Der Snapshot enthielt 49.876 Alerts.
42.933 davon waren technische Herkunftshinweise, verteilt auf unterschiedliche
Fahrten und Entity-IDs, beispielsweise `1333012al2827965951`.

Die Herkunftsangabe steht in **`FeedEntity.alert.description_text.translation[].text`**:

```text
Echtzeitdaten aufbereitet von GTFS.de, bereitgestellt von DELFI
```

Weitere Varianten nennen etwa VRR, VBN oder opentransportdata.swiss.
Die Einträge haben leeres `header_text`, `cause=UNKNOWN_CAUSE`,
`effect=UNKNOWN_EFFECT`, `severity_level=INFO`, keine URL und keinen
`active_period`. `informed_entity.trip` enthält Fahrt-ID und Verkehrstag.
Ein explizites Provenienzkennzeichen enthält dieser Feed an diesen Einträgen nicht.

**Die strukturellen Felder allein sind kein zuverlässiger Filter:** Dieselbe
Kombination kommt bei „Rollstuhlgeeignet“, „Niederflur“ und einem Hinweis auf
eine defekte Rolltreppe vor. Diese Meldungen bleiben erhalten.

## Bewusst eng begrenzte Filterregel

Nur für die Feedquelle mit Host `realtime.gtfs.de` wird die Kombination aus
obiger Struktur, Fahrtbezug und einer vollständigen Herkunftsformulierung mit
Verarbeiter `GTFS.de` und Datenlieferant ausgefiltert. Unterstützt werden deutsche
und englische Herkunftsformulierungen; es gibt keinen Vergleich ausschließlich
mit einem konkreten deutschen Meldungstext und keinen pauschalen INFO-Filter.
Andere Quellen, Überschriften, konkrete Verkehrsauswirkungen oder zusätzliche
Störungssätze werden nicht über diese Regel ausgeblendet. Unbekannte Formate
bleiben konservativ sichtbar. Alle nichtleeren Übersetzungen müssen passen.

Die Filterung erfolgt vor dem Aufbau des Meldungsindexes. Fahrtupdates werden
unverändert verarbeitet. Am untersuchten Snapshot werden 42.933 Herkunftshinweise
entfernt und 6.943 übrige Alerts behalten.

## Deduplizierung und UI

Entity-IDs unterscheiden sich auch bei identischem Inhalt. Der Vergleichsschlüssel
besteht deshalb aus Überschrift und Beschreibung, Unicode-NFC-normalisiert und
mit vereinheitlichten Leerzeichen. Unterschiedliche Beschreibungen bleiben getrennt.

Im Backend wird **erst nach Selektor- und Gültigkeitsprüfung** je Station bzw.
Fahrt dedupliziert. So verliert eine Fahrt keine Meldung, nur weil derselbe Inhalt
auch einer anderen Fahrt zugeordnet ist. Die Oberfläche dedupliziert zusätzlich
über Stations- und sichtbare Fahrtmeldungen hinweg, ebenso in den Fahrtdetails.
Standardmäßig erscheint ausschließlich eine geschlossene, einzeilige Zusammenfassung
„⚠ 3 relevante Verkehrsmeldungen“ (28 Pixel hoch), keine einzelnen Meldungskarten.
Bei null Meldungen bleibt der Bereich leer und unsichtbar. Erst Aufklappen zeigt
die deduplizierte Textliste; ihre Höhe ist auch dann begrenzt. Fahrtmeldungen
werden zusätzlich durch ein kompaktes ⚠ vor dem Ziel markiert, die Details sind
über den Zielbutton erreichbar. Verkehrsmodus-, Linien- und Richtungsfilter
bestimmen die berücksichtigten Fahrten. Ein aufgeklappter Bereich bleibt bei
Datenaktualisierungen geöffnet, wird beim Wechsel des Auswahlkontexts aber wieder
geschlossen.

Tests prüfen den Schutz regulärer INFO-Meldungen, Quellenbindung, englische
Provenienz, gemischte Störungs-/Provenienztexte, Fahrtzuordnung, unterschiedliche
Beschreibungen sowie Zähler und Deduplizierung im Browser.
