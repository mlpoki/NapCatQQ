"""Unpack and smoke-check the exact release archives on each target runner."""
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]


def run(*args, cwd=None, env=None):
    subprocess.run(args, cwd=cwd, env=env, check=True, timeout=60)


def require(root, *names):
    for name in names:
        file = root / name
        if not file.is_file() or file.stat().st_size == 0:
            raise RuntimeError(f'Missing/empty: {file}')


def native(root):
    system = platform.system()
    suffix = {'Windows': 'win32.x64', 'Linux': 'linux.x64', 'Darwin': 'darwin.arm64'}[system]
    addons = [root / 'native' / folder / name for folder, name in [
        ('packet', f'MoeHoo.{suffix}.node'),
        ('napi2native', f'napi2native.{suffix}.node'),
        ('ffmpeg', f'ffmpegAddon.{suffix}.node'),
    ]]
    for file in addons:
        require(file.parent, file.name)
        if system != 'Windows':
            run('file', str(file))
        if system == 'Darwin':
            arch = subprocess.check_output(['lipo', '-archs', str(file)], text=True)
            if 'arm64' not in arch.split():
                raise RuntimeError(f'Not arm64: {file}: {arch}')
            run('codesign', '--force', '--sign', '-', str(file))
        # Each addon is tested in its own process: a crash must fail the job.
        run('node', '-e', 'require(process.argv[1]); console.log("native-load PASS"); process.exit(0)', str(file))


def smoke(root):
    windows = platform.system() == 'Windows'
    require(root, 'qce-server.exe' if windows else 'qce-server', 'static/qce/index.html',
            'plugins/napcat-plugin-qce/index.mjs', 'plugins/napcat-plugin-qce/runtime/rustBridge.mjs',
            'plugins/napcat-plugin-builtin/index.mjs', 'config/plugins.json', 'build-source.json')
    enabled = json.loads((root / 'config/plugins.json').read_text())
    if not enabled.get('napcat-plugin-qce') or not enabled.get('napcat-plugin-builtin'):
        raise RuntimeError('Required plugins not enabled')
    provenance = json.loads((root / 'build-source.json').read_text())
    if provenance['SOURCE_SHA'] != os.environ['SOURCE_SHA']:
        raise RuntimeError('Wrong source snapshot')
    server = root / ('qce-server.exe' if windows else 'qce-server')
    if platform.system() == 'Darwin':
        run('file', str(server))
        run('lipo', '-verify_arch', 'arm64', str(server))
        run('codesign', '--force', '--sign', '-', str(server))
    if platform.system() == 'Linux':
        run('file', str(server))
        header = subprocess.check_output(['readelf', '-h', str(server)], text=True)
        if 'X86-64' not in header:
            raise RuntimeError('Wrong Linux server architecture')
        if 'Requesting program interpreter' in subprocess.check_output(['readelf', '-l', str(server)], text=True):
            raise RuntimeError('Linux server is not static')
        require(root, 'qq_magic.so', 'libnapcat_launcher.so', 'napcat-bootstrap.mjs', 'launcher-user.sh')
    framework = 'Framework' in root.name
    if framework:
        require(root, 'napiloader.dll', 'napimain.exe', 'nativeLoader.cjs', 'napiLoader.bat')
        command = [str(server)]
    else:
        require(root, 'qce-standalone.mjs', 'launcher-user.bat' if windows else 'launcher-user.sh')
        run('node', '--check', str(root / 'qce-standalone.mjs'))
        command = ['node', 'qce-standalone.mjs', '40653']
    env = dict(os.environ, QCE_STANDALONE_MODE='1', QCE_NO_AUTO_OPEN='1', QCE_SERVER_PORT='40653')
    # Standalone prints an ephemeral local access token; never copy its logs to CI output.
    with open(root / 'smoke-private.log', 'wb') as log:
        process = subprocess.Popen(command, cwd=root, env=env, stdout=log, stderr=log)
        try:
            for _ in range(90):
                if process.poll() is not None:
                    raise RuntimeError(f'QCE standalone exited: {process.returncode}')
                try:
                    with urllib.request.urlopen('http://127.0.0.1:40653/qce', timeout=2) as response:
                        if response.status == 200 and b'<html' in response.read().lower():
                            print(f'QCE standalone HTTP PASS: {root.name}')
                            break
                except OSError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError('QCE HTTP page unavailable')
        finally:
            if windows:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
            else:
                process.terminate()
            process.wait(timeout=20)


def main():
    if platform.system() == 'Darwin' and platform.machine() != 'arm64':
        raise RuntimeError('macOS runner must be native arm64')
    for archive in sorted((ROOT / 'release-assets').iterdir()):
        dest = ROOT / 'unpacked' / archive.name.replace('.tar.gz', '').replace('.zip', '')
        dest.mkdir(parents=True)
        shutil.unpack_archive(archive, dest)
        if 'OneKey' in archive.name:
            require(dest, 'node.exe', 'wrapper.node', 'QQNT.dll', 'index.js', 'napcat.bat', 'napcat/napcat.mjs', 'napcat/static/index.html', 'napcat/plugins/napcat-plugin-builtin/index.mjs')
            run(str(dest / 'node.exe'), '--check', str(dest / 'index.js'))
            run(str(dest / 'node.exe'), '-e', 'if (!require("./wrapper.node").NodeQQNTWrapperUtil) throw Error("wrapper ABI mismatch"); console.log("QQ wrapper native-load PASS"); process.exit(0)', cwd=dest)
            native(dest / 'napcat')
        else:
            root = dest / dest.name
            require(root, 'napcat.mjs', 'static/index.html')
            native(root)
            smoke(root)
        print(f'Archive acceptance PASS: {archive.name}')


if __name__ == '__main__':
    main()
