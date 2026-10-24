"""Zotero local writes with a shared, Windows-protected remembered authorization.

Only this client's own named Credential Manager entry is read. A cached key is
not a permission check: Zotero validates it on the next actual write. Requests
are never retried automatically, including requests whose outcome is unknown.
"""

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request


BASE = "http://127.0.0.1:23119"
APP_NAME = "Codex Zotero 文献导入与整理"
_TARGET_PREFIX = "Codex/ZoteroLocalAPI/"


class LocalAPIError(RuntimeError):
    """An error whose message contains no server response or credentials."""


class AuthorizationRequired(LocalAPIError):
    pass


class PersistentAuthorizationRequired(AuthorizationRequired):
    pass


class CredentialStoreError(LocalAPIError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise LocalAPIError("Zotero redirected the request; it was not followed.")


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class WindowsCredentialStore:
    """Exact-target access to our current Windows user's credential vault."""

    def __init__(self):
        if os.name != "nt":
            raise CredentialStoreError("Remembered Zotero access requires Windows Credential Manager.")
        self._dll = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._dll.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                       wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_Credential))]
        self._dll.CredReadW.restype = wintypes.BOOL
        self._dll.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
        self._dll.CredWriteW.restype = wintypes.BOOL
        self._dll.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._dll.CredDeleteW.restype = wintypes.BOOL
        self._dll.CredFree.argtypes = [ctypes.c_void_p]
        self._dll.CredFree.restype = None

    @staticmethod
    def target(server_id):
        if not isinstance(server_id, str) or not server_id or len(server_id) > 512:
            raise CredentialStoreError("Invalid Zotero server identity.")
        return _TARGET_PREFIX + hashlib.sha256(server_id.encode("utf-8")).hexdigest()

    def get(self, server_id):
        result = ctypes.POINTER(_Credential)()
        if not self._dll.CredReadW(self.target(server_id), 1, 0, ctypes.byref(result)):
            if ctypes.get_last_error() == 1168:  # ERROR_NOT_FOUND
                return None
            raise CredentialStoreError("Could not read this client's Windows credential.")
        try:
            credential = result.contents
            raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
            value = json.loads(raw.decode("utf-8"))
            if (not isinstance(value, dict) or value.get("server_id") != server_id
                    or value.get("remember") is not True or not _valid_key(value.get("key"))):
                raise ValueError
            return value
        except (ValueError, UnicodeError, TypeError):
            raise CredentialStoreError("This client's saved Zotero credential is invalid.") from None
        finally:
            self._dll.CredFree(result)

    def put(self, server_id, key):
        if not _valid_key(key):
            raise CredentialStoreError("Zotero returned an invalid authorization.")
        raw = json.dumps({"server_id": server_id, "key": key, "remember": True},
                         ensure_ascii=False).encode("utf-8")
        blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        credential = _Credential()
        credential.Type = 1  # CRED_TYPE_GENERIC
        credential.TargetName = self.target(server_id)
        credential.Comment = APP_NAME
        credential.CredentialBlobSize = len(raw)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE: current user, across logons
        credential.UserName = "Codex Zotero Local API"
        try:
            if not self._dll.CredWriteW(ctypes.byref(credential), 0):
                raise CredentialStoreError("Could not save this client's Windows credential.")
        finally:
            ctypes.memset(ctypes.addressof(blob), 0, len(blob))

    def delete(self, server_id):
        if not self._dll.CredDeleteW(self.target(server_id), 1, 0):
            if ctypes.get_last_error() != 1168:
                raise CredentialStoreError("Could not remove this client's Windows credential.")


def _valid_key(value):
    return isinstance(value, str) and len(value) == 32 and value.isascii() and value.isalnum()


