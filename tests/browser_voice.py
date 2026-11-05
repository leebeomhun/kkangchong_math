"""강토끼 목소리(/api/tts) 브라우저 검증. Typecast는 부르지 않고 ffmpeg로 만든 짧은 mp3를 돌려줍니다."""
import json
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from sync_server import Handler, Store, SyncServer, empty_state
from voice import Voice
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
KEY = 'kkangchong-math-v1'
TONE = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.25',
                       '-ac', '1', '-b:a', '48k', '-f', 'mp3', 'pipe:1'], capture_output=True, check=True).stdout
made = []   # 서버가 Typecast 대신 만든 문장


class TestHandler(Handler, SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        self.directory = str(ROOT)
        super().__init__(*args, **kwargs)

    def log_message(self, *args):
        pass

    def do_GET(self):
        if urlsplit(self.path).path in ('/api/sync', '/api/tts'):
            self.headers['X-Kkangchong-Token'] = self.server.token
            if urlsplit(self.path).path == '/api/tts':
                self.server.asked.append(parse_qs(urlsplit(self.path).query).get('text', [''])[0])
            Handler.do_GET(self)
        else:
            SimpleHTTPRequestHandler.do_GET(self)

    def do_POST(self):
        self.headers['X-Kkangchong-Token'] = self.server.token
        Handler.do_POST(self)


SPY = """
window.__played = 0; window.__device = [];
const start = AudioBufferSourceNode.prototype.start;
AudioBufferSourceNode.prototype.start = function (...a) { window.__played++; return start.apply(this, a); };
window.SpeechSynthesisUtterance = class { constructor(t) { this.text = t; } };
Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
  speaking: false, pending: false, getVoices: () => [{ name: 'Yuna', lang: 'ko-KR' }], addEventListener() {}, cancel() {},
  speak(u) { window.__device.push(u.text); setTimeout(() => u.onend && u.onend(), 20); } } });
"""


def run():
    with tempfile.TemporaryDirectory() as temp:
        store = Store(Path(temp) / 'progress.sqlite3')
        voice = Voice(Path(temp) / 'tts', 'key', 'tc_test', monthly_limit=10_000, label='로로',
                      fetch=lambda text, tempo: made.append(text) or TONE)
        server = SyncServer(('127.0.0.1', 0), store, 'test-proxy-token', '', TestHandler, voice=voice)
        server.asked = []
        base = f'http://127.0.0.1:{server.server_port}'
        server.origin = base
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        state = empty_state()
        state['settings'].update(source='custom', custom=[[18, 2]], modes=['balloon'], voice=True, sfx=False)
        errors, result = [], {}
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--autoplay-policy=no-user-gesture-required'])
                ctx = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
                ctx.add_init_script(SPY + "if (!localStorage.getItem('" + KEY + "')) localStorage.setItem('" + KEY + "'," + json.dumps(json.dumps(state)) + ');')
                page = ctx.new_page()
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(base); page.wait_for_timeout(2500)   # 공통 문장 미리 받기
                warmed = len(made)
                page.locator('#startBtn').click()
                page.wait_for_function('window.__played >= 2', timeout=10000)
                page.locator('#play .ans[data-v="20"]').click()
                page.wait_for_function('window.__played >= 4', timeout=10000)
                result['natural_playback'] = page.evaluate('window.__played') >= 4 and page.evaluate('window.__device.length') == 0
                result['sentences_split'] = '18 더하기 2는 뭘까?' in made and '맞는 풍선을 터뜨려 봐!' in made and '18 더하기 2는 20!' in made
                result['common_lines_warmed'] = warmed >= 10 and '딩동댕!' in made[:warmed]
                result['each_sentence_made_once'] = len(made) == len(set(made))
                # 다시 열면 들었던 문장은 이 기기에 저장된 것으로 재생 (서버에 다시 묻지 않음)
                page.wait_for_selector('#reward:not([hidden]) .gift', timeout=15000)
                page.locator('#gifts .gift').first.click()
                page.wait_for_selector('#rewardActions:not([hidden])', timeout=5000)
                page.locator('#rewardHome').click()
                server.asked.clear()
                page.reload(); page.wait_for_timeout(800)
                page.locator('#startBtn').click()
                page.wait_for_function('window.__played >= 2', timeout=10000)
                result['cached_on_device'] = '18 더하기 2는 뭘까?' not in server.asked
                # 이번 달 한도를 넘으면 새 문장은 기기 목소리로, 보호자 메뉴에 이유 표시
                voice.limit = 0
                page.locator('#homeBtn').click(); page.locator('#quitYes').click()
                page.evaluate("document.querySelector('#parentBtn').dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}))")
                page.wait_for_selector('#parent:not([hidden])')
                page.evaluate("document.querySelector('#parentBtn').dispatchEvent(new PointerEvent('pointerup', {bubbles: true}))")
                page.locator('.chips[data-key="rate"] [data-v="fast"]').click()   # 빠르기가 바뀌면 새 음성이 필요함
                page.wait_for_function('window.__device.length >= 1', timeout=10000)
                page.wait_for_function("document.querySelector('#voiceNote').textContent.includes('사용량을 다 써서')", timeout=5000)
                result['budget_falls_back_to_device'] = True
                # 문제를 정해 두면 놀기 전에 그 문제의 말을 미리 만들어 둠 → 시작하자마자 기기에 저장된 목소리
                voice.limit = 10_000
                prepared = bv_state = empty_state()
                bv_state['settings'].update(source='custom', custom=[[15, 3]], modes=['hop'], voice=True, sfx=False)
                ctx2 = browser.new_context(reduced_motion='reduce')
                ctx2.add_init_script(SPY + "localStorage.setItem('" + KEY + "'," + json.dumps(json.dumps(bv_state)) + ');')
                page2 = ctx2.new_page(); page2.on('pageerror', lambda e: errors.append(str(e)))
                page2.goto(base)
                page2.wait_for_function("caches.open('kkangchong-voice-v1').then(c => c.match('/api/tts?t=1&text=' + encodeURIComponent('강토끼는 15에 있어.'))).then(r => !!r)", polling=500, timeout=30000)
                server.asked.clear()
                page2.locator('#startBtn').click()
                page2.wait_for_function('window.__played >= 1', timeout=10000)
                result['prepared_before_play'] = '강토끼는 15에 있어.' not in server.asked
                ctx2.close()
                result['javascript_errors'] = errors
                browser.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()
        print(json.dumps(result, ensure_ascii=False))
        assert all(v is True for k, v in result.items() if k != 'javascript_errors') and not errors, result


if __name__ == '__main__':
    run()
