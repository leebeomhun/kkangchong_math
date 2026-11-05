"""Browser integration against an isolated database, never the production child's data."""
import json
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from sync_server import Handler, Store, SyncServer, empty_state
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = Path('/home/ubuntu/.codex/validation/kkangchong-sync-20261105')
OUT.mkdir(parents=True, exist_ok=True)
KEY = 'kkangchong-math-v1'


class TestHandler(Handler, SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        self.directory = str(ROOT)
        super().__init__(*args, **kwargs)

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == '/api/sync':
            self.headers['X-Kkangchong-Token'] = self.server.token
            Handler.do_GET(self)
        else:
            SimpleHTTPRequestHandler.do_GET(self)

    def do_POST(self):
        self.headers['X-Kkangchong-Token'] = self.server.token
        Handler.do_POST(self)


def run():
    with tempfile.TemporaryDirectory() as temp:
        store = Store(Path(temp) / 'progress.sqlite3')
        server = SyncServer(('127.0.0.1', 0), store, 'test-proxy-token', '', TestHandler)
        base = f'http://127.0.0.1:{server.server_port}'
        server.origin = base
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
                contexts = [browser.new_context(viewport={'width':390,'height':844}, reduced_motion='reduce') for _ in range(2)]
                a, b = [ctx.new_page() for ctx in contexts]
                errors = []
                for page in (a, b):
                    page.on('pageerror', lambda e: errors.append(str(e)))
                legacy = empty_state()
                legacy.update(stars=10, rounds=2, stickers={'🐶':2,'🐰':1})
                legacy['settings'].update(source='custom', custom=[[1,1]], modes=['balloon'], voice=False, sfx=False)
                a.add_init_script("if (!localStorage.getItem('" + KEY + "')) localStorage.setItem('" + KEY + "'," + json.dumps(json.dumps(legacy, ensure_ascii=False)) + ');')
                a.goto(base)

                def local(page):
                    return page.evaluate(f'JSON.parse(localStorage.getItem("{KEY}"))')

                def sync(page, stars=None):
                    page.evaluate("window.dispatchEvent(new Event('focus'))")
                    page.wait_for_function("!Object.keys(localStorage).some(k => k.startsWith('kkangchong-sync-event-v1:'))", timeout=20000)
                    if stars is not None:
                        page.wait_for_function(f'JSON.parse(localStorage.getItem("{KEY}")).stars === {stars}', timeout=20000)

                def home(page):
                    if page.locator('#reward').is_visible(): page.locator('#rewardHome').click()
                    elif page.locator('#book').is_visible(): page.locator('#bookBack').click()
                    elif page.locator('#parent').is_visible(): page.locator('#parentBack').click()

                def start_answer(page):
                    home(page)
                    page.locator('#startBtn').click(); page.locator('#setupGo').click()
                    page.wait_for_function('!document.querySelector("#choices").classList.contains("waiting")')
                    page.locator('#play .ans[data-v="2"]').click()

                def gift(page):
                    page.wait_for_selector('#reward:not([hidden]) .gift')
                    page.locator('#gifts').evaluate('(g) => {g.children[0].click();g.children[1].click();}')
                    page.wait_for_selector('#reveal:not([hidden]) .sticker-art')

                def play(page):
                    start_answer(page); gift(page)

                def parent(page):
                    home(page)
                    page.locator('#parentBtn').dispatch_event('pointerdown')
                    page.wait_for_selector('#parent:not([hidden])')
                    page.locator('#parentBtn').dispatch_event('pointerup')

                sync(a, 10)
                assert store.snapshot()['state']['stickers'] == {'🐶':2,'🐰':1}
                b.goto(base); sync(b, 10)
                assert local(a) == local(b)
                # Opening/reloading other devices must not multiply the legacy snapshot.
                b.reload(); sync(b, 10)
                assert store.snapshot()['state']['stars'] == 10
                start_answer(a); start_answer(b); gift(a); gift(b)
                sync(a, 12); sync(b, 12)
                assert store.snapshot()['state']['rounds'] == 4
                assert sum(store.snapshot()['state']['stickers'].values()) == 5
                # Persist offline work through an actual service-worker reload.
                a.wait_for_function('!!navigator.serviceWorker.controller')
                contexts[0].set_offline(True)
                play(a)
                assert local(a)['stars'] == 13
                a.reload()
                assert local(a)['stars'] == 13
                play(b); sync(b, 13)
                contexts[0].set_offline(False); sync(a, 14); sync(b, 14)
                assert store.snapshot()['state']['rounds'] == 6
                # The response can be lost after a durable commit: retry must award once.
                lost = {'count':0}
                def lose_response(route):
                    if route.request.method == 'POST':
                        route.fetch(); lost['count'] += 1; route.abort()
                    else: route.continue_()
                a.route('**/api/sync', lose_response)
                play(a)
                a.wait_for_timeout(400)
                assert lost['count'] > 0
                a.unroute('**/api/sync', lose_response)
                sync(a, 15); sync(b, 15)
                assert store.snapshot()['state']['rounds'] == 7
                # Two tabs share a persistent per-event journal, rather than replacing a queue.
                sibling = contexts[0].new_page()
                sibling.on('pageerror', lambda e: errors.append(str(e)))
                sibling.goto(base); sync(sibling, 15)
                start_answer(a); start_answer(sibling); gift(a); gift(sibling)
                sync(a, 17); sync(sibling, 17); sync(b, 17)
                sibling.close()
                # Offline changes to separate settings fields merge, not whole stale settings.
                contexts[0].set_offline(True)
                parent(a); a.locator('.chips[data-key="voice"] [data-v="true"]').click()
                parent(b); b.locator('.chips[data-key="sfx"] [data-v="true"]').click(); sync(b)
                contexts[0].set_offline(False); sync(a); sync(b)
                settings = store.snapshot()['state']['settings']
                assert settings['voice'] and settings['sfx']
                b.locator('.chips[data-key="voice"] [data-v="false"]').click()
                b.locator('.chips[data-key="sfx"] [data-v="false"]').click(); sync(b); sync(a)
                # An old offline device must not resurrect records after a parent reset.
                contexts[0].set_offline(True); play(a)
                parent(b); b.locator('#resetAsk').click(); b.locator('#resetYes').click(); sync(b, 0)
                contexts[0].set_offline(False); sync(a, 0)
                assert store.snapshot()['state']['stickers'] == {}
                assert a.evaluate("!!localStorage.getItem('kkangchong-sync-recovery-v1')")
                fresh = browser.new_context()
                c = fresh.new_page()
                c.on('pageerror', lambda e: errors.append(str(e)))
                c.route('**/api/sync', lambda route: route.abort())
                c.add_init_script("localStorage.setItem('" + KEY + "'," + json.dumps(json.dumps(legacy, ensure_ascii=False)) + ');')
                c.goto(base)
                play(c)
                assert local(c)['stars'] == 11
                c.unroute('**/api/sync'); sync(c, 0)
                assert store.snapshot()['state']['stars'] == 0
                fresh.close()
                # New play immediately after an offline reset belongs to the new generation.
                play(b); sync(b, 1)
                contexts[1].set_offline(True)
                parent(b); b.locator('#resetAsk').click(); b.locator('#resetYes').click()
                play(b)
                lost['count'] = 0
                b.route('**/api/sync', lose_response)
                contexts[1].set_offline(False)
                b.evaluate("window.dispatchEvent(new Event('focus'))")
                b.wait_for_timeout(500)
                assert lost['count'] > 0
                b.unroute('**/api/sync', lose_response)
                sync(b, 1); sync(a, 1)
                parent(a)
                assert a.locator('#syncStatus').get_attribute('data-status') == 'saved'
                a.locator('.parent-body').evaluate('(el) => el.scrollTop = 0')
                a.screenshot(path=str(OUT / 'parent-sync-phone.png'))
                # API reads are never stored by the service worker.
                assert not a.evaluate("async () => !!(await caches.match('/api/sync'))")
                assert a.evaluate("async () => !!(await caches.match('sync.js'))")
                assert not errors, errors
                result = dict(legacy_migration=True, two_devices=True, concurrent_awards=True, offline_reload=True,
                              offline_merge=True, lost_response_retry=True, concurrent_tabs=True, settings_patches=True,
                              reset_prevents_resurrection=True, legacy_blocked_after_reset=True,
                              unbound_old_work_blocked_after_reset=True, lost_reset_response=True,
                              play_after_offline_reset=True, api_never_cached=True, javascript_errors=errors)
                (OUT / 'validation.json').write_text(json.dumps(result, indent=2) + '\n')
                print(json.dumps(result))
                browser.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    run()
