"""Download a private tmux from the image's signed Debian package index."""
import hashlib
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import tempfile
import urllib.request

LIBRARIES = {
    'libevent_core-2.1.so.7': ('libevent-core-2.1-7t64', 'libevent-core-2.1-7'),
    'libjemalloc.so.2': ('libjemalloc2',), 'libsystemd.so.0': ('libsystemd0',),
    'libtinfo.so.6': ('libtinfo6',), 'libcap.so.2': ('libcap2',),
    'libgcrypt.so.20': ('libgcrypt20',), 'libgpg-error.so.0': ('libgpg-error0',),
    'liblz4.so.1': ('liblz4-1',), 'libzstd.so.1': ('libzstd1',),
    'liblzma.so.5': ('liblzma5',), 'libseccomp.so.2': ('libseccomp2',),
}


def private_tmux(root, tool='tmux'):
    from .runtime import Failure, write
    release = Path('/etc/os-release').read_text() if Path('/etc/os-release').exists() else ''
    if 'ID=debian' not in release or not all(shutil.which(x) for x in ('apt-cache', 'dpkg-deb', 'ldd')):
        raise Failure('TMUX_REQUIRED')
    if platform.machine() not in ('x86_64', 'aarch64'):
        raise Failure('UNSUPPORTED_PLATFORM')
    native = root / 'native'
    native.mkdir(parents=True, exist_ok=True, mode=0o700)
    libraries = native / 'usr/lib' / ('x86_64-linux-gnu' if platform.machine() == 'x86_64' else 'aarch64-linux-gnu')
    receipts = []
    apt_options = []
    refreshed = False
    def refresh_private_index():
        nonlocal refreshed, apt_options
        if refreshed:
            raise Failure('NATIVE_PACKAGE_UNAVAILABLE')
        refreshed = True
        lists = native / 'apt/lists'
        lists.mkdir(parents=True, exist_ok=True, mode=0o700)
        cache = native / 'apt/cache'
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        apt_options = ['-o', 'Dir::State::lists=' + str(lists), '-o', 'Dir::Cache=' + str(cache)]
        result = subprocess.run(['apt-get', *apt_options, 'update'], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=180)
        if result.returncode:
            raise Failure('SIGNED_INDEX_REFRESH_FAILED')
    def download(names):
        for name in names:
            result = subprocess.run(['apt-cache', *apt_options, 'show', name], capture_output=True, text=True, timeout=20)
            paragraphs = result.stdout.split('\n\n')
            metadata = next((dict(line.split(': ', 1) for line in part.splitlines() if ': ' in line and not line.startswith(' '))
                             for part in paragraphs if '\nSHA256: ' in part), None)
            if not metadata:
                continue
            filename, checksum = metadata.get('Filename', ''), metadata.get('SHA256', '')
            if not filename.startswith('pool/') or '..' in filename.split('/') or len(checksum) != 64:
                raise Failure('DEPENDENCY_METADATA_INVALID')
            url = 'https://deb.debian.org/debian/' + filename
            try:
                with urllib.request.urlopen(url, timeout=60) as response:
                    data = response.read(20_000_001)
            except urllib.error.HTTPError as error:
                if error.code == 404 and not refreshed:
                    # Old container images can retain indexes whose package
                    # files have left the mirror. Refresh signatures/indexes
                    # into the plugin directory, never /var/lib/apt or dpkg.
                    refresh_private_index()
                    return download(names)
                raise Failure('NATIVE_PACKAGE_DOWNLOAD_FAILED') from None
            if len(data) > 20_000_000 or hashlib.sha256(data).hexdigest() != checksum:
                raise Failure('DEPENDENCY_HASH_MISMATCH')
            fd, temporary = tempfile.mkstemp(suffix='.deb', dir=native)
            try:
                with os.fdopen(fd, 'wb') as file:
                    file.write(data)
                extraction = subprocess.run(['dpkg-deb', '-x', temporary, str(native)],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
                if extraction.returncode:
                    raise Failure('TMUX_EXTRACT_FAILED')
            finally:
                os.unlink(temporary)
            receipts.append({'name': name, 'version': metadata.get('Version'), 'sha256': checksum, 'url': url})
            return
        raise Failure('TMUX_PACKAGE_MISSING')
    if tool not in ('tmux', 'bwrap'):
        raise Failure('DEPENDENCY_UNSUPPORTED')
    download(('tmux' if tool == 'tmux' else 'bubblewrap',))
    env = {**os.environ, 'LD_LIBRARY_PATH': str(libraries)}
    for _ in range(5):
        result = subprocess.run(['ldd', str(native / ('usr/bin/' + tool))], env=env, capture_output=True, text=True, timeout=10)
        missing = [line.strip().split()[0] for line in result.stdout.splitlines() if 'not found' in line]
        if not missing:
            break
        for library in missing:
            if library not in LIBRARIES:
                raise Failure('TMUX_LIBRARY_UNSUPPORTED')
            download(LIBRARIES[library])
    probe = subprocess.run([str(native / ('usr/bin/' + tool)), '-V' if tool == 'tmux' else '--version'], env=env, capture_output=True, timeout=10)
    if probe.returncode:
        raise Failure('TMUX_RUNTIME_FAILED')
    (root / 'bin').mkdir(exist_ok=True, mode=0o700)
    wrapper = root / 'bin' / tool
    # Library search is scoped to tmux, preserving the other engines' runtime.
    content = '#!/bin/sh\nexport LD_LIBRARY_PATH=' + shlex.quote(str(libraries)) + \
              '\nexec ' + shlex.quote(str(native / ('usr/bin/' + tool))) + ' "$@"\n'
    fd, temporary = tempfile.mkstemp(dir=wrapper.parent)
    try:
        with os.fdopen(fd, 'w') as file:
            os.fchmod(file.fileno(), 0o700)
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, wrapper)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    from .runtime import read
    previous = read(root / 'native-dependencies.json').get('packages', [])
    combined = {row['name']: row for row in previous + receipts}
    write(root / 'native-dependencies.json', {'source': 'image signed APT index', 'packages': list(combined.values())})
