"""Stage Chromium resources on Windows, then run upstream's Gulp transforms.

Equivalent resource inputs to upstream build.sh, production mode (-p). Does not
modify upstream sources or installed extensions. Requires initialized submodules
and npm dependencies in connector-build.
"""
from pathlib import Path
import json
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
UPSTREAM = ROOT / 'connector-build'
SRC = UPSTREAM / 'src'
OUT = UPSTREAM / 'build' / 'manifestv3'


def copy_tree(source, target):
    shutil.copytree(source, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('.*', '*.jsx', 'node_modules'))


def main():
    if OUT.exists():
        raise SystemExit('Build output exists; refusing to overwrite')
    OUT.mkdir(parents=True)
    shutil.copy2(UPSTREAM / 'COPYING', OUT)
    copy_tree(SRC / 'common', OUT)
    copy_tree(SRC / 'browserExt', OUT)
    copy_tree(SRC / 'translate' / 'src', OUT / 'translate')
    copy_tree(SRC / 'utilities', OUT / 'utilities')
    google = SRC / 'zotero-google-docs-integration'
    copy_tree(google / 'src' / 'connector', OUT / 'zotero-google-docs-integration')
    shutil.copy2(google / 'package.json', OUT / 'zotero-google-docs-integration')

    locales = OUT / '_locales'
    (locales / 'en').mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC / 'messages.json', locales / 'en' / 'messages.json')
    for loc in sorted((SRC / 'zotero/chrome/locale').iterdir()):
        code = loc.name
        messages = loc / 'zotero' / 'connector.json'
        if code == 'en-US' or not re.fullmatch(r'..(?:-..)?', code) or not messages.is_file():
            continue
        dest = locales / (code if code in ('pt-PT', 'zh-TW') else code[:2])
        dest.mkdir(exist_ok=True)
        shutil.copy2(messages, dest / 'messages.json')

    skin = SRC / 'zotero/chrome/skin/default/zotero'
    images = OUT / 'images'
    images.mkdir(exist_ok=True)
    resources = list(skin.glob('treeitem*png')) + list((SRC / 'common/images').glob('*'))
    names = ['treesource-collection.png', 'zotero-new-z-16px.png', 'progress_arcs.png',
             'cross.png', 'tick.png', 'tick@2x.png', 'spinner-16px.png',
             'spinner-16px@2x.png', 'treesource-library.png', 'prefs-general.png',
             'prefs-advanced.png', 'prefs-proxies.png']
    resources += [skin / name for name in names]
    for resource in resources:
        if resource.is_file():
            shutil.copy2(resource, images / resource.name)
    for size in (16, 32, 64, 128):
        shutil.copy2(UPSTREAM / 'icons' / f'Icon-{size}.png', OUT)

    libs = OUT / 'lib'
    libs.mkdir(exist_ok=True)
    for source, name in [('react/umd/react.production.min.js', 'react.js'),
                         ('react-dom/umd/react-dom.production.min.js', 'react-dom.js'),
                         ('prop-types/prop-types.min.js', 'prop-types.js'),
                         ('dompurify/dist/purify.min.js', 'dompurify.js'),
                         ('react-dom-factories/index.js', 'react-dom-factories.js')]:
        shutil.copy2(UPSTREAM / 'node_modules' / source, libs / name)
    copy_tree(UPSTREAM / 'lib/SingleFile', libs / 'SingleFile')
    shutil.copy2(SRC / 'zotero/chrome/content/zotero/xpcom/singlefile.js', OUT / 'singlefile-config.js')

    log = ROOT / 'connector-build.log'
    with log.open('w', encoding='utf-8') as stream:
        subprocess.run(['node', str(UPSTREAM / 'node_modules/gulp/bin/gulp.js'),
                        'process-custom-scripts', '--connector-version', '4.999.0', '-p'],
                       cwd=UPSTREAM, stdout=stream, stderr=subprocess.STDOUT, check=True)
    manifest_path = OUT / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest.pop('applications', None)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / 'manifest-v3.json').unlink()
    for image in list(images.glob('*2x.png')) + list(images.glob('*48px.png')):
        name = image.name.replace('@2x', '').replace('@48px', '')
        if name != image.name:
            shutil.copy2(image, images / name)
    print(f'Staged and transformed official Chromium build: {OUT}')
    print(f'Upstream Gulp log: {log}')


if __name__ == '__main__':
    main()
