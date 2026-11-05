#!/usr/bin/env python3
"""Private, single-child progress store. nginx handles the existing parent login."""
import copy
import hmac
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STICKERS = ['🐶','🐱','🐰','🦊','🐻','🐼','🐨','🐯','🦁','🐷','🐸','🐧','🐥','🦄','🐳','🐬','🦋','🐞','🌈','🌸','🌻','🍓','🍒','🍰','🧁','🍭','🍦','🎀','👑','💎',
            'friend-hug','friend-peace','friend-reading','friend-star','friend-carrot','friend-cheer']
MODES = ['hop', 'candy', 'fish', 'rocket', 'balloon', 'mole', 'carrot']
SETTINGS = dict(v=2, source='range', addends=[2], **{'from': 10}, to=19, carry='mix', count=5,
                custom=[], order='list', modes=MODES, voice=True, sfx=True)


def empty_state():
    return dict(stars=0, rounds=0, stickers={}, log={}, missed={}, settings=copy.deepcopy(SETTINGS))


def number(value, maximum=1_000_000_000):
    return min(maximum, max(0, value)) if type(value) is int else 0


def problem(a, b):
    return type(a) is int and type(b) is int and 0 <= a <= 99 and 1 <= b <= 9


def day_key(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def clean_settings(raw):
    raw = raw if isinstance(raw, dict) else {}
    s = copy.deepcopy(SETTINGS)
    s['source'] = 'custom' if raw.get('source') == 'custom' else 'range'
    s['order'] = 'shuffle' if raw.get('order') == 'shuffle' else 'list'
    s['carry'] = raw.get('carry') if raw.get('carry') in ('mix', 'no', 'only') else 'mix'
    for field in ('from', 'to', 'count'):
        value = raw.get(field, s[field])
        s[field] = max(1 if field == 'count' else 0, number(value, 30 if field == 'count' else 99))
    if s['from'] > s['to']:
        s['from'], s['to'] = s['to'], s['from']
    for field, allowed, fallback in [('addends', [1,2,3,4,5], [2]), ('modes', MODES, MODES)]:
        values = raw.get(field, fallback)
        s[field] = [v for v in allowed if isinstance(values, list) and v in values] or fallback.copy()
    values = raw.get('custom', [])
    s['custom'] = [p for p in values if isinstance(p, list) and len(p) == 2 and problem(*p)][:30] if isinstance(values, list) else []
    for field in ('voice', 'sfx'):
        s[field] = raw.get(field) is not False
    return s


def clean_state(raw):
    raw = raw if isinstance(raw, dict) else {}
    s = empty_state()
    for field in ('stars', 'rounds'):
        s[field] = number(raw.get(field))
    stickers = raw.get('stickers', {})
    if isinstance(stickers, dict):
        s['stickers'] = {k: number(stickers[k]) for k in STICKERS if number(stickers.get(k))}
    log = raw.get('log', {})
    if isinstance(log, dict):
        for day in sorted(k for k in log if day_key(k))[-60:]:
            row = log[day] if isinstance(log[day], dict) else {}
            solved = number(row.get('solved'))
            s['log'][day] = dict(solved=solved, first=min(solved, number(row.get('first'))), rounds=number(row.get('rounds')))
    missed = raw.get('missed', {})
    if isinstance(missed, dict):
        for key, value in missed.items():
            if re.fullmatch(r'\d{1,2}\+[1-9]', key) and number(value, 3):
                s['missed'][key] = number(value, 3)
    s['settings'] = clean_settings(raw.get('settings'))
    return s


def merge_legacy(state, snapshot, include_settings=False):
    """Old snapshots have no event IDs: merge maxima, never repeatedly add totals."""
    other = clean_state(snapshot)
    for field in ('stars', 'rounds'):
        state[field] = max(state[field], other[field])
    for field in ('stickers', 'missed'):
        for key, value in other[field].items():
            state[field][key] = max(state[field].get(key, 0), value)
    for day, row in other['log'].items():
        target = state['log'].setdefault(day, dict(solved=0, first=0, rounds=0))
        for field, value in row.items():
            target[field] = max(target[field], value)
    if include_settings:
        state['settings'] = other['settings']


class Invalid(ValueError):
    pass


def validate_event(event):
    if not isinstance(event, dict) or not isinstance(event.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9:_-]{1,100}', event['id']):
        raise Invalid('Invalid event ID')
    kind = event.get('type')
    if kind == 'answer':
        if not problem(event.get('a'), event.get('b')) or type(event.get('first')) is not bool or not day_key(event.get('day')):
            raise Invalid('Invalid answer')
    elif kind == 'round':
        if not day_key(event.get('day')):
            raise Invalid('Invalid day')
    elif kind == 'sticker':
        if event.get('sticker') not in STICKERS:
            raise Invalid('Invalid sticker')
    elif kind == 'settings':
        if not isinstance(event.get('patch'), dict) or not event['patch'] or set(event['patch']) - set(SETTINGS):
            raise Invalid('Invalid settings')
    elif kind == 'legacy':
        if not isinstance(event.get('snapshot'), dict):
            raise Invalid('Invalid legacy snapshot')
    elif kind != 'reset':
        raise Invalid('Unknown event')


def apply_event(state, event, initial=False):
    kind = event['type']
    if kind == 'legacy':
        merge_legacy(state, event['snapshot'], initial)
    elif kind == 'settings':
        state['settings'] = clean_settings({**state['settings'], **event['patch']})
    elif kind == 'sticker':
        key = event['sticker']
        state['stickers'][key] = state['stickers'].get(key, 0) + 1
    elif kind in ('answer', 'round'):
        row = state['log'].setdefault(event['day'], dict(solved=0, first=0, rounds=0))
        if kind == 'round':
            state['rounds'] += 1
            row['rounds'] += 1
        else:
            state['stars'] += 1
            row['solved'] += 1
            row['first'] += int(event['first'])
            key = f"{event['a']}+{event['b']}"
            if event['first']:
                if state['missed'].get(key, 0) > 1:
                    state['missed'][key] -= 1
                else:
                    state['missed'].pop(key, None)
            else:
                state['missed'][key] = min(3, state['missed'].get(key, 0) + 1)
    for day in sorted(state['log'])[:-60]:
        del state['log'][day]


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS profile (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            payload = dict(epoch=str(uuid.uuid4()), revision=0, resetId=None, legacyAllowed=True, settingsSet=False,
                           state=empty_state(), updatedAt=None)
            db.execute('INSERT OR IGNORE INTO profile VALUES (1, ?)', (json.dumps(payload, ensure_ascii=False),))
        os.chmod(path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    def snapshot(self):
        with self.connect() as db:
            return json.loads(db.execute('SELECT data FROM profile WHERE id=1').fetchone()[0])

    def update(self, body):
        if not isinstance(body, dict) or not isinstance(body.get('events'), list) or not 1 <= len(body['events']) <= 100:
            raise Invalid('Expected 1 to 100 events')
        events = body['events']
        for event in events:
            validate_event(event)
        if len({e['id'] for e in events}) != len(events):
            raise Invalid('Duplicate IDs in batch')
        if any(e['type'] == 'reset' for e in events[:-1]):
            raise Invalid('Reset must end the batch')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            data = json.loads(db.execute('SELECT data FROM profile WHERE id=1').fetchone()[0])
            new = []
            for event in events:
                payload = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                receipt = db.execute('SELECT payload FROM receipts WHERE id=?', (event['id'],)).fetchone()
                if receipt:
                    if receipt[0] != payload:
                        raise Invalid('Event ID reused with different contents')
                else:
                    new.append((event, payload))
            if new and body.get('epoch') != data['epoch']:
                return 409, data
            for event, payload in new:
                if event['type'] == 'legacy' and not data['legacyAllowed']:
                    continue
                if event['type'] == 'reset':
                    settings = data['state']['settings']
                    data['state'] = empty_state()
                    data['state']['settings'] = settings
                    data['epoch'] = str(uuid.uuid4())
                    data['resetId'] = event['id']
                    data['legacyAllowed'] = False
                else:
                    initial_settings = False
                    if event['type'] == 'legacy' and not data.get('settingsSet', False):
                        old = clean_state(event['snapshot'])
                        initial_settings = bool(old['stars'] or old['rounds'] or old['stickers'] or old['log'] or old['settings'] != SETTINGS)
                    apply_event(data['state'], event, initial=initial_settings)
                    if initial_settings or event['type'] == 'settings':
                        data['settingsSet'] = True
                db.execute('INSERT INTO receipts VALUES (?, ?)', (event['id'], payload))
                data['revision'] += 1
            if new:
                data['updatedAt'] = datetime.now(timezone.utc).isoformat()
                db.execute('UPDATE profile SET data=? WHERE id=1', (json.dumps(data, ensure_ascii=False),))
            return 200, data


class SyncServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store, token, origin, handler=None):
        self.store, self.token, self.origin = store, token, origin
        super().__init__(address, handler or Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Never log cookies, bodies, or the private proxy token.
        print(f'{self.command} {self.path.split("?")[0]} {args[1] if len(args) > 1 else ""}', flush=True)

    def reply(self, status, body):
        payload = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def authorize(self):
        if self.path != '/api/sync':
            self.reply(404, dict(error='Not found'))
            return False
        if not hmac.compare_digest(self.headers.get('X-Kkangchong-Token', ''), self.server.token):
            self.reply(403, dict(error='Forbidden'))
            return False
        origin = self.headers.get('Origin')
        if (self.command == 'POST' and origin != self.server.origin) or (origin and origin != self.server.origin):
            self.reply(403, dict(error='Invalid origin'))
            return False
        return True

    def do_GET(self):
        if self.authorize():
            try:
                self.reply(200, self.server.store.snapshot())
            except sqlite3.Error:
                self.reply(503, dict(error='Storage unavailable'))

    def do_POST(self):
        if not self.authorize():
            return
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            self.reply(415, dict(error='Expected JSON'))
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 262144:
                self.reply(413, dict(error='Body too large'))
                return
            body = json.loads(self.rfile.read(length))
            status, data = self.server.store.update(body)
            self.reply(status, data)
        except (ValueError, UnicodeError, RecursionError) as error:
            self.reply(400, dict(error=str(error)[:100]))
        except sqlite3.Error:
            self.reply(503, dict(error='Storage unavailable'))


def main():
    os.umask(0o077)
    data = Path(os.environ.get('KK_SYNC_DATA', str(Path.home() / '.local/share/kkangchong-math')))
    token_file = Path(os.environ.get('KK_SYNC_TOKEN_FILE', str(Path.home() / '.config/kkangchong-math/sync-token')))
    token = token_file.read_text().strip()
    if len(token) < 32:
        raise RuntimeError('Private proxy token is missing')
    store = Store(data / 'progress.sqlite3')
    server = SyncServer(('127.0.0.1', int(os.environ.get('KK_SYNC_PORT', '8871'))), store, token,
                        os.environ.get('KK_SYNC_ORIGIN', 'https://aiedu.codesearch.ai.kr'))
    print(f'Progress sync listening on 127.0.0.1:{server.server_port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
