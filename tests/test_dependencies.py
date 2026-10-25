import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from hermes_omnigent.dependencies import private_tmux
from hermes_omnigent.runtime import Failure


class NativeDependenciesTests(unittest.TestCase):
    def test_empty_image_index_is_refreshed_privately_before_downloading(self):
        self.install_from_index()

    def test_security_archive_uses_same_signed_hash_when_main_archive_returns_404(self):
        self.install_from_index(security=True)

    def install_from_index(self, security=False):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            refreshed = False
            calls = []
            package = b'verified-test-package'
            def run(argv, **kwargs):
                nonlocal refreshed
                calls.append(argv)
                if argv[0] == 'apt-get':
                    refreshed = True
                    return SimpleNamespace(returncode=0)
                if argv[0] == 'apt-cache':
                    metadata = ('Package: tmux\nVersion: 3.5\nFilename: pool/main/t/tmux/tmux.deb\n'
                                'SHA256: ' + hashlib.sha256(package).hexdigest() + '\n')
                    return SimpleNamespace(returncode=0, stdout=metadata if refreshed else '')
                return SimpleNamespace(returncode=0, stdout='')
            urls = []
            def fetch(url, **kwargs):
                urls.append(url)
                if security and '/debian-security/' not in url:
                    raise urllib.error.HTTPError(url, 404, 'Not Found', {}, None)
                return io.BytesIO(package)
            real_read = Path.read_text
            def read(path, *args, **kwargs):
                return 'ID=debian\n' if str(path) == '/etc/os-release' else real_read(path, *args, **kwargs)
            with patch.object(Path, 'read_text', read), patch('hermes_omnigent.dependencies.platform.machine', return_value='x86_64'), \
                 patch('hermes_omnigent.dependencies.shutil.which', return_value='/usr/bin/tool'), \
                 patch('hermes_omnigent.dependencies.subprocess.run', side_effect=run), \
                 patch('hermes_omnigent.dependencies.urllib.request.urlopen', side_effect=fetch):
                private_tmux(root)
            updates = [c for c in calls if c[0] == 'apt-get']
            self.assertEqual(len(updates), 1)
            self.assertIn('Dir::State::lists=' + str(root / 'native/apt/lists'), updates[0])
            self.assertNotIn('install', updates[0])
            self.assertTrue((root / 'bin/tmux').is_file())
            if security:
                self.assertTrue(any('/debian-security/' in url for url in urls))

    def test_unavailable_package_after_private_refresh_fails_without_retry_loop(self):
        with tempfile.TemporaryDirectory() as temp:
            calls = []
            def run(argv, **kwargs):
                calls.append(argv)
                return SimpleNamespace(returncode=0, stdout='')
            with patch.object(Path, 'read_text', return_value='ID=debian\n'), \
                 patch('hermes_omnigent.dependencies.platform.machine', return_value='x86_64'), \
                 patch('hermes_omnigent.dependencies.shutil.which', return_value='/usr/bin/tool'), \
                 patch('hermes_omnigent.dependencies.subprocess.run', side_effect=run):
                with self.assertRaises(Failure) as error:
                    private_tmux(Path(temp))
            self.assertEqual(error.exception.code, 'TMUX_PACKAGE_MISSING')
            self.assertEqual(sum(c[0] == 'apt-get' for c in calls), 1)
