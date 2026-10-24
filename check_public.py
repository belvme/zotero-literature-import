"""Check only tracked source candidates for accidentally included private material."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
blocked = {'.bridge-token','mcp-private-config.json','.env'}
ignored = {'.git','.venv','__pycache__','connector-build','connector-ready','connector-with-bridge'}
patterns = [r'[A-Za-z]:[/\\]Users[/\\][^/\\\s]+',
            r'gh[pousr]_[A-Za-z0-9]{20,}', r'github_pat_[A-Za-z0-9_]{20,}',
            r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----',
            r'https?://[^/\s]+/[^\s]*[?&](?:captchaId|invoice|ctx)=']
issues = []
count = 0
for path in ROOT.rglob('*'):
    if not path.is_file() or any(part in ignored for part in path.relative_to(ROOT).parts):
        continue
    count += 1
    if path.name in blocked or path.suffix.lower() in {'.pdf','.zip','.sqlite','.log','.png','.jpg'}:
        issues.append(path.relative_to(ROOT).as_posix()+': forbidden runtime/data artifact')
        continue
    text = path.read_text(encoding='utf-8')
    if any(re.search(pattern,text) for pattern in patterns):
        issues.append(path.relative_to(ROOT).as_posix()+': private path, session URL or credential signature')
if issues:
    raise SystemExit('\n'.join(issues))
print(f'Public-tree checks passed: {count} files; no runtime credentials or personal paths detected.')
