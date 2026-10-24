"""Prepare a SEPARATE development Connector build; never edit installed extensions."""
import argparse
import json
from pathlib import Path
import secrets
import shutil

ROOT = Path(__file__).resolve().parent
HOOK = 'browser.action.onClicked.addListener(waitForInit(logListenerErrors(_browserAction)));'


def provision(target):
    manifest_path = target/'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('name') != 'Zotero Connector (MCP Bridge)':
        raise ValueError('Only provision a bridge build')
    token_path = ROOT/'.bridge-token'
    if not token_path.exists():
        token_path.write_text(secrets.token_urlsafe(32)+'\n',encoding='utf-8')
    (target/'mcp-private-config.json').write_text(json.dumps({'port':23130,'token':token_path.read_text(encoding='utf-8').strip()}),encoding='utf-8')


def patch_build(source: Path, target: Path):
    source, target = source.resolve(), target.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError('Source and target must be separate directories')
    if target.exists(): raise ValueError('Output exists; choose a new directory')
    manifest = json.loads((source/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('manifest_version') not in (2, 3): raise ValueError('Unsupported manifest')
    background = (source/'background.js').read_text(encoding='utf-8-sig')
    if background.count(HOOK) != 1:
        raise ValueError('Connector source differs: exact official toolbar hook not found')
    # The caller supplies an official DEVELOPMENT build, not a minified package.
    polyfill = source/'browser-polyfill.js'
    if not polyfill.is_file():
        raise ValueError('Expected official browser polyfill is missing')
    preferences = source/'preferences/preferences.html'
    preferences_html = preferences.read_text(encoding='utf-8') if preferences.is_file() else None
    if preferences_html is not None and preferences_html.count('<body>') != 1:
        raise ValueError('Connector preferences page differs: body hook not found')
    shutil.copytree(source, target)
    background = background.replace(HOOK, 'this.saveCurrentTabFromMCP = waitForInit(_browserAction);\n\t' + HOOK)
    background += '\n' + (ROOT/'bridge-background.js').read_text(encoding='utf-8')
    (target/'background.js').write_text(background, encoding='utf-8')
    for src, dest in [('bridge-page.html','mcp-bridge.html'), ('bridge-page.js','mcp-bridge-page.js')]:
        shutil.copy2(ROOT/src, target/dest)
    # Upstream rebuilds context menus on navigation, so keep a stable entry in
    # extension options as well. This does not replace Zotero's own settings.
    if preferences_html is not None:
        preferences_html = preferences_html.replace('<body>', '<body>\n<p><a href="../mcp-bridge.html" target="_blank">Connect Zotero MCP bridge</a></p>', 1)
        (target/'preferences/preferences.html').write_text(preferences_html, encoding='utf-8')
    sandbox = target/'offscreen/offscreenSandbox.html'
    if sandbox.is_file():
        # Chromium ignores frame-ancestors in a meta CSP. Remove only that
        # ineffective declaration, preserving all effective sandbox directives.
        sandbox.write_text(sandbox.read_text(encoding='utf-8').replace(" frame-ancestors 'self';", ''), encoding='utf-8')
    manifest['name'] = 'Zotero Connector (MCP Bridge)'
    manifest['minimum_chrome_version'] = '116'
    manifest['permissions'] = list(dict.fromkeys(manifest.get('permissions',[]) + ['alarms','downloads']))
    (target/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    provision(target)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['init','patch','provision'])
    parser.add_argument('--source', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.action == 'init':
        with (ROOT/'.bridge-token').open('x', encoding='utf-8') as f:
            f.write(secrets.token_urlsafe(32)+'\n')
        print('Created .bridge-token; value not printed.')
    elif args.action == 'provision':
        if not args.output: parser.error('provision requires --output')
        provision(args.output)
        print('Provisioned private local pairing; token not printed.')
    else:
        if not args.source or not args.output: parser.error('patch requires --source and --output')
        print(patch_build(args.source, args.output))
