"""Adapt the pinned QCE packers to this run's NapCat build; Actions only."""
import hashlib
import json
import os
from pathlib import Path
import platform
import runpy
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
PINS = json.loads((ROOT / '.github/scripts/release-components.json').read_text())


def package(kind):
    qce = ROOT / 'qce'
    source = ROOT / 'packages' / f'napcat-{kind}' / 'dist'
    for required in ['napcat.mjs', 'static/index.html', 'plugins/napcat-plugin-builtin/index.mjs']:
        if not (source / required).is_file():
            raise RuntimeError(f'Missing runner build output: {source / required}')
    os.chdir(qce)
    os.environ['QCE_VERSION'] = PINS['qce_version']
    os.environ['QCE_FRONTEND_OUT'] = str(qce / 'qce-v4-tool/out')
    if not os.environ.get('NAPCAT_VERSION'):
        raise RuntimeError('NAPCAT_VERSION is required')
    windows = platform.system() == 'Windows'
    binary = qce / 'qq-chat-export-server/target'
    if platform.system() == 'Linux':
        binary /= 'x86_64-unknown-linux-musl'
    binary /= 'release/qce-server.exe' if windows else 'release/qce-server'
    if not binary.is_file():
        raise RuntimeError(f'Missing current-run QCE binary: {binary}')
    os.environ['QCE_SERVER_BINARY'] = str(binary)
    os.environ['QCE_SERVER_WINDOWS_X64'] = str(binary) if windows else ''
    name = 'quick-pack.py' if kind == 'shell' else 'build-framework-plugin.py'
    script = qce / 'scripts' / name
    expected = PINS['quick_pack_sha256' if kind == 'shell' else 'framework_pack_sha256']
    if hashlib.sha256(script.read_bytes()).hexdigest() != expected:
        raise RuntimeError(f'Pinned QCE script drift: {name}')
    text = script.read_text(encoding='utf-8')
    if kind == 'shell':
        start = text.index('    # Get NapCat version\n')
        end = text.index('    # Fix NapCat bug:', start)
        replacement = f'''    napcat_version = os.environ['NAPCAT_VERSION']
    if os.path.exists(pack_dir):
        shutil.rmtree(pack_dir)
    shutil.copytree({str(source)!r}, pack_dir)
'''
        text = text[:start] + replacement + text[end:]
        text = text.replace('output_basename = f"NapCat-QCE-{os_name}-{arch}-v{qce_version}"', 'output_basename = f"NapCat-QCE-{os_name}-{arch}"')
    else:
        text = text.replace('napcat_version = get_napcat_latest_version()', "napcat_version = os.environ['NAPCAT_VERSION']")
        start = text.index('    # Download NapCat.Framework\n')
        end = text.index('    # Replace upstream napiLoader', start)
        text = text[:start] + f'    shutil.copytree({str(source)!r}, output_dir)\n\n' + text[end:]
        text = text.replace('output_zip = f"NapCat-Framework-QCE-v{qce_version}.zip"', 'output_zip = "NapCat-Framework-QCE.zip"')
    # Preserve embedded launcher line endings; keep the adapted script beside its helpers.
    adapted = script.with_name('fork-' + name)
    adapted.write_bytes(text.replace('\n', '\r\n').encode('utf-8'))
    sys.path.insert(0, str(script.parent))
    runpy.run_path(str(adapted), run_name='__main__')
    output = qce / ('NapCat-Framework-QCE' if kind == 'framework' else f'NapCat-QCE-{ {"Windows": "Windows-x64", "Linux": "Linux-x64", "Darwin": "macOS-arm64"}[platform.system()]}')
    shutil.copy2(ROOT / '.github/scripts/release-components.json', output / 'release-components.json')
    (output / 'build-source.json').write_text(json.dumps({k: os.environ[k] for k in ['SOURCE_SHA', 'NAPCAT_VERSION', 'GITHUB_WORKFLOW_SHA']}), encoding='utf-8')
    # Recreate after provenance is added. Validation unpacks this exact final archive.
    archive = output.with_suffix('.zip' if windows else '.tar.gz')
    if windows:
        shutil.make_archive(str(output), 'zip', output.parent, output.name)
    else:
        shutil.make_archive(str(output), 'gztar', output.parent, output.name)
    target = ROOT / 'release-assets'
    target.mkdir(exist_ok=True)
    shutil.copy2(archive, target / archive.name)


if __name__ == '__main__':
    package(sys.argv[1])
