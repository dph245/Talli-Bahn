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
            page.locator('#alerts summary').click()
            expect(page.locator('#alerts p')).to_be_visible()
            expect(page.locator('#journeys tr').first).to_contain_text('Nach Fahrplan')
            expect(page.locator('#journeys tr').first).to_contain_text('Testbetreiber')
            expect(page.locator('#journeys tr').first).to_contain_text('statt 1')
            expect(page.locator('#journeys tr').first.locator('.expected')).to_have_count(0)
            expect(page.locator('#error')).to_be_hidden()
            assert not errors, errors
            browser.close()
            print('Browser OK: Suche, Favoriten, Ankünfte, Filter, Design, Mobilansicht, Offline, Sollzeit-Fallback, Meldungen, Betreiber, Gleiswechsel.')
    finally:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == '__main__':
    main()
