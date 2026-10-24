import concurrent.futures
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import Bridge, start_http
from prepare import HOOK, patch_build


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.bridge = Bridge('t'*43)
        self.client = 'a'*32
        self.bridge.next_job(self.client)

    def take_job(self):
        deadline = time.monotonic()+2
        while self.bridge.jobs.empty() and time.monotonic() < deadline:
            time.sleep(.005)
        return self.bridge.next_job(self.client)

    def test_round_trip_and_at_most_once_delivery(self):
        with concurrent.futures.ThreadPoolExecutor() as pool:
            f = pool.submit(self.bridge.request, 'save', 'https://example.org/article')
            job = self.take_job()
            self.assertEqual(job['expected_url'], 'https://example.org/article')
            self.assertIsNone(self.bridge.next_job(self.client))
            self.bridge.complete(self.client, job['id'], {'ok': True, 'status': 'save_requested'})
            self.assertEqual(f.result()['status'], 'save_requested')
        with self.assertRaises(ValueError):
            self.bridge.complete(self.client, job['id'], {'ok': True})

    def test_delivered_timeout_is_not_retried(self):
        with concurrent.futures.ThreadPoolExecutor() as pool:
            f = pool.submit(self.bridge.request, 'save', 'https://example.org', .05)
            job = self.take_job()
            with self.assertRaisesRegex(RuntimeError, 'Outcome unknown'):
                f.result()
        self.assertIsNone(self.bridge.next_job(self.client))
        with self.assertRaises(ValueError):
            self.bridge.complete(self.client, job['id'], {'ok': True})

    def test_undelivered_commands_expire(self):
        with self.assertRaisesRegex(RuntimeError, 'queued command expired'):
            self.bridge.request('save', 'https://example.org', .01)
        self.assertIsNone(self.bridge.next_job(self.client))

    def test_offline_and_second_client_fail_closed(self):
        with self.assertRaises(ValueError):
            self.bridge.next_job('b'*32)
        self.bridge.last_seen = 0
        with self.assertRaisesRegex(RuntimeError, 'offline'):
            self.bridge.request('save', 'https://example.org')

    def test_http_token_origin_and_client_binding(self):
        http = start_http(self.bridge, 0)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = f'http://127.0.0.1:{http.server_port}/next'
        headers = {'X-Zotero-MCP-Token': self.bridge.token,
                   'X-Zotero-MCP-Client': self.client}
        try:
            for wrong, code in [({}, 401), ({**headers, 'Origin':'https://evil.example'}, 403)]:
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    opener.open(urllib.request.Request(url, headers=wrong))
                self.assertEqual(raised.exception.code, code)
            allowed = {**headers, 'Origin': 'chrome-extension://'+'a'*32}
            with opener.open(urllib.request.Request(url, headers=allowed)) as r:
                self.assertEqual(r.headers['Access-Control-Allow-Origin'], allowed['Origin'])
                self.assertIsNone(json.load(r)['job'])
        finally:
            http.shutdown(); http.server_close()

    def test_patcher_only_changes_separate_copy(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as d:
            source, target = Path(d)/'source', Path(d)/'output'
            source.mkdir()
            original = 'function init() {\n'+HOOK+'\n}\n'
            (source/'background.js').write_text(original)
            (source/'manifest.json').write_text(json.dumps({'manifest_version':3}))
            (source/'browser-polyfill.js').write_text('// fixture')
            with patch('prepare.provision') as provision:
                patch_build(source, target)
            provision.assert_called_once_with(target.resolve())
            self.assertEqual((source/'background.js').read_text(), original)
            self.assertIn('saveCurrentTabFromMCP', (target/'background.js').read_text(encoding='utf-8'))
            self.assertTrue((target/'mcp-bridge.html').exists())
            with self.assertRaises(ValueError): patch_build(source, target)
            with self.assertRaises(ValueError): patch_build(source, source/'nested')


if __name__ == '__main__':
    unittest.main()
