import contextlib
import io
import stat
import sys
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import host
from test_project import config


class HostAutomation(unittest.TestCase):
    def args(self, *extra):
        args = host.parser().parse_args(['--template', 'local:vztmpl/debian-12-standard_12.0-1_amd64.tar.zst', '--yes', '--ctid', '333', '--owner', 'SteamOwner', '--password-file', '/root/private-password', '--approve-image', *extra])
        args.password_file = MagicMock()
        args.password_file.lstat.return_value = type('File', (), {'st_mode': stat.S_IFREG | 0o600, 'st_uid': 0})()
        args.password_file.read_text.return_value = 'example-secret\n'
        return args

    def test_all_network_choices_are_deferred_without_secrets_or_public_ip(self):
        for method in ('playit-local', 'playit-existing', 'direct', 'lan'):
            with self.subTest(method=method), patch.object(host, 'resolve', return_value={'image': config()['image']}), patch.object(host, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
                h, c, proof = host.unattended(self.args('--network-mode', method))
            self.assertEqual(c['network_preference'], method)
            self.assertEqual(c['mode'], 'network-pending')
            self.assertEqual(c['public_ip'], '')
            self.assertEqual(c['playit_secret'], '')
            self.assertEqual(h['ram'], 4096)
            self.assertTrue(proof['game']['approved'])
            run.assert_not_called()  # Building configuration never deploys a guest.

    def test_resource_flags_and_static_address_are_validated(self):
        args = self.args('--storage', 'local-zfs', '--ipv4', '10.23.45.20/24', '--gateway', '10.23.45.1', '--vlan', '20', '--cores', '4', '--ram', '8192')
        with patch.object(host, 'resolve', return_value={'image': config()['image']}), contextlib.redirect_stdout(io.StringIO()):
            h, _, _ = host.unattended(args)
        self.assertEqual((h['storage'], h['cores'], h['ram'], h['vlan']), ('local-zfs', 4, 8192, '20'))
        args.gateway = ''
        with self.assertRaisesRegex(ValueError, 'gateway'):
            host.unattended(args)

    def test_secret_file_permissions_and_ownership(self):
        for uid, mode in ((0, 0o644), (1000, 0o600)):
            args = self.args(); args.password_file.lstat.return_value.st_uid = uid
            args.password_file.lstat.return_value.st_mode = stat.S_IFREG | mode
            with self.subTest(uid=uid, mode=mode), patch.object(host, 'resolve') as resolve, self.assertRaisesRegex(ValueError, 'root-owned'):
                host.unattended(args)
            resolve.assert_not_called()

    def test_missing_explicit_approval_never_fetches_image(self):
        args = self.args(); args.approve_image = False
        with patch.object(host, 'resolve') as resolve, self.assertRaisesRegex(ValueError, '--approve-image'):
            host.unattended(args)
        resolve.assert_not_called()

    def test_password_not_printed(self):
        out = io.StringIO()
        with patch.object(host, 'resolve', return_value={'image': config()['image']}), contextlib.redirect_stdout(out):
            host.unattended(self.args())
        self.assertNotIn('example-secret', out.getvalue())

    def test_flags_are_not_silently_ignored_in_interactive_mode(self):
        with patch.object(host, 'run') as run, self.assertRaisesRegex(ValueError, 'require --yes'):
            host.main(['--template', 'template', '--ctid', '333'])
        run.assert_not_called()
