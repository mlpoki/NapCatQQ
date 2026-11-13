"""Validate the patched installer on a Windows Actions runner and replace its ZIP."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from patch_installer import NEW_URL, OBSOLETE_MIRRORS, OLD_URL, patch_installer

REPO = 'mlpoki/NapCatQQ'
TAG = 'v4.18.42'
RELEASE_ID = 443723727
NAME = 'NapCat.Shell.Windows.OneKey.zip'
ORIGINAL_ZIP_SHA256 = '46c3ec155aa61f12e89bbf97013a21243d00b0019b5565992a88852d7627f39c'


def api(route, method='GET', payload=None):
    command = ['gh', 'api', route, '--method', method]
    data = None
    if payload is not None:
        command.extend(['--input', '-'])
        data = json.dumps(payload).encode('utf-8')
    result = subprocess.run(command, input=data, stdout=subprocess.PIPE, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def manifest(release):
    return {asset['name']: (asset['size'], asset['digest']) for asset in release['assets']}


def main():
    root = Path(os.environ['RUNNER_TEMP']) / 'onekey-installer-repair'
    root.mkdir()
    artifacts = Path(os.environ['GITHUB_WORKSPACE']) / 'repair-artifacts'
    artifacts.mkdir()
    before = api(f'repos/{REPO}/releases/latest')
    assert before['id'] == RELEASE_ID and before['tag_name'] == TAG
    before_manifest = manifest(before)
    old_asset = next(asset for asset in before['assets'] if asset['name'] == NAME)
    assert old_asset['digest'] == 'sha256:' + ORIGINAL_ZIP_SHA256
    (artifacts / 'release-before.json').write_text(json.dumps(before, ensure_ascii=False), encoding='utf-8')
    subprocess.run(['gh', 'release', 'download', TAG, '--repo', REPO, '--pattern', NAME,
                    '--dir', str(root)], check=True)
    original = root / NAME
    assert digest(original) == ORIGINAL_ZIP_SHA256
    shutil.copy2(original, artifacts / ('original-' + NAME))
    run_dir = root / 'installer-run'
    with zipfile.ZipFile(original) as archive:
        archive.extractall(run_dir)
    patched = root / 'NapCatInstaller.exe'
    patch_installer(run_dir / 'NapCatInstaller.exe', patched)
    shutil.copy2(patched, run_dir / 'NapCatInstaller.exe')

    # Execute only on the disposable GitHub-hosted Windows runner.
    log = artifacts / 'installer-native.log'
    installer_env = dict(os.environ)
    installer_env.pop('GH_TOKEN', None)
    installer_env.pop('GITHUB_TOKEN', None)
    with log.open('wb') as output:
        process = subprocess.Popen([str(run_dir / 'NapCatInstaller.exe')], cwd=run_dir,
                                   stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW, env=installer_env)
        try:
            process.communicate(input=b'\r\n' * 16, timeout=900)
        except subprocess.TimeoutExpired:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
            raise
    raw = log.read_bytes()
    encoding = 'utf-16-le' if raw.count(b'\x00') > len(raw) // 6 else 'utf-8'
    text = raw.decode(encoding, errors='replace')
    (artifacts / 'installer-native-utf8.log').write_text(text, encoding='utf-8')
    print(text[-6000:], flush=True)
    assert process.returncode == 0, f'Installer exited with {process.returncode}'
    patched_bytes = patched.read_bytes()
    assert NEW_URL.encode('utf-16-le') in patched_bytes
    assert OLD_URL.encode('utf-16-le') not in patched_bytes
    assert all(url.encode('utf-16-le') not in patched_bytes for url in OBSOLETE_MIRRORS)

    reference = root / 'reference'
    subprocess.run(['gh', 'release', 'download', TAG, '--repo', REPO, '--pattern',
                    'NapCat.Shell.zip', '--dir', str(reference)], check=True)
    shell_asset = next(asset for asset in before['assets'] if asset['name'] == 'NapCat.Shell.zip')
    assert 'sha256:' + digest(reference / 'NapCat.Shell.zip') == shell_asset['digest']
    downloaded = run_dir / 'NapCat.Shell.zip'
    assert downloaded.is_file() and 'sha256:' + digest(downloaded) == shell_asset['digest'], 'Native download SHA256 mismatch'
    with zipfile.ZipFile(reference / 'NapCat.Shell.zip') as shell:
        reference_core = hashlib.sha256(shell.read('napcat.mjs')).hexdigest()
    installed = list(run_dir.rglob('napcat.mjs'))
    assert len(installed) == 1 and digest(installed[0]) == reference_core, 'Installed NapCat core mismatch'
    print('Native fork download and installed NapCat core SHA256 PASS', flush=True)

    candidate = artifacts / ('NapCat.Shell.Windows.OneKey.links-fixed.zip')
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(candidate, 'w') as output:
        for info in source.infolist():
            content = patched.read_bytes() if info.filename == 'NapCatInstaller.exe' else source.read(info.filename)
            output.writestr(info, content)
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(candidate) as output:
        assert source.namelist() == output.namelist()
        for name in source.namelist():
            if name != 'NapCatInstaller.exe':
                assert source.read(name) == output.read(name), f'Unrelated member changed: {name}'
        assert output.read('NapCatInstaller.exe') == patched.read_bytes()
    patched_hash = 'sha256:' + digest(candidate)
    current = api(f'repos/{REPO}/releases/{RELEASE_ID}')
    assert manifest(current) == before_manifest, 'Release changed before upload'
    subprocess.run(['gh', 'release', 'upload', TAG, '--repo', REPO, str(candidate)], check=True)
    current = api(f'repos/{REPO}/releases/{RELEASE_ID}')
    staged = next(asset for asset in current['assets'] if asset['name'] == candidate.name)
    assert staged['state'] == 'uploaded' and staged['digest'] == patched_hash
    backup_name = NAME + '.before-link-fix'
    api(f'repos/{REPO}/releases/assets/{old_asset["id"]}', 'PATCH', {'name': backup_name})
    try:
        api(f'repos/{REPO}/releases/assets/{staged["id"]}', 'PATCH', {'name': NAME})
    except Exception:
        api(f'repos/{REPO}/releases/assets/{old_asset["id"]}', 'PATCH', {'name': NAME})
        raise
    after = api(f'repos/{REPO}/releases/{RELEASE_ID}')
    updated = next(asset for asset in after['assets'] if asset['name'] == NAME)
    assert updated['digest'] == patched_hash and updated['state'] == 'uploaded'
    api(f'repos/{REPO}/releases/assets/{old_asset["id"]}', 'DELETE')
    after = api(f'repos/{REPO}/releases/{RELEASE_ID}')
    expected = dict(before_manifest)
    expected[NAME] = (candidate.stat().st_size, patched_hash)
    assert manifest(after) == expected and after['body'] == before['body']
    (artifacts / 'release-after.json').write_text(json.dumps(after, ensure_ascii=False), encoding='utf-8')
    print(f'Replaced {NAME}: {patched_hash}', flush=True)


if __name__ == '__main__':
    main()
