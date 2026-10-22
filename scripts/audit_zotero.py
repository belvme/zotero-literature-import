"""Read-only, task-scoped Zotero audit. Standard library; pypdf is optional."""
import argparse
import difflib
import json
import pathlib
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request


def normalize(text):
    text = unicodedata.normalize('NFKC', str(text)).casefold()
    return ''.join(c for c in text if c.isalnum())


def normalize_doi(value):
    value = str(value or '').strip().lower()
    return re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', value)


def classify(reference, data):
    if data.get('itemType') not in {'journalArticle', 'magazineArticle', 'conferencePaper',
                                    'thesis', 'preprint', 'report', 'bookSection'} or data.get('deleted'):
        return None
    wanted_doi = normalize_doi(reference.get('doi') or reference.get('DOI'))
    current_doi = normalize_doi(data.get('DOI'))
    wanted_title = normalize(reference['title'])
    current_title = normalize(data.get('title', ''))
    wanted_year = str(reference.get('year') or '')
    current_year = re.search(r'\d{4}', data.get('date', ''))
    current_year = current_year.group() if current_year else ''
    year_conflict = bool(wanted_year and current_year and wanted_year != current_year)
    if wanted_doi and wanted_doi == current_doi:
        return 'same_doi_review_year' if year_conflict else 'same_doi'
    if wanted_title == current_title:
        return 'different_year_review' if year_conflict else 'same_title_review_authors'
    similarity = difflib.SequenceMatcher(None, wanted_title, current_title).ratio()
    if similarity >= .96 and not year_conflict:
        return 'similar_title_review'
    return None


def find_candidates(api, reference):
    """Search within this reference; an empty phrase search is not proof of absence."""
    hits = {}
    queries = [(reference['title'][:64], 'titleCreatorYear')]
    doi = normalize_doi(reference.get('doi') or reference.get('DOI'))
    if doi:
        queries.append((doi, 'everything'))
    for term, mode in queries:
        path = 'users/0/items/top?' + urllib.parse.urlencode({'q': term, 'qmode': mode})
        for item in api.get(path):
            hits[item['key']] = item
    if not any(classify(reference, item['data']) for item in hits.values()):
        words = sorted(set(re.findall(r'[A-Za-z]{6,}', reference['title'])),
                       key=lambda word: (-len(word), word.casefold()))[:2]
        for word in words:
            path = 'users/0/items/top?' + urllib.parse.urlencode(
                {'q': word.upper(), 'qmode': 'titleCreatorYear'})
            for item in api.get(path):
                hits[item['key']] = item
    return [item for item in hits.values() if classify(reference, item['data'])]


class LocalAPI:
    def __init__(self, base_url):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise ValueError('Only a local loopback Zotero HTTP endpoint is accepted')
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Do not put credentials or query parameters in the base URL')
        if parsed.path.rstrip('/') != '/api':
            raise ValueError('The endpoint path must be /api/')
        self.base = base_url.rstrip('/') + '/'

    def get(self, path, as_json=True):
        req = urllib.request.Request(self.base + path, headers={'User-Agent': 'ZoteroLiteratureAudit/1.0'})
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read()
        return json.loads(raw) if as_json else raw.decode('utf-8')


def file_status(api, child):
    data = child['data']
    result = {'key': child['key'], 'title': data.get('title', ''), 'stored': False,
              'content_review': 'Check title, authors, and version before claiming the correct full text'}
    try:
        url = api.get('users/0/items/' + child['key'] + '/file/view/url', as_json=False).strip()
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'file' or parsed.netloc not in {'', 'localhost'}:
            raise ValueError('The attachment did not resolve to a local file URL')
        local = pathlib.Path(urllib.request.url2pathname(parsed.path))
        result['path'] = str(local)
        if not local.is_file():
            raise ValueError('Attachment metadata exists, but its file is missing')
        result['bytes'] = local.stat().st_size
        with local.open('rb') as stream:
            prefix = stream.read(1024)
        if result['bytes'] == 0 or b'%PDF-' not in prefix:
            raise ValueError('The saved file is empty or does not contain a PDF header')
        try:
            from pypdf import PdfReader
        except ImportError:
            result['stored'] = True
            result['readability'] = 'PDF header only; inspect pages using available PDF tooling'
        else:
            reader = PdfReader(str(local))
            result['pages'] = len(reader.pages)
            if not result['pages']:
                raise ValueError('The PDF has no readable pages')
            result['stored'] = True
            result['readability'] = 'Parsed pages; content identity still requires review'
    except Exception as error:
        # A broken attachment must appear in the report, not abort other papers.
        result['error'] = str(error)
    return result


def audit(api, references):
    rows = []
    for index, reference in enumerate(references, 1):
        matched = []
        for item in find_candidates(api, reference):
            reason = classify(reference, item['data'])
            if reason is None:
                continue
            children = api.get('users/0/items/' + item['key'] + '/children')
            pdfs = [file_status(api, c) for c in children
                    if c['data'].get('contentType') == 'application/pdf' and not c['data'].get('deleted')]
            matched.append({'key': item['key'], 'reason': reason, 'title': item['data'].get('title', ''),
                            'identity_review_required': reason != 'same_doi',
                            'date': item['data'].get('date', ''), 'collections': item['data'].get('collections', []),
                            'PDFs': pdfs, 'note_count': sum(c['data'].get('itemType') == 'note' and not c['data'].get('deleted') for c in children)})
        rows.append({'id': reference.get('id', reference.get('n', index)), 'title': reference['title'],
                     'matches': matched, 'duplicate_review': len(matched) > 1,
                     'identity_review_required': any(m['identity_review_required'] for m in matched),
                     'candidate_has_stored_pdf': any(p['stored'] for m in matched for p in m['PDFs']),
                     'has_stored_pdf': any(p['stored'] for m in matched
                                           if not m['identity_review_required'] for p in m['PDFs'])})
    return {'references': rows, 'requested_count': len(rows),
            'matched_reference_count': sum(bool(r['matches']) for r in rows),
            'identity_review_reference_count': sum(r['identity_review_required'] for r in rows),
            'stored_pdf_reference_count': sum(r['has_stored_pdf'] for r in rows),
            'duplicate_review_count': sum(r['duplicate_review'] for r in rows),
            'missing_pdf_ids': [r['id'] for r in rows if not r['has_stored_pdf']],
            'writes_performed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=pathlib.Path)
    parser.add_argument('--output', type=pathlib.Path)
    parser.add_argument('--base-url', default='http://127.0.0.1:23119/api/')
    args = parser.parse_args()
    references = json.loads(args.manifest.read_text(encoding='utf-8-sig'))
    if isinstance(references, dict):
        references = references.get('references')
    if not isinstance(references, list) or not references:
        parser.error('Manifest must contain a non-empty reference array')
    if any(not isinstance(r, dict) or not isinstance(r.get('title'), str)
           or not r['title'].strip() for r in references):
        parser.error('Every reference needs a non-empty string title')
    try:
        report = audit(LocalAPI(args.base_url), references)
    except (ValueError, OSError, urllib.error.URLError) as error:
        parser.exit(1, 'Audit failed: ' + str(error) + '\n')
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + '\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
