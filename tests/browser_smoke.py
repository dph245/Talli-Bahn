"""Optional UI smoke test: pip install playwright; uses installed Chromium."""
import threading
import time
import shutil
import uvicorn
from playwright.sync_api import sync_playwright, expect
from app.main import create_app
from app.providers import DemoProvider


def main():
    server = uvicorn.Server(uvicorn.Config(create_app(DemoProvider()), host='127.0.0.1', port=8765, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.05)
    assert server.started
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=shutil.which('chromium') or shutil.which('google-chrome'), headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1200})
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://127.0.0.1:8765')
            expect(page.locator('h1')).to_have_text('Abfahrten. Ohne Drama.')
            expect(page.locator('#journeys tr')).to_have_count(12)
            expect(page.locator('#source-badge')).to_contain_text('DEMO')
            page.locator('#favorite').click()
            expect(page.locator('#favorites button')).to_have_count(1)
            page.reload()
            expect(page.locator('#favorite')).to_have_attribute('aria-pressed', 'true')
            page.locator('[data-mode="bus"]').click()
            expect(page.locator('#journeys tr')).to_have_count(2)
            page.locator('#line').select_option('120')
            expect(page.locator('#journeys tr')).to_have_count(1)
            page.locator('[data-mode="rail"]').click()
            expect(page.locator('#empty')).to_be_visible()
            page.locator('#reset-filters').click()
            expect(page.locator('#journeys tr')).to_have_count(12)
            page.locator('#arrivals').click()
            expect(page.locator('#direction-title')).to_have_text('HERKUNFT')
            expect(page.locator('#journeys tr')).to_have_count(12)
            expect(page.locator('#journeys')).to_contain_text('Potsdam Hbf')
            page.locator('#departures').click()
            expect(page.locator('#journeys tr')).to_have_count(12)
            page.locator('#search').fill('Alexander')
            page.locator('#search-results button').click()
            expect(page.locator('#station-name')).to_have_text('Berlin Alexanderplatz')
            expect(page.locator('#journeys tr')).to_have_count(12)
            page.locator('#theme').click()
            expect(page.locator('html')).to_have_attribute('data-theme', 'light')
            page.reload()
            expect(page.locator('html')).to_have_attribute('data-theme', 'light')
            expect(page.locator('#station-name')).to_have_text('Berlin Alexanderplatz')
            expect(page.locator('#journeys tr')).to_have_count(12)
            page.locator('#theme').click()
            page.screenshot(path='/tmp/talli-desktop.png', full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.screenshot(path='/tmp/talli-mobile.png', full_page=True)
            page.locator('#favorite').click()
            expect(page.locator('#favorites button')).to_have_count(2)
            page.locator('#auto').uncheck(force=True)
            page.evaluate('navigator.serviceWorker.ready')
            context.set_offline(True)
            expect(page.locator('#error')).to_contain_text('offline')
            expect(page.locator('#count')).to_contain_text('veraltet')
            context.set_offline(False)
            expect(page.locator('#error')).to_be_hidden()
            board = page.request.get('http://127.0.0.1:8765/api/board?stop_id=demo-alex').json()
            board['journeys'][0]['realtime'] = None
            board['journeys'][0]['delay_minutes'] = None
            board['journeys'][0]['operator'] = 'Testbetreiber'
            board['journeys'][0]['scheduled_platform'] = '1'
            board['journeys'][0]['platform'] = '9'
            board['alerts'] = [{'id': 'test-alert', 'header': '<img src=x onerror=alert(1)>',
                                'description': 'Bauarbeiten am Bahnhof', 'source': 'GTFS-Realtime'}]
            page.route('**/api/board?*', lambda route: route.fulfill(json=board))
            page.locator('#refresh').click()
            expect(page.locator('#alerts')).to_contain_text('<img src=x onerror=alert(1)>')
            expect(page.locator('#alerts img')).to_have_count(0)
            expect(page.locator('#alerts .alerts-count')).to_have_text('⚠ 1 relevante Verkehrsmeldung')
            expect(page.locator('#alerts p')).not_to_be_visible()
            assert page.locator('#alerts').bounding_box()['height'] <= 30
            page.locator('#alerts summary').click()
            expect(page.locator('#alerts p')).to_be_visible()
            expect(page.locator('#journeys tr').first).to_contain_text('Plan')
            page.locator('#journeys tr').first.locator('.destination').click()
            expect(page.locator('#journey-detail')).to_contain_text('Testbetreiber')
            page.locator('#detail-close').click()
            expect(page.locator('#journeys tr').first).to_contain_text('statt 1')
            expect(page.locator('#journeys tr').first.locator('.expected')).to_have_count(0)
            expect(page.locator('#error')).to_be_hidden()
            original_alert = board['alerts'][0]
            board['alerts'] += [dict(original_alert, id='duplicate'),
                                dict(original_alert, id='different', description='Anderer Ersatzhalt'),
                                dict(original_alert, id='third', header='Aufzug defekt')]
            board['journeys'][0]['alerts'] = [dict(original_alert, id='trip-copy'),
                                             dict(original_alert, id='trip-copy-2')]
            page.locator('#refresh').click()
            expect(page.locator('#alerts .alerts-count')).to_have_text('⚠ 3 relevante Verkehrsmeldungen')
            expect(page.locator('#alerts .alerts-list li')).to_have_count(3)
            page.locator('#journeys tr').first.locator('.destination').click()
            expect(page.locator('#detail-body')).to_contain_text('Bauarbeiten am Bahnhof')
            assert page.locator('#detail-body').inner_text().count('Bauarbeiten am Bahnhof') == 1
            page.locator('#detail-close').click()
            # Only the visible journey carries this alert; other modes must hide it.
            board['alerts'] = []
            page.locator('#refresh').click()
            expect(page.locator('#alerts .alerts-count')).to_have_text('⚠ 1 relevante Verkehrsmeldung')
            expect(page.locator('#journeys .service-note')).to_have_count(1)
            expect(page.locator('#journeys .service-note')).to_have_text('⚠ ')
            page.locator('[data-mode="bus"]').click()
            expect(page.locator('#alerts')).to_be_hidden()
            expect(page.locator('#alerts')).to_be_empty()
            expect(page.locator('#journeys .service-note')).to_have_count(0)
            page.locator('[data-mode="all"]').click()
            expect(page.locator('#alerts .alerts-list')).not_to_be_visible()
            page.locator('#line').select_option(board['journeys'][1]['line'])
            expect(page.locator('#alerts')).to_be_hidden()
            page.locator('#line').select_option('')
            expect(page.locator('#alerts .alerts-count')).to_be_visible()
            page.locator('#alerts summary').focus()
            page.keyboard.press('Enter')
            expect(page.locator('#alerts .alerts-list')).to_be_visible()
            page.keyboard.press('Enter')
            expect(page.locator('#alerts .alerts-list')).not_to_be_visible()
            compact_height = page.locator('#alerts').bounding_box()['height']
            board['alerts'] = [dict(original_alert, id=f'many-{i}', header=f'Meldung {i}') for i in range(30)]
            page.locator('#refresh').click()
            expect(page.locator('#alerts .alerts-count')).to_have_text('⚠ 31 relevante Verkehrsmeldungen')
            assert page.locator('#alerts').bounding_box()['height'] == compact_height
            expect(page.locator('#alerts .alerts-list')).not_to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.screenshot(path='/tmp/talli-mobile-alerts.png', full_page=False)
            board['journeys'][0]['alerts'] = []
            # Hold realtime open: the static table must already be visible and usable.
            page.unroute('**/api/board?*')
            board['demo'] = False
            board['alerts'] = []
            base = board['journeys'][0]
            board['journeys'] = [dict(base, id=f'density-{i}') for i in range(40)]
            pending = []
            def staged(route):
                if 'realtime=true' in route.request.url:
                    pending.append(route)
                else:
                    route.fulfill(json=board)
            page.route('**/api/board?*', staged)
            page.set_viewport_size({'width': 1920, 'height': 1080})
            page.locator('#refresh').click()
            expect(page.locator('#journeys tr')).to_have_count(40)
            expect(page.locator('#updated')).to_contain_text('Echtzeit lädt')
            expect(page.locator('#board-panel')).to_have_attribute('aria-busy', 'false')
            visible = page.locator('#journeys tr').evaluate_all('(rows) => rows.filter(r => r.getBoundingClientRect().bottom <= innerHeight).length')
            assert visible >= 15, visible
            page.screenshot(path='/tmp/talli-desktop.png', full_page=False)
            page.set_viewport_size({'width': 2560, 'height': 1440})
            larger = page.locator('#journeys tr').evaluate_all('(rows) => rows.filter(r => r.getBoundingClientRect().bottom <= innerHeight).length')
            assert larger > visible, (visible, larger)
            for route in pending:
                route.abort()
            expect(page.locator('#updated')).to_contain_text('Echtzeit nicht verfügbar')
            expect(page.locator('#journeys tr')).to_have_count(40)
            expect(page.locator('#error')).to_be_hidden()
            pending.clear()
            page.locator('#refresh').click()
            expect(page.locator('#updated')).to_contain_text('Echtzeit lädt')
            page.wait_for_timeout(100)
            assert pending
            for route in pending:
                route.fulfill(json=dict(board, realtime_status='loading'))
            pending.clear()
            expect(page.locator('#updated')).to_contain_text('Echtzeit lädt')
            # Loading must be polled even with the Auto switch disabled.
            page.wait_for_timeout(2500)
            enriched = dict(board, source='GTFS + GTFS-Realtime', realtime_status='available', journeys=[
                dict(j, realtime=j['scheduled'], delay_minutes=0) for j in board['journeys']])
            assert pending
            for route in pending:
                route.fulfill(json=enriched)
            expect(page.locator('#source-badge')).to_have_text('GTFS + GTFS-Realtime')
            expect(page.locator('#journeys tr').first).to_contain_text('Pünktlich')
            pending.clear()
            # The static response omits a delayed journey: no disappearing row while RT loads.
            from datetime import datetime, timedelta, timezone
            now = datetime.now(timezone.utc)
            delayed = dict(enriched['journeys'][0], id='delayed', line='DELAYED',
                           scheduled=(now - timedelta(minutes=5)).isoformat(),
                           realtime=(now + timedelta(minutes=5)).isoformat(), delay_minutes=10)
            enriched['journeys'] = [delayed, enriched['journeys'][1]]
            page.locator('#refresh').click()
            page.wait_for_timeout(100)
            assert pending
            for route in pending:
                route.fulfill(json=enriched)
            pending.clear()
            expect(page.locator('#journeys')).to_contain_text('DELAYED')
            board['journeys'] = [enriched['journeys'][1] | {'realtime': None, 'delay_minutes': None}]
            page.locator('#refresh').click()
            page.wait_for_timeout(100)
            assert pending
            expect(page.locator('#journeys')).to_contain_text('DELAYED')
            expect(page.locator('#journeys tr')).to_have_count(2)
            for route in pending:
                route.abort()
            pending.clear()
            expect(page.locator('#updated')).to_contain_text('Echtzeit nicht verfügbar')
            expect(page.locator('#journeys tr')).to_have_count(1)
            expect(page.locator('#journeys')).not_to_contain_text('DELAYED')
            expect(page.locator('#journeys')).to_contain_text('Plan')
            print(f'Density: {visible} rows at 1920x1080; {larger} at 2560x1440. Static board survives realtime failure.')
            assert not errors, errors
            browser.close()
            print('Browser OK: Suche, Favoriten, Ankünfte, Filter, Design, Mobilansicht, Offline, Sollzeit-Fallback, Meldungen, Betreiber, Gleiswechsel.')
    finally:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == '__main__':
    main()