def _safe_path(path):
    if not isinstance(path, str) or not path.startswith("/api/"):
        raise LocalAPIError("Only relative Zotero /api/ paths are allowed.")
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment or "\\" in path:
        raise LocalAPIError("Invalid local API path.")
    decoded = path
    for _ in range(4):
        previous = decoded
        decoded = urllib.parse.unquote(decoded)
        pathname = decoded.split("?", 1)[0]
        if "\\" in decoded or any(part in (".", "..") for part in pathname.split("/")):
            raise LocalAPIError("Path traversal is not allowed.")
        if any(ord(c) < 32 or ord(c) == 127 for c in decoded):
            raise LocalAPIError("Control characters are not allowed in API paths.")
        if previous == decoded:
            break
    if urllib.parse.unquote(decoded) != decoded:
        raise LocalAPIError("Excessively encoded API paths are not allowed.")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if any(name.lower() == "key" for name in query):
        raise LocalAPIError("Authorization must be supplied by the shared local API client.")
    return path


def _is_authorization_path(path):
    decoded = urllib.parse.urlsplit(path).path
    for _ in range(4):
        decoded = urllib.parse.unquote(decoded)
    return decoded.rstrip("/") == "/api/local/authorize"


class LocalAPI:
    def __init__(self, *, store=None, opener=None, timeout=10):
        self._lock = threading.RLock()
        self._store = store if store is not None else WindowsCredentialStore()
        # Loopback requests must not inherit an HTTP proxy or follow a redirect.
        self._opener = opener if opener is not None else urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect())
        self._key = None
        self._remembered = False
        self._cached = False
        self._auto_auth_blocked = False
        _, _, response_headers = self._send("/api/", None, {}, timeout)
        self.server_id = response_headers.get("Zotero-Server-ID")
        if (not isinstance(self.server_id, str) or not self.server_id
                or len(self.server_id) > 512 or any(ord(c) < 32 for c in self.server_id)):
            raise LocalAPIError("Zotero did not provide a valid server identity.")
        stored = self._store.get(self.server_id)
        if stored:
            if (stored.get("server_id") != self.server_id or stored.get("remember") is not True
                    or not _valid_key(stored.get("key"))):
                raise CredentialStoreError("This client's saved Zotero credential is invalid.")
            self._key = stored["key"]
            self._remembered = True
            self._cached = True

    def status(self):
        """Describe cached state only; no key validation or write is performed."""
        with self._lock:
            return {"cached": self._cached, "in_memory": self._key is not None,
                    "remembered": self._remembered, "validated": False}

    def _send(self, path, data, headers, timeout):
        req = urllib.request.Request(BASE + _safe_path(path), data=data, headers=headers)
        try:
            with self._opener.open(req, timeout=timeout) as response:
                code = response.status
                if 300 <= code < 400:
                    raise LocalAPIError("Zotero redirected the request; it was not followed.")
                body = response.read()
                response_headers = response.headers
                if body.strip() and "json" in response_headers.get("Content-Type", ""):
                    try:
                        result = json.loads(body)
                    except (ValueError, UnicodeError):
                        raise LocalAPIError("Zotero returned invalid JSON.") from None
                else:
                    result = body.decode("utf-8", errors="replace")
                return code, result, response_headers
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            # Never include the URL, headers, or response body in an exception.
            raise _HTTPFailure(code) from None
        except (TimeoutError, socket.timeout):
            raise LocalAPIError("Zotero request timed out; no retry was made. Check the operation's outcome before retrying.") from None
        except urllib.error.URLError:
            raise LocalAPIError("Could not complete the Zotero request; no retry was made. Check the operation's outcome before retrying.") from None

    def authorize(self, timeout=600):
        """Reuse this client's key, or obtain one from Zotero's official dialog."""
        with self._lock:
            if self._key is not None:
                return self.status()
            # A denial, timeout, or rejected write must not turn a caller's loop
            # into a series of authorization dialogs. Only this explicit method
            # can request another grant once an automatic flow has stopped.
            self._auto_auth_blocked = True
            payload = json.dumps({"appName": APP_NAME}, ensure_ascii=False).encode("utf-8")
            try:
                _, result, _ = self._send("/api/local/authorize", payload,
                                          {"Zotero-Server-ID": self.server_id,
                                           "Content-Type": "application/json"}, timeout)
            except _HTTPFailure as exc:
                if exc.code == 403:
                    raise AuthorizationRequired("Zotero authorization was denied. No retry was made.") from None
                raise LocalAPIError(f"Zotero authorization returned HTTP {exc.code}; no retry was made.") from None
            if (not isinstance(result, dict) or not _valid_key(result.get("key"))
                    or not isinstance(result.get("remember"), bool)):
                raise LocalAPIError("Zotero returned an invalid authorization response.")
            key = result["key"]
            remembered = result["remember"]
            if remembered:
                self._store.put(self.server_id, key)
            self._key = key
            self._remembered = remembered
            self._cached = remembered
            self._auto_auth_blocked = False
            return self.status()

    def ensure_authorized(self, require_remembered=True, timeout=600):
        with self._lock:
            self._check_auto_authorization()
            self.authorize(timeout=timeout)
            if require_remembered and not self._remembered:
                raise PersistentAuthorizationRequired(
                    "Zotero granted one write only. This multi-step operation needs 始终允许 (Always Allow); "
                    "no write or additional authorization request was made.")
            return self.status()

    def _check_auto_authorization(self):
        if self._key is None and self._auto_auth_blocked:
            raise AuthorizationRequired(
                "Automatic authorization stopped after the previous failure. "
                "Call authorize() explicitly to request a new grant; no request was made.")

    def _clear(self):
        self._key = None
        self._remembered = False
        self._cached = False

    def forget(self):
        """Remove only our local saved credential; this does not revoke Zotero grants."""
        with self._lock:
            self._store.delete(self.server_id)
            self._clear()
            return {"forgotten": True, "server_revoked": False}

    def request(self, path, data=None, content_type=None, headers=None, write=False, timeout=30):
        with self._lock:
            _safe_path(path)
            if _is_authorization_path(path):
                raise LocalAPIError("Use authorize() for local API authorization.")
            supplied = dict(headers or {})
            protected = {"zotero-api-key", "authorization", "proxy-authorization",
                         "zotero-server-id", "host"}
            if any(name.lower() in protected for name in supplied):
                raise LocalAPIError("Callers cannot override authentication or server identity headers.")
            if data is not None and not isinstance(data, bytes):
                raise LocalAPIError("Request data must be bytes; use json() for JSON data.")
            if write and data is None:
                raise LocalAPIError("A write request requires an explicit request body.")
            if content_type:
                supplied["Content-Type"] = content_type
            supplied["Zotero-Server-ID"] = self.server_id
            if write:
                self._check_auto_authorization()
                self.authorize()
                supplied["Zotero-API-Key"] = self._key
            single_use = write and not self._remembered
            try:
                code, result, _ = self._send(path, data, supplied, timeout)
                return code, self._redact(result)
            except _HTTPFailure as exc:
                if exc.code == 401 and write:
                    self._auto_auth_blocked = True
                    try:
                        self._store.delete(self.server_id)
                    finally:
                        self._clear()
                    raise AuthorizationRequired(
                        "Zotero rejected the saved authorization (HTTP 401). The local cache was cleared; "
                        "authorize again before retrying. No write was replayed.") from None
                if exc.code == 403 and write:
                    self._auto_auth_blocked = True
                raise LocalAPIError(f"Zotero returned HTTP {exc.code}; no retry was made.") from None
            finally:
                if single_use:
                    self._clear()

    def _redact(self, value):
        if isinstance(value, str) and self._key:
            return value.replace(self._key, "[redacted]")
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            return {self._redact(key): self._redact(item) for key, item in value.items()}
        return value

    def json(self, path, data=None, timeout=30):
        payload = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
        return self.request(path, payload, "application/json" if payload is not None else None,
                            write=payload is not None, timeout=timeout)[1]


class _HTTPFailure(LocalAPIError):
    def __init__(self, code):
        self.code = code
        super().__init__(f"Zotero returned HTTP {code}; no retry was made.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "authorize", "forget"))
    args = parser.parse_args()
    try:
        client = LocalAPI()
        if args.command == "authorize":
            result = client.ensure_authorized()
        elif args.command == "forget":
            result = client.forget()
        else:
            result = client.status()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except LocalAPIError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
