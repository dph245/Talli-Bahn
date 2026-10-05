"""Passenger-facing attribution for the configured provider chain.

Evidence and unresolved licensing questions: docs/data-sources.md.
"""
from urllib.parse import urlsplit
from .providers.gtfs_static import GTFSStaticProvider
from .providers.demo import DemoProvider
from .providers.transport_rest import TransportRestProvider

STATIC = '''<section aria-labelledby="static-source">
<h2 id="static-source">Fahrplandaten · GTFS für Deutschland</h2>
<p>Die Fahrplandaten stammen von gtfs.de. Grundlage des Deutschland-Feeds ist der NeTEx-Datensatz von DELFI e.V.</p>
<p><a href="https://www.gtfs.de/de/feeds/">Quelle: GTFS für Deutschland</a> ·
<a href="https://creativecommons.org/licenses/by/4.0/">Lizenz: CC BY 4.0</a></p>
</section>'''
REALTIME = '''<section aria-labelledby="realtime-source">
<h2 id="realtime-source">Echtzeitdaten · gtfs.de</h2>
<p>Prognosen, Ausfälle und Verkehrsmeldungen stammen aus dem GTFS-Realtime-Stream von gtfs.de. Die beteiligten Datenlieferanten sind auf der Quellenseite aufgeführt.</p>
<p><a href="https://www.gtfs.de/de/realtime/">Quelle und Datenlieferanten</a> ·
<a href="https://creativecommons.org/licenses/by-sa/4.0/">Lizenz: CC BY-SA 4.0</a></p>
</section>'''
EFA = '''<section aria-labelledby="efa-source">
<h2 id="efa-source">Regionale Ergänzung · VRB-EFA</h2>
<p>Die elektronische Fahrplanauskunft des Verkehrsverbunds Region Braunschweig ergänzt verfügbare Abfahrtsprognosen und Steigangaben über die Schnittstelle auf bsvg.efa.de.</p>
<p><a href="https://auskunft.vrb-online.de/">Quelle: VRB-Fahrplanauskunft</a></p>
<p>Eine ausdrückliche Lizenz oder API-Nutzungsbedingung zur Nachnutzung dieser Daten ist in den geprüften Unterlagen nicht belegt. Deshalb wird hier keine offene Datenlizenz angegeben.</p>
</section>'''
TRANSPORT = '''<section aria-labelledby="transport-source">
<h2 id="transport-source">Gleisangaben · transport.rest</h2>
<p>Talli ergänzt Gleisangaben über v6.db.transport.rest. Der unabhängige Dienst greift auf Auskunftsdaten der Deutschen Bahn zu.</p>
<p><a href="https://v6.db.transport.rest/">Quelle und Nutzungshinweise des API-Betreibers</a></p>
<p>Der Betreiber dokumentiert Zugriff ohne Anmeldung und begrenzte Abrufraten. Eine offene Lizenz für die gelieferten Fahrplandaten ist dort nicht ausgewiesen.</p>
</section>'''


def source_sections(provider):
    transport = False
    while hasattr(provider, 'primary'):
        transport |= isinstance(provider, TransportRestProvider)
        provider = provider.primary
    sections = []
    if isinstance(provider, GTFSStaticProvider):
        sections.append(STATIC)
        realtime = getattr(provider, 'realtime', None)
        if realtime and urlsplit(realtime.url or '').hostname == 'realtime.gtfs.de':
            sections.append(REALTIME)
        if getattr(provider, 'efa', None) is not None:
            sections.append(EFA)
        if transport:
            sections.append(TRANSPORT)
        sections.append('<p class="source-note">Talli bereitet die Daten für die Anzeige auf, ordnet Ergänzungen dem Fahrplan zu und berechnet Verspätungsanzeigen. Die Angaben können je nach Quelle und Verfügbarkeit abweichen.</p>')
    elif isinstance(provider, DemoProvider):
        sections.append('<p>Diese Talli-Instanz zeigt fiktive Beispieldaten. Sie verwendet keine externen Fahrplandaten.</p>')
    else:
        sections.append('<p>Für diese Datenquelle sind hier noch keine Attributionsangaben hinterlegt.</p>')
    return '\n'.join(sections)
