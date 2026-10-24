"""One Connector action followed by bounded, read-only local PDF verification."""
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import urllib.parse
import urllib.request
from pypdf import PdfReader
from local_api import _NoRedirect

BASE = 'http://127.0.0.1:23119/api/users/0/items'
_capture_lock = threading.Lock()


def normalized(title):
    return ' '.join(title.split()).rstrip('.').casefold()


def get(suffix):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(BASE + suffix, timeout=10) as response:
        raw = response.read()
        return json.loads(raw) if 'json' in response.headers.get('Content-Type', '') else raw.decode()


def inspect(title, doi=''):
    rows = get('?' + urllib.parse.urlencode({'q':title, 'limit':100}))
    matches = []
    for row in rows:
        data = row['data']
        if data.get('deleted') or data.get('itemType') != 'journalArticle':
            continue
        if normalized(data.get('title', '')) != normalized(title):
            continue
        doi_conflict = bool(doi and data.get('DOI') and data['DOI'].strip().casefold() != doi.strip().casefold())
        key = row['key']
        if not re.fullmatch('[A-Z0-9]{8}', key):
            continue
        pdfs = []
        for child in get('/' + key + '/children'):
            info = child['data']
            attachment = child['key']
            if info.get('contentType') != 'application/pdf' or not re.fullmatch('[A-Z0-9]{8}', attachment):
                continue
            try:
                url = urllib.parse.urlsplit(get('/' + attachment + '/file/view/url'))
                if url.scheme != 'file' or url.netloc not in ('', 'localhost'):
                    continue
                path = Path(urllib.request.url2pathname(url.path))
                raw = path.read_bytes()
                if not raw.startswith(b'%PDF-'):
                    continue
                digest = hashlib.md5(raw).hexdigest()
                if not info.get('md5') or digest != info['md5']:
                    continue
                pdf = PdfReader(path)
                if not len(pdf.pages):
                    continue
                pdfs.append({'attachment':attachment, 'bytes':len(raw), 'pages':len(pdf.pages),
                             'md5':digest, 'first_page_excerpt':(pdf.pages[0].extract_text() or '')[:700]})
            except Exception:
                # An attachment can exist before its background download finishes.
                continue
        matches.append({'parent':key, 'pdfs':pdfs, 'doi':data.get('DOI', ''),
                        'doi_conflict':doi_conflict, 'needs_identity_review':doi_conflict})
    return matches


def save_and_check(router, title, expected_url, doi='', wait_seconds=30):
    if not isinstance(title, str) or not 5 <= len(title.strip()) <= 500:
        raise ValueError('Supply the verified article title (5–500 characters)')
    if not 0 <= wait_seconds <= 60:
        raise ValueError('wait_seconds must be between 0 and 60')
    parsed = urllib.parse.urlsplit(expected_url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Supply the exact active HTTP(S) article URL')
    with _capture_lock:
        before = inspect(title, doi)
        if any(item['pdfs'] for item in before):
            review = any(r.get('doi_conflict') and r['pdfs'] for r in before)
            return {'status':'pdf_identity_review_required' if review else 'already_has_verified_pdf',
                    'saved':False, 'items':before}
        # Do not retry this action on timeout or a lost acknowledgement.
        router.request('save', expected_url)
        deadline = time.monotonic() + wait_seconds
        while True:
            current = inspect(title, doi)
            if any(item['pdfs'] for item in current):
                review = any(r.get('doi_conflict') and r['pdfs'] for r in current)
                return {'status':'pdf_identity_review_required' if review else 'pdf_verified', 'saved':True, 'items':current,
                        'new_parent_keys':[r['parent'] for r in current
                                           if r['parent'] not in {b['parent'] for b in before}],
                        'needs_identity_review':True}
            if time.monotonic() >= deadline:
                return {'status':'saved_pdf_pending_or_missing', 'saved':True, 'items':current,
                        'warning':'Inspect actual attachments before downloading; do not save again.'}
            time.sleep(min(1, max(0, deadline-time.monotonic())))
