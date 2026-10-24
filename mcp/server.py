"""MCP to a user-installed Zotero Connector background bridge.

No browser debugging, keyboard injection, cookies, or executable commands.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
from pathlib import Path
import queue
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


class Bridge:
    def __init__(self, token: str):
        self.token = token
        self.jobs: queue.Queue = queue.Queue()
        self.pending: dict = {}
        self.lock = threading.Lock()
        self.last_seen = 0.0
        self.client_id: str | None = None

    def next_job(self, client_id: str):
        now = time.monotonic()
        with self.lock:
            if self.client_id and self.client_id != client_id and now-self.last_seen < 6:
                raise ValueError('Another bridge page is connected; disconnect it first')
            self.client_id, self.last_seen = client_id, now
            while True:
                try:
                    job = self.jobs.get_nowait()
                except queue.Empty:
                    return None
                entry = self.pending.get(job['id'])
                if entry and not entry['expired'] and time.time() < job['expires_at']:
                    entry['delivered'] = True
                    return job

    def complete(self, client_id, job_id, result):
        with self.lock:
            entry = self.pending.get(job_id)
            if client_id != self.client_id or not entry or not entry['delivered']:
                raise ValueError('Unknown command or client')
            if entry['event'].is_set():
                raise ValueError('Command already completed')
            entry['result'] = result
            entry['event'].set()

    def request(self, action, expected_url=None, timeout=30, download_id=None):
        with self.lock:
            if time.monotonic()-self.last_seen > 6:
                raise RuntimeError('Connector background is offline. Check extension is enabled and reload it if upgraded.')
            job = {'id': secrets.token_hex(16), 'action': action,
                   'expected_url': expected_url, 'expires_at': time.time()+15}
            if download_id is not None:
                job['download_id'] = download_id
            entry = {'event': threading.Event(), 'delivered': False,
                     'expired': False, 'result': None}
            self.pending[job['id']] = entry
            self.jobs.put(job)
        if not entry['event'].wait(timeout):
            with self.lock:
                entry['expired'] = True
                delivered = entry['delivered']
                self.pending.pop(job['id'], None)
            raise RuntimeError('Outcome unknown; command was delivered. Check Zotero before retrying.'
                               if delivered else 'Bridge did not receive command; queued command expired.')
        with self.lock:
            self.pending.pop(job['id'], None)
        result = entry['result']
        if not isinstance(result, dict) or not result.get('ok'):
            raise RuntimeError((result or {}).get('error', 'Connector returned an invalid result'))
        return result


def start_http(bridge: Bridge, port: int):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log tokens, page URLs, or request bodies.

        def send_json(self, status, value):
            b = json.dumps(value).encode()
            self.send_response(status)
            origin = self.headers.get('Origin', '')
            if re.fullmatch(r'chrome-extension://[a-p]{32}', origin):
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def valid_origin(self):
            origin = self.headers.get('Origin')
            return not origin or bool(re.fullmatch(r'chrome-extension://[a-p]{32}', origin))

        def authorized(self):
            if not self.valid_origin():
                self.send_json(403, {'error': 'Only extension origins are accepted'})
                return False
            if not hmac.compare_digest(self.headers.get('X-Zotero-MCP-Token', ''), bridge.token):
                self.send_json(401, {'error': 'Invalid bridge token'})
                return False
            return True

        def do_OPTIONS(self):
            if not self.valid_origin():
                self.send_json(403, {'error': 'Origin rejected'})
                return
            self.send_response(204)
            origin = self.headers.get('Origin')
            if origin:
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Zotero-MCP-Token, X-Zotero-MCP-Client')
            self.end_headers()

        def do_GET(self):
            if not self.authorized(): return
            client = self.headers.get('X-Zotero-MCP-Client', '')
            if self.path != '/next' or not re.fullmatch('[a-f0-9]{32}', client):
                self.send_json(400, {'error': 'Invalid request'})
                return
            try:
                self.send_json(200, {'job': bridge.next_job(client)})
            except ValueError as e:
                self.send_json(409, {'error': str(e)})

        def do_POST(self):
            if not self.authorized(): return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if self.path != '/result' or not 0 < size <= 16384:
                    raise ValueError('Invalid request size or path')
                data = json.loads(self.rfile.read(size))
                bridge.complete(self.headers.get('X-Zotero-MCP-Client', ''), data['id'], data['result'])
                self.send_json(200, {'ok': True})
            except (ValueError, KeyError, TypeError) as e:
                self.send_json(400, {'error': str(e)})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class Router:
    """Select one connected browser using read-only probes; never broadcast writes."""
    def __init__(self, token):
        self.token = token
        self.peers = []
        self.lock = threading.Lock()
        self.preferred = None
        self.downloads = {}

    def request(self, action, expected_url=None, timeout=30, download_id=None):
        with self.lock:
            peers = [p for p in self.peers if time.monotonic()-p.last_seen < 6]
            preferred = self.preferred
        if action == 'download_status':
            peer = self.downloads.get(download_id)
            if peer not in peers:
                raise RuntimeError('Download browser is offline or download ID is unknown')
            return peer.request(action, timeout=timeout, download_id=download_id)
        if not peers:
            raise RuntimeError('Connector background is offline; enable the v2 extension')
        if not expected_url and preferred in peers:
            peers = [preferred]
        matches = []
        errors = []
        for peer in peers:
            try:
                result = peer.request('status', expected_url, timeout=8)
                matches.append((peer,result))
            except RuntimeError as error:
                errors.append(str(error))
                continue
        if len(matches) != 1:
            if len(peers) == 1 and errors:
                raise RuntimeError(errors[0])
            raise RuntimeError('Cannot select one browser ('+str(len(matches))+' matches). Supply the active article URL; nothing saved.')
        peer, status = matches[0]
        self.preferred = peer
        if action == 'status':
            return status
        result = peer.request(action, status['url'], timeout=timeout)
        if action == 'download_pdf':
            self.downloads[result['download_id']] = peer
        return result


def start_ws(router, port: int):
    from websockets.sync.server import serve
    from websockets.exceptions import ConnectionClosed

    def handler(ws):
        client = secrets.token_hex(16)
        bridge = Bridge(router.token) if isinstance(router, Router) else router
        try:
            hello = json.loads(ws.recv(timeout=5))
            if not isinstance(hello, dict) or not isinstance(hello.get('token'), str) or not hmac.compare_digest(hello['token'], bridge.token):
                ws.close(1008, 'Authentication failed')
                return
            if isinstance(router, Router):
                with router.lock:
                    router.peers.append(bridge)
            ws.send(json.dumps({'type':'ready','version':2}))
            while True:
                job = bridge.next_job(client)
                if job:
                    ws.send(json.dumps({'type':'job','job':job}))
                try:
                    message = json.loads(ws.recv(timeout=.25))
                except TimeoutError:
                    continue
                if not isinstance(message, dict):
                    raise ValueError('Invalid frame')
                if message.get('type') == 'result':
                    bridge.complete(client, message['id'], message['result'])
                elif message.get('type') == 'heartbeat':
                    ws.send(json.dumps({'type':'heartbeat'}))
        except (ConnectionClosed, TimeoutError, ValueError, KeyError, TypeError):
            ws.close()
        finally:
            with bridge.lock:
                if bridge.client_id == client:
                    bridge.last_seen = 0
            if isinstance(router, Router):
                with router.lock:
                    if bridge in router.peers:
                        router.peers.remove(bridge)

    server = serve(handler, '127.0.0.1', port,
                   origins=[re.compile(r'chrome-extension://[a-p]{32}')],
                   max_size=16384, compression=None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations
    token_path = Path(os.environ.get('ZOTERO_MCP_TOKEN_FILE', Path(__file__).with_name('.bridge-token')))
    token = token_path.read_text(encoding='utf-8').strip()
    if len(token) < 32:
        raise ValueError('Generate a bridge token using prepare.py init')
    port = int(os.environ.get('ZOTERO_MCP_WS_PORT', '23130'))
    bridge = Router(token)
    ws_server = start_ws(bridge, port)
    mcp = FastMCP('Zotero Connector Bridge')
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                        idempotentHint=False, openWorldHint=True))
    async def save_and_check_pdf(title: str, expected_url: str, doi: str = '', wait_seconds: int = 30) -> dict:
        """Save the verified active article once, then check real Zotero PDF files.

        Verify institution login and article identity in the browser first. Skips save
        if a matching active item already has a verified PDF. Returns keys, page count,
        hash and first-page excerpt for identity review and merging to the original.
        Pending/missing PDF is not permission to repeat save. No local write grant needed.
        """
        from capture import save_and_check
        return await asyncio.to_thread(save_and_check, bridge, title, expected_url, doi, wait_seconds)

    local_client = None
    local_client_lock = threading.Lock()

    def local_api_client():
        nonlocal local_client
        from local_api import LocalAPI
        with local_client_lock:
            if local_client is None:
                local_client = LocalAPI()
            return local_client

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
    async def local_write_auth_status() -> dict:
        """Read this app's cached local write authorization state. No prompt or write.

        Cached credentials are not proof of current write validity. Zotero validates
        them on the next necessary write. Never return the credential itself.
        """
        return await asyncio.to_thread(lambda: local_api_client().status())

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                        idempotentHint=False, openWorldHint=False))
    async def authorize_local_writes() -> dict:
        """Reuse saved authorization, or show Zotero's permission dialog once.

        Use when the user requests local writes or persistent permission. Selecting
        Always Allow stores this app's key in Windows Credential Manager so attachment
        and metadata scripts share it. No literature is changed.
        """
        def authorize():
            client = local_api_client()
            client.authorize()  # An explicit tool call may resume after a prior denial/401.
            return client.ensure_authorized(require_remembered=True)
        return await asyncio.to_thread(authorize)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
    async def connector_status(expected_url: str = '') -> dict:
        """Read active page/translator status. Optional exact HTTP(S) URL disambiguates host windows. No save."""
        if expected_url:
            parsed = urlparse(expected_url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('expected_url must be an HTTP(S) page without embedded credentials')
        return await asyncio.to_thread(bridge.request, 'status', expected_url or None)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                        idempotentHint=False, openWorldHint=True))
    async def save_current_page(expected_url: str = '') -> dict:
        """Trigger the actual Connector toolbar action for the active HTTP(S) page.

        Verify the school's login in the normal browser before calling. expected_url
        must equal the active page. May create a duplicate item. Success means the
        Connector action was requested, NOT that PDF download or import completed.
        Never retry an unknown outcome before inspecting Zotero.
        """
        if expected_url:
            parsed = urlparse(expected_url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('expected_url must be an HTTP(S) page without embedded credentials')
        return await asyncio.to_thread(bridge.request, 'save', expected_url or None)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True))
    async def download_current_pdf(expected_url: str) -> dict:
        """After inspecting Zotero for missing PDF, download the page's explicit PDF link with browser authentication. Does not import or claim completion."""
        parsed = urlparse(expected_url)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Expected a credential-free HTTP(S) URL')
        return await asyncio.to_thread(bridge.request, 'download_pdf', expected_url)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
    async def download_status(download_id: int) -> dict:
        """Check only a download initiated by this bridge; actual PDF and Zotero attachment still require verification."""
        if download_id < 0:
            raise ValueError('Invalid download ID')
        return await asyncio.to_thread(bridge.request, 'download_status', download_id=download_id)

    try:
        mcp.run(transport='stdio')
    finally:
        ws_server.shutdown()


if __name__ == '__main__':
    main()
