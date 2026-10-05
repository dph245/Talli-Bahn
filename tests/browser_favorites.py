"""Explicit favorite repair across generations; no name/coordinate migration."""
import shutil
import threading
import time
import uvicorn
from playwright.sync_api import sync_playwright, expect
from app.main import create_app
from app.models import Stop
from app.providers import DemoProvider


class ChangingProvider(DemoProvider):
    generation = 'test:old'
    stations = [Stop(id='421683', name='Bad Harzburg, Bahnhof'),
                Stop(id='380494', name='Bad Harzburg'),
                Stop(id='255810', name='Bahnhof, Bad Harzburg')]
    calls = []

    def dataset_version(self):
        return self.generation

    async def board(self, stop_id, kind, now):
        self.calls.append(stop_id)
        board = await super().board(stop_id, kind, now)
        if stop_id == '255810':
            board.journeys = []
        return board


def main():
    provider = ChangingProvider()
    server = uvicorn.Server(uvicorn.Config(create_app(provider), host='127.0.0.1', port=8768, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started: break
            time.sleep(.05)
        assert server.started
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=shutil.which('chromium') or shutil.which('google-chrome'), headless=True)
            context = browser.new_context(viewport={'width':390,'height':844}, service_workers='block')
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://127.0.0.1:8768')
            expect(page.locator('#journeys tr')).to_have_count(12)
            page.evaluate('''() => {
                const old = {id:'380494',name:'Bad Harzburg'};
                localStorage.setItem('talli:favorites',JSON.stringify([old,{id:'421683',name:'Bad Harzburg, Bahnhof'}]));
                localStorage.setItem('talli:last-stop',JSON.stringify(old));
                localStorage.setItem('talli:auto','false');
            }''')
            provider.calls.clear()
            page.reload()
            expect(page.locator('#selection-repair')).to_be_visible()
            expect(page.locator('#favorites')).to_contain_text('Neu auswählen')
            expect(page.locator('#empty')).to_be_hidden()
            expect(page.locator('#journeys tr')).to_have_count(0)
            assert not provider.calls
            assert 'dataset_version' not in page.evaluate("JSON.parse(localStorage.getItem('talli:favorites'))[0]")
            page.locator('#selection-search').click()
            expect(page.locator('#search-results button')).to_have_count(3)
            page.locator('#search-results button').filter(has_text='Bad Harzburg').first.click()
            expect(page.locator('#journeys tr')).to_have_count(12)
            saved = page.evaluate("JSON.parse(localStorage.getItem('talli:favorites'))")
            assert saved[0] == dict(id='380494', name='Bad Harzburg', dataset_version='test:old')
            assert 'dataset_version' not in saved[1]  # No bulk or name migration.
            page.reload()
            expect(page.locator('#selection-repair')).to_be_hidden()
            expect(page.locator('#favorite')).to_have_attribute('aria-pressed','true')
            expect(page.locator('#journeys tr')).to_have_count(12)

            # Server import while the page remains open: old ID now means another place.
            provider.generation = 'test:new'
            provider.stations = [Stop(id='380494',name='Anderer Ort'),
                                 Stop(id='new-rail',name='Bad Harzburg'),
                                 Stop(id='255810',name='Bahnhof, Bad Harzburg')]
            provider.calls.clear()
            page.locator('#refresh').click()
            expect(page.locator('#selection-repair')).to_be_visible()
            expect(page.locator('#journeys tr')).to_have_count(0)
            assert not provider.calls  # API rejects stale ID before querying the board.
            page.reload()
            expect(page.locator('#selection-repair')).to_be_visible()
            page.locator('#selection-search').click()
            page.locator('#search-results button').filter(has_text='Bad Harzburg').first.click()
            expect(page.locator('#journeys tr')).to_have_count(12)
            assert page.evaluate("JSON.parse(localStorage.getItem('talli:last-stop')).id") == 'new-rail'
            assert page.evaluate("JSON.parse(localStorage.getItem('talli:favorites'))[0].id") == 'new-rail'

            # A truly empty current station is valid and remains so after reload.
            page.locator('#search').fill('Bahnhof, Bad Harzburg')
            page.locator('#search-results button').click()
            expect(page.locator('#empty')).to_be_visible()
            expect(page.locator('#selection-repair')).to_be_hidden()
            page.reload()
            expect(page.locator('#empty')).to_be_visible()
            expect(page.locator('#selection-repair')).to_be_hidden()
            page.locator('#favorites button').filter(has_text='Neu auswählen').click()
            page.locator('#selection-remove').click()
            expect(page.locator('#favorites button')).to_have_count(1)
            page.evaluate('''() => {
                const missing = {id:'gone',name:'Entfallene Station',dataset_version:'test:new'};
                localStorage.setItem('talli:favorites',JSON.stringify([missing]));
                localStorage.setItem('talli:last-stop',JSON.stringify(missing));
            }''')
            page.reload()
            expect(page.locator('#selection-repair')).to_be_visible()
            expect(page.locator('#empty')).to_be_hidden()
            page.locator('#selection-remove').click()
            assert page.evaluate("JSON.parse(localStorage.getItem('talli:last-stop'))") is None
            assert not errors, errors
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == '__main__':
    main()
