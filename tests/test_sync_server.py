import concurrent.futures
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from sync_server import Invalid, Store, SyncServer, UnknownChild, empty_state


def event(kind='answer', **extra):
    value = dict(id=str(uuid.uuid4()), type=kind)
    if kind == 'answer':
        value.update(day='2026-11-05', a=1, b=1, first=True)
    value.update(extra)
    return value


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'progress.sqlite3'
        self.store = Store(self.path)
        self.epoch = self.store.snapshot()['epoch']

    def tearDown(self):
        self.temp.cleanup()

    def send(self, *events, epoch=None):
        return self.store.update(dict(epoch=epoch or self.epoch, events=list(events)))

    def test_retry_is_idempotent_and_reopen_is_durable(self):
        answer, sticker = event(), event('sticker', sticker='🐰')
        self.send(answer, sticker)
        self.send(answer, sticker)
        data = Store(self.path).snapshot()
        self.assertEqual(data['state']['stars'], 1)
        self.assertEqual(data['state']['stickers'], {'🐰': 1})
        self.assertEqual(data['revision'], 2)

    def test_photo_stickers_persist_across_retry_and_legacy_import(self):
        keys = ['friend-hug', 'friend-peace', 'friend-reading', 'friend-star', 'friend-carrot', 'friend-cheer']
        rewards = [event('sticker', sticker=key) for key in ['🐰', *keys]]
        self.send(*rewards)
        self.send(*rewards)
        old = empty_state()
        old['stickers'] = {'🐰': 3, 'friend-hug': 2, 'unknown': 10}
        self.send(event('legacy', snapshot=old))
        restored = Store(self.path).snapshot()['state']['stickers']
        self.assertEqual(restored, {'🐰': 3, **{key: 2 if key == 'friend-hug' else 1 for key in keys}})

    def test_transaction_rejects_invalid_or_reused_ids(self):
        good = event()
        with self.assertRaises(Invalid):
            self.send(good, event('sticker', sticker='unknown'))
        self.assertEqual(self.store.snapshot()['state']['stars'], 0)
        self.send(good)
        with self.assertRaises(Invalid):
            self.send(event(), {**good, 'first': False})
        self.assertEqual(self.store.snapshot()['state']['stars'], 1)

    def test_concurrent_devices_never_overwrite_each_other(self):
        operations = [event() for _ in range(40)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda e: self.send(e), operations + operations))
        self.assertTrue(all(status == 200 for status, _ in results))
        self.assertEqual(self.store.snapshot()['state']['stars'], 40)

    def test_legacy_maxima_and_first_settings(self):
        first = empty_state()
        first.update(stars=10, rounds=2, stickers={'🐰': 3})
        first['settings']['count'] = 8
        other = empty_state()
        other.update(stars=7, rounds=4, stickers={'🐶': 2, '🐰': 1})
        self.send(event('legacy', snapshot=first))
        self.send(event('legacy', snapshot=other))
        data = self.store.snapshot()['state']
        self.assertEqual((data['stars'], data['rounds']), (10, 4))
        self.assertEqual(data['stickers'], {'🐰': 3, '🐶': 2})
        self.assertEqual(data['settings']['count'], 8)

    def test_reset_rejects_old_devices_and_cannot_repeat(self):
        self.send(event(), event('settings', patch={'count': 9}))
        reset = event('reset')
        _, data = self.send(reset)
        self.assertNotEqual(data['epoch'], self.epoch)
        self.assertEqual(data['state']['stars'], 0)
        self.assertEqual(data['state']['settings']['count'], 9)
        self.assertEqual(self.send(event())[0], 409)
        self.send(event(), epoch=data['epoch'])
        self.assertEqual(self.send(reset)[0], 200)
        self.assertEqual(self.store.snapshot()['state']['stars'], 1)
        legacy = empty_state(); legacy['stars'] = 999
        self.send(event('legacy', snapshot=legacy), epoch=data['epoch'])
        self.assertEqual(self.store.snapshot()['state']['stars'], 1)

    def test_settings_patches_preserve_other_device_changes(self):
        self.send(event('settings', patch={'voice': False}))
        self.send(event('settings', patch={'count': 12}))
        s = self.store.snapshot()['state']['settings']
        self.assertEqual(s['count'], 12)
        self.assertFalse(s['voice'])

    def test_missed_and_log_follow_actual_answers(self):
        self.send(event(first=False), event(first=False), event(first=True), event('round', day='2026-11-05'))
        state = self.store.snapshot()['state']
        self.assertEqual(state['missed'], {'1+1': 1})
        self.assertEqual(state['log']['2026-11-05'], dict(solved=3, first=1, rounds=1))

    def test_answer_stats_split_by_carry_addend_and_mode(self):
        self.send(event(a=18, b=2, first=True, mode='hop'), event(a=18, b=2, first=False, mode='hop'),
                  event(a=11, b=3, first=True, mode='mole'), event(a=5, b=2, first=True))
        row = self.store.snapshot()['state']['stats']['2026-11-05']
        self.assertEqual(row['carry'], [2, 1])
        self.assertEqual(row['plain'], [2, 2])
        self.assertEqual(row['add'], {'2': [3, 2], '3': [1, 1]})
        self.assertEqual(row['mode'], {'hop': [2, 1], 'mole': [1, 1]})
        with self.assertRaises(Invalid):
            self.send(event(mode='unknown'))

    def test_old_state_without_stats_still_accepts_answers(self):
        with self.store.connect() as db:
            data = json.loads(db.execute('SELECT data FROM profile WHERE id=1').fetchone()[0])
            del data['state']['stats']
            db.execute('UPDATE profile SET data=? WHERE id=1', (json.dumps(data),))
        self.send(event(a=9, b=1, mode='fish'))
        self.assertEqual(self.store.snapshot()['state']['stats']['2026-11-05']['carry'], [1, 1])

    def test_records_from_older_versions_get_new_defaults(self):
        with self.store.connect() as db:
            data = json.loads(db.execute('SELECT data FROM profile WHERE id=1').fetchone()[0])
            del data['state']['settings']['rate']; del data['state']['stats']
            data['state']['settings']['count'] = 7
            db.execute('UPDATE profile SET data=? WHERE id=1', (json.dumps(data),))
        state = self.store.snapshot()['state']
        self.assertEqual((state['settings']['rate'], state['settings']['count'], state['stats']), ('normal', 7, {}))

    def test_rate_setting_is_validated(self):
        self.send(event('settings', patch={'rate': 'slow'}))
        self.assertEqual(self.store.snapshot()['state']['settings']['rate'], 'slow')
        self.send(event('settings', patch={'rate': 'warp'}))
        self.assertEqual(self.store.snapshot()['state']['settings']['rate'], 'normal')

    def test_children_keep_separate_records(self):
        self.assertEqual(self.store.snapshot()['children'], [dict(id='main', name='')])
        self.send(event())
        created = self.store.manage(dict(op='addChild', name='  둘째  '))['created']
        other = self.store.snapshot(created)
        self.assertEqual(other['state']['stars'], 0)
        self.store.update(dict(child=created, epoch=other['epoch'], events=[event(), event()]))
        self.assertEqual(self.store.snapshot(created)['state']['stars'], 2)
        self.assertEqual(self.store.snapshot()['state']['stars'], 1)
        self.store.manage(dict(op='renameChild', child='main', name='첫째'))
        names = [c['name'] for c in Store(self.path).snapshot()['children']]
        self.assertEqual(names, ['첫째', '둘째'])
        with self.assertRaises(Invalid):
            self.store.manage(dict(op='removeChild', child='main'))
        with self.assertRaises(Invalid):
            self.store.manage(dict(op='renameChild', child='main', name=''))
        self.store.manage(dict(op='removeChild', child=created))
        with self.assertRaises(UnknownChild):
            self.store.snapshot(created)
        self.assertEqual(self.store.snapshot()['state']['stars'], 1)

    def test_child_limit(self):
        for n in range(5):
            self.store.manage(dict(op='addChild', name=f'아이{n}'))
        with self.assertRaises(Invalid):
            self.store.manage(dict(op='addChild', name='한 명 더'))

    def test_empty_new_device_does_not_block_real_legacy_settings(self):
        self.send(event('legacy', snapshot=empty_state()))
        old = empty_state(); old['stars'] = 3; old['settings']['count'] = 12
        self.send(event('legacy', snapshot=old))
        self.assertEqual(self.store.snapshot()['state']['settings']['count'], 12)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        StoreTests.setUp(self)
        self.token = 'private-test-token-' * 3
        self.origin = 'https://aiedu.codesearch.ai.kr'
        self.server = SyncServer(('127.0.0.1', 0), self.store, self.token, self.origin)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/api/sync'

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        StoreTests.tearDown(self)

    def request(self, body=None, **headers):
        payload = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url, data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, response.headers, json.load(response)
        except urllib.error.HTTPError as response:
            return response.code, response.headers, json.load(response)

    def test_api_requires_proxy_token_and_post_origin(self):
        self.assertEqual(self.request()[0], 403)
        headers = {'X-Kkangchong-Token': self.token}
        status, response_headers, _ = self.request(**headers)
        self.assertEqual(status, 200)
        self.assertEqual(response_headers['Cache-Control'], 'no-store')
        body = dict(epoch=self.epoch, events=[event()])
        self.assertEqual(self.request(body, **headers)[0], 403)
        headers.update({'Origin': 'https://other.invalid', 'Content-Type': 'application/json'})
        self.assertEqual(self.request(body, **headers)[0], 403)
        headers['Origin'] = self.origin
        self.assertEqual(self.request(body, **headers)[0], 200)

    def test_child_query_and_operations(self):
        headers = {'X-Kkangchong-Token': self.token, 'Origin': self.origin, 'Content-Type': 'application/json'}
        status, _, data = self.request(dict(op='addChild', name='둘째'), **headers)
        self.assertEqual(status, 200)
        child = data['created']
        url = self.url
        self.url = f'{url}?child={child}'
        status, _, data = self.request(**{'X-Kkangchong-Token': self.token})
        self.assertEqual((status, data['state']['stars'], len(data['children'])), (200, 0, 2))
        self.url = f'{url}?child=nobody'
        self.assertEqual(self.request(**{'X-Kkangchong-Token': self.token})[0], 410)
        self.url = url
        body = dict(child='nobody', epoch=self.epoch, events=[event()])
        self.assertEqual(self.request(body, **headers)[0], 410)


if __name__ == '__main__':
    unittest.main()
