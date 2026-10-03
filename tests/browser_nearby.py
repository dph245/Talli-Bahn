"""Browser regression for explicit, one-shot nearby search; no real location used."""
import shutil
import threading
import time

import uvicorn
from playwright.sync_api import sync_playwright, expect
from app.main import create_app
from app.providers import DemoProvider


def main():
    server = uvicorn.Server(uvicorn.Config(create_app(DemoProvider()), host='127.0.0.1', port=8767, log_level='error'))
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
            context.add_init_script('''
                window.geoCalls = [];
                Object.defineProperty(navigator, 'geolocation', {configurable:true, value:{
                    getCurrentPosition(success, failure, options) { geoCalls.push({success, failure, options}); }
                }});
            ''')
            page = context.new_page()
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            requests=[]
            hits=[dict(id='demo-alex',name='Berlin Alexanderplatz',distance_m=250)]
            def nearby(route):
                requests.append(route.request)
                route.fulfill(json=hits)
            page.route('**/api/stops/nearby',nearby)
            page.goto('http://127.0.0.1:8767')
            expect(page.locator('#journeys tr')).to_have_count(12)
            assert page.evaluate('geoCalls.length') == 0 and not requests
            page.locator('#nearby').click()
            assert page.evaluate('geoCalls.length') == 1
            assert not requests
            page.evaluate('geoCalls.at(-1).success({coords:{latitude:52.5,longitude:13.4}})')
            expect(page.locator('#search-results button')).to_contain_text('ca. 250 m Luftlinie')
            assert requests[0].method == 'POST' and '?' not in requests[0].url
            assert requests[0].post_data_json == dict(lat=52.5,lon=13.4)
            page.locator('#nearby').press('ArrowDown')
            expect(page.locator('#search-results button')).to_be_focused()
            page.keyboard.press('Enter')
            expect(page.locator('#station-name')).to_have_text('Berlin Alexanderplatz')
            saved=page.evaluate('JSON.parse(localStorage.getItem("talli:last-stop"))')
            assert saved == dict(id='demo-alex',name='Berlin Alexanderplatz')
            page.reload()
            expect(page.locator('#journeys tr')).to_have_count(12)
            assert page.evaluate('geoCalls.length') == 0
            for code,text in [(1,'abgelehnt'),(2,'nicht ermittelt'),(3,'dauert zu lange')]:
                page.locator('#nearby').click()
                page.evaluate('(code) => geoCalls.at(-1).failure({code})',code)
                expect(page.locator('#search-results')).to_contain_text(text)
            # Typing invalidates an outstanding location callback.
            page.locator('#nearby').click()
            page.locator('#search').fill('Alexander')
            expect(page.locator('#search-results button')).to_have_text('Berlin Alexanderplatz')
            before=len(requests)
            page.evaluate('geoCalls.at(-1).success({coords:{latitude:52,longitude:10}})')
            expect(page.locator('#search-results button')).to_have_text('Berlin Alexanderplatz')
            assert len(requests)==before
            # Escape also invalidates an outstanding location callback.
            page.locator('#nearby').click()
            page.keyboard.press('Escape')
            page.evaluate('geoCalls.at(-1).failure({code:1})')
            expect(page.locator('#search-results')).to_be_hidden()
            hits.clear()
            page.locator('#nearby').click()
            page.evaluate('geoCalls.at(-1).success({coords:{latitude:0,longitude:0}})')
            expect(page.locator('#search-results')).to_contain_text('Umkreis von 2 km')
            page.unroute('**/api/stops/nearby')
            page.route('**/api/stops/nearby',lambda r:r.fulfill(status=503,json={'detail':'unavailable'}))
            page.locator('#nearby').click()
            page.evaluate('geoCalls.at(-1).success({coords:{latitude:0,longitude:0}})')
            expect(page.locator('#search-results')).to_contain_text('Umgebungssuche nicht verfügbar')
            for width in [320,390,1440]:
                page.set_viewport_size({'width':width,'height':900})
                for theme in ['light','dark']:
                    page.evaluate('(theme) => document.documentElement.dataset.theme = theme',theme)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    assert page.locator('#nearby').bounding_box()['width'] > 70
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path='/tmp/talli-nearby-mobile.png',full_page=True)
            page.evaluate("Object.defineProperty(navigator,'geolocation',{value:undefined})")
            page.locator('#nearby').click()
            expect(page.locator('#search-results')).to_contain_text('Standortabfrage nicht verfügbar')
            assert not errors,errors
            browser.close()
            print('OK: explicit click only, reload, POST, selection, keyboard, location failures, stale callbacks, empty/unavailable results, mobile themes.')
    finally:
        server.should_exit=True
        thread.join(timeout=5)


if __name__=='__main__': main()
