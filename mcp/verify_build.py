"""Check packaged assets before loading the extension; no browser access."""
from pathlib import Path
import json
import re

p = Path(__file__).resolve().parent / 'connector-ready'
m = json.loads((p/'manifest.json').read_text(encoding='utf-8'))
refs = [m['background']['service_worker'], m['options_ui']['page'],
        *m['icons'].values(), *m['action']['default_icon'].values(), *m['sandbox']['pages']]
refs += [f for c in m['content_scripts'] for f in c.get('js', [])]
worker = (p/'background-worker.js').read_text(encoding='utf-8')
refs += re.findall(r'"([^"\n]+\.js)"', worker)
for html in ('mcp-bridge.html', m['options_ui']['page']):
    text = (p/html).read_text(encoding='utf-8')
    refs += [str((Path(html).parent/f).as_posix())
             for f in re.findall(r'<script[^>]+src="([^"]+)"', text)]
missing = sorted(set(f for f in refs if not (p/f).is_file()))
print('Manifest:', m['name'], 'MV', m['manifest_version'])
print('Referenced local assets checked:', len(set(refs)))
print('Missing:', missing)
raise SystemExit(bool(missing))
