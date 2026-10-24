import io
import json
import os
from pathlib import Path
import socket
import sys
import unittest
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_api import (APP_NAME, AuthorizationRequired, LocalAPI, LocalAPIError,
                       PersistentAuthorizationRequired, WindowsCredentialStore, _NoRedirect)


TEST_KEY = "T" * 32


class MemoryStore:
    def __init__(self):
        self.values = {}
        self.reads = []
        self.writes = []
        self.deletes = []

    def get(self, server):
        self.reads.append(server)
        return self.values.get(server)

    def put(self, server, key):
        self.writes.append(server)
        self.values[server] = {"server_id": server, "key": key, "remember": True}

    def delete(self, server):
        self.deletes.append(server)
        self.values.pop(server, None)


class Response:
    def __init__(self, body=None, status=200, server="server-one"):
        self.status = status
        self.headers = {"Content-Type": "application/json", "Zotero-Server-ID": server}
        self.body = json.dumps({} if body is None else body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, req, timeout):
        self.requests.append(req)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def http_error(code):
    return urllib.error.HTTPError("http://127.0.0.1:23119/api/users/0/items", code,
                                  TEST_KEY, {}, io.BytesIO(TEST_KEY.encode()))


class LocalAPITests(unittest.TestCase):
    def test_remembered_authorization_is_reused_by_new_clients(self):
        store = MemoryStore()
        transport = Transport(Response(), Response({"key": TEST_KEY, "remember": True}),
                              Response({"successful": {}}))
        first = LocalAPI(store=store, opener=transport)
        self.assertFalse(first.status()["remembered"])
        self.assertTrue(first.ensure_authorized()["remembered"])
        first.json("/api/users/0/items", [])
        next_transport = Transport(Response(), Response({"successful": {}}))
        second = LocalAPI(store=store, opener=next_transport)
        second.ensure_authorized()
        second.json("/api/users/0/items", [])
        self.assertEqual(len(store.writes), 1)
        self.assertEqual(len(next_transport.requests), 2)
        self.assertEqual(json.loads(transport.requests[1].data), {"appName": APP_NAME})
        self.assertEqual(next_transport.requests[1].get_header("Zotero-api-key"), TEST_KEY)
        self.assertFalse(second.status()["validated"])
        self.assertNotIn(TEST_KEY, json.dumps(second.status()))

    def test_allow_once_is_never_persisted_and_blocks_multistep(self):
        store = MemoryStore()
        transport = Transport(Response(), Response({"key": TEST_KEY, "remember": False}))
        client = LocalAPI(store=store, opener=transport)
        for _ in range(2):
            with self.assertRaises(PersistentAuthorizationRequired):
                client.ensure_authorized()
        self.assertEqual(len(transport.requests), 2)
        self.assertFalse(store.writes)
        self.assertTrue(client.status()["in_memory"])
        self.assertFalse(client.status()["remembered"])

    def test_allow_once_is_discarded_after_one_write_attempt(self):
        store = MemoryStore()
        transport = Transport(Response(), Response({"key": TEST_KEY, "remember": False}),
                              http_error(403))
        client = LocalAPI(store=store, opener=transport)
        client.ensure_authorized(require_remembered=False)
        with self.assertRaises(LocalAPIError):
            client.json("/api/users/0/items", [])
        self.assertFalse(client.status()["in_memory"])
        self.assertEqual(len(transport.requests), 3)

    def test_401_forgets_only_this_server_without_replay_or_reauthorization(self):
        store = MemoryStore()
        store.put("server-one", TEST_KEY)
        store.put("different-server", "X" * 32)
        transport = Transport(Response(), http_error(401))
        client = LocalAPI(store=store, opener=transport)
        with self.assertRaises(AuthorizationRequired) as caught:
            client.json("/api/users/0/items", [])
        self.assertNotIn(TEST_KEY, str(caught.exception))
        self.assertFalse(client.status()["in_memory"])
        self.assertEqual(store.deletes, ["server-one"])
        self.assertIn("different-server", store.values)
        self.assertEqual(len(transport.requests), 2)
        with self.assertRaises(AuthorizationRequired):
            client.json("/api/users/0/items", [])
        with self.assertRaises(AuthorizationRequired):
            client.ensure_authorized()
        self.assertEqual(len(transport.requests), 2)
        transport.responses.append(Response({"key": "N" * 32, "remember": True}))
        self.assertTrue(client.authorize()["remembered"])
        self.assertEqual(len(transport.requests), 3)

    def test_failures_are_not_retried_and_do_not_leak_bodies(self):
        for failure in (http_error(403), http_error(412), http_error(428),
                        socket.timeout(TEST_KEY), urllib.error.URLError(TEST_KEY)):
            with self.subTest(failure=type(failure).__name__):
                store = MemoryStore()
                store.put("server-one", TEST_KEY)
                transport = Transport(Response(), failure)
                client = LocalAPI(store=store, opener=transport)
                with self.assertRaises(LocalAPIError) as caught:
                    client.json("/api/users/0/items", [])
                self.assertNotIn(TEST_KEY, str(caught.exception))
                self.assertEqual(len(transport.requests), 2)
                self.assertFalse(store.deletes)

    def test_server_identity_partitions_credentials(self):
        store = MemoryStore()
        store.put("server-one", TEST_KEY)
        other = LocalAPI(store=store, opener=Transport(Response(server="server-two")))
        self.assertEqual(store.reads, ["server-two"])
        self.assertFalse(other.status()["cached"])
        self.assertNotEqual(WindowsCredentialStore.target("server-one"),
                            WindowsCredentialStore.target("server-two"))

    def test_redirect_handler_never_follows_cross_origin_or_loopback_redirects(self):
        handler = _NoRedirect()
        req = urllib.request.Request("http://127.0.0.1:23119/api/")
        for code in (301, 302, 303, 307, 308):
            for url in ("https://example.org/", "http://127.0.0.1:23119/api/elsewhere"):
                with self.assertRaises(LocalAPIError):
                    handler.redirect_request(req, None, code, "redirect", {}, url)

    def test_unsafe_paths_and_headers_are_rejected_before_transmission(self):
        transport = Transport(Response())
        client = LocalAPI(store=MemoryStore(), opener=transport)
        for path in ("https://example.org/api/", "//example.org/api/", "/api/../secret",
                     "/api/%2e%2e/secret", "/api/%252e%252e/secret", "/api/./secret",
                     "/api/items?key=secret", "/api/items?%6bey=secret", "/api/\nitems",
                     "/api/local/authorize", "/api/local/%61uthorize",
                     "/api/local/%2561uthorize"):
            with self.subTest(path=path), self.assertRaises(LocalAPIError):
                client.request(path)
        for header in ("Authorization", "Zotero-API-Key", "Zotero-Server-ID", "Host"):
            with self.assertRaises(LocalAPIError):
                client.request("/api/", headers={header: "bad"})
        self.assertEqual(len(transport.requests), 1)

    def test_forget_is_local_only_and_does_not_call_server_revocation(self):
        store = MemoryStore()
        store.put("server-one", TEST_KEY)
        transport = Transport(Response())
        client = LocalAPI(store=store, opener=transport)
        self.assertEqual(client.forget(), {"forgotten": True, "server_revoked": False})
        self.assertEqual(len(transport.requests), 1)
        self.assertFalse(client.status()["cached"])

    def test_denial_does_not_retry_and_authorization_response_never_escapes(self):
        transport = Transport(Response(), http_error(403))
        client = LocalAPI(store=MemoryStore(), opener=transport)
        with self.assertRaises(AuthorizationRequired) as caught:
            client.authorize()
        self.assertNotIn(TEST_KEY, str(caught.exception))
        self.assertEqual(len(transport.requests), 2)
        with self.assertRaises(AuthorizationRequired):
            client.json("/api/users/0/items", [])
        with self.assertRaises(AuthorizationRequired):
            client.ensure_authorized()
        self.assertEqual(len(transport.requests), 2)

    def test_own_key_is_redacted_from_ordinary_responses(self):
        store = MemoryStore()
        store.put("server-one", TEST_KEY)
        transport = Transport(Response(), Response({"key": "ITEMABCD", "echo": [TEST_KEY]}))
        result = LocalAPI(store=store, opener=transport).json("/api/users/0/items")
        self.assertEqual(result["key"], "ITEMABCD")
        self.assertEqual(result["echo"], ["[redacted]"])

    @unittest.skipUnless(os.name == "nt", "Windows Credential Manager integration")
    def test_windows_vault_roundtrip_uses_only_a_new_random_test_target(self):
        # Does not enumerate/read real user credentials or contact Zotero.
        server = "test-only-" + uuid.uuid4().hex
        fake_key = uuid.uuid4().hex
        store = WindowsCredentialStore()
        try:
            self.assertIsNone(store.get(server))
            store.put(server, fake_key)
            saved = WindowsCredentialStore().get(server)
            self.assertEqual(saved, {"server_id": server, "key": fake_key, "remember": True})
            store.delete(server)
            self.assertIsNone(store.get(server))
        finally:
            store.delete(server)


if __name__ == "__main__":
    unittest.main()
