import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import manage as m
import host


def config():
    return dict(image='barumel/docker-astroneer-server@sha256:' + 'a'*64,
                name='Test', owner='Owner', password='secret', port=8777,
                public_ip='8.8.8.8', mode='direct', retention=2, autostart=True,
                external_port=8777, playit_image='', playit_secret='')


def result(out='', code=0):
    return subprocess.CompletedProcess([], code, out, '')


class Validation(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(m.validate(config())['port'], 8777)

    def test_bad_values(self):
        for k, v in [('port', True), ('port', 0), ('image', 'latest'), ('password', 'a\nb'),
                     ('public_ip', 'x'), ('public_ip', '192.168.1.1'), ('retention', 0),
                     ('external_port', 12345), ('owner', 'a"b'), ('mode', 'x')]:
            with self.subTest(k=k, v=v), self.assertRaises(ValueError):
                m.validate(dict(config(), **{k: v}))

    def test_no_execution_in_env(self):
        c = dict(config(), password='$(touch /tmp/never-execute)')
        self.assertIn('$(touch', m.env(m.validate(c)))
        self.assertIn('ASTRO_SERVER_DISABLE_ENCRYPTION=false', m.env(c))

    def test_unknown_config(self):
        with self.assertRaises(ValueError):
            m.validate(dict(config(), executable='x'))

    def test_playit(self):
        c = dict(config(), mode='playit', playit_image='ghcr.io/playit-cloud/playit-agent@sha256:'+'b'*64, playit_secret='a'*20)
        m.validate(c)

    def test_parser(self):
        for argv in ([], ['restore'], ['update'], ['start', '--yes'], ['diagnostics', 'oops']):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                m.parser().parse_args(argv)

    def test_host_args(self):
        h = dict(id=999, hostname='astro', storage='local-lvm', cores=2, ram=4096,
                 swap=1024, disk=30, bridge='vmbr0', ip='dhcp', gateway='', vlan='10', dns='1.1.1.1', search='')
        a = host.create_args(h, 'template', True)
        self.assertIn('nesting=1,keyctl=1', a)
        self.assertEqual(a[a.index('--unprivileged')+1], '1')
        self.assertIn('firewall=1,tag=10', a[a.index('--net0')+1])

    def test_host_invalid_no_side_effects(self):
        with patch.object(host, 'run') as run, self.assertRaises(ValueError):
            host.main(['--template', 'bad'])
        run.assert_not_called()


class Operations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root/'data'; self.base.mkdir(mode=0o700)
        self.saved = self.base/'game/Astro/Saved'; self.saved.mkdir(parents=True)
        (self.saved/'SaveGames').mkdir()
        (self.saved/'SaveGames/SAVE_1.savegame').write_bytes(b'original save')
        self.conf = self.root/'etc/config.json'
        m.atomic(self.conf, json.dumps(config()))
        self.back = self.root/'backups'
        for name, val in [('BASE', self.base), ('CONF', self.conf), ('BACK', self.back), ('LOCK', self.root/'lock')]:
            p = patch.object(m, name, val); p.start(); self.addCleanup(p.stop)
        previous_umask = os.umask(0o077)
        self.addCleanup(os.umask, previous_umask)
        capture = patch.object(m, 'capture_logs', return_value=result(code=1))
        capture.start(); self.addCleanup(capture.stop)
        self.output = contextlib.redirect_stdout(io.StringIO()); self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def test_backup_restore_round_trip(self):
        p = m.backup(config()); m.check_archive(p)
        (self.saved/'SaveGames/SAVE_1.savegame').write_bytes(b'changed')
        with patch.object(m, 'apply'), patch.object(m, 'run', return_value=result()):
            m.restore(p, config())
        self.assertEqual((self.saved/'SaveGames/SAVE_1.savegame').read_bytes(), b'original save')
        self.assertEqual(len(list(self.back.glob('*.tar.gz'))), 2)

    def test_old_backup_restore_can_preserve_current_playit_endpoint(self):
        p = m.backup(config())
        current = dict(config(), mode='playit', public_ip='8.8.4.4', port=12345, external_port=12345,
                       playit_image='ghcr.io/playit-cloud/playit-agent@sha256:'+'b'*64, playit_secret='agent-secret-12345')
        m.atomic(self.conf, json.dumps(current))
        with patch.object(m, 'apply') as apply, patch.object(m, 'run', return_value=result()):
            m.restore(p, current, keep_network=True)
        restored = apply.call_args.args[0]
        self.assertEqual(restored['public_ip'], '8.8.4.4')
        self.assertEqual(restored['port'], 12345)
        self.assertEqual(restored['playit_secret'], current['playit_secret'])

    def test_corrupt_archive(self):
        p = m.backup(config()); p.write_bytes(b'broken')
        with self.assertRaises(ValueError):
            m.check_archive(p)
        self.assertEqual((self.saved/'SaveGames/SAVE_1.savegame').read_bytes(), b'original save')

    def test_archive_traversal(self):
        p = self.root/'evil.tar.gz'
        with tarfile.open(p, 'w:gz') as t:
            t.addfile(tarfile.TarInfo('../escape'))
        m.atomic(p.with_suffix('.gz.sha256'), m.digest(p))
        with self.assertRaises(ValueError):
            m.check_archive(p)

    def test_link_rejected(self):
        (self.saved/'evil').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):
            m.backup(config())

    def test_retention(self):
        for _ in range(4):
            m.backup(config())
        self.assertEqual(len(list(self.back.glob('*.tar.gz'))), 2)

    def test_stopped_restarts_on_failure(self):
        with patch.object(m, 'active', return_value=True), patch.object(m, 'run') as run:
            with self.assertRaises(RuntimeError), m.stopped():
                raise RuntimeError('test')
            self.assertEqual(run.call_args_list[0].args[0], ['systemctl', 'stop', 'astroneer'])
            self.assertEqual(run.call_args_list[-1].args[0], ['systemctl', 'start', 'astroneer'])

    def test_repeated_provision(self):
        with patch.object(m, 'repair') as repair, patch.object(m, 'pull_verified') as pull:
            m.provision(self.conf)
        repair.assert_called_once_with(config(), start=True)
        pull.assert_not_called()

    def test_partial_provision(self):
        incoming = self.root/'incoming.json'; incoming.write_text(self.conf.read_text())
        self.conf.unlink()
        with patch.object(m, 'run', side_effect=RuntimeError('failed')), self.assertRaises(RuntimeError):
            m.provision(incoming)
        self.assertFalse(self.conf.exists())

    def test_missing_dependency(self):
        with self.assertRaises(FileNotFoundError):
            m.run(['astroneer-nonexistent-command-1234'])

    def test_update_backup_before_apply(self):
        calls = []
        @contextlib.contextmanager
        def stopped():
            yield
        with patch.object(m.os, 'geteuid', return_value=0), patch.object(m, 'stopped', stopped), patch.object(m, 'run', return_value=result()), patch.object(m, 'backup', side_effect=lambda c: calls.append('backup')), patch.object(m, 'apply', side_effect=lambda c: calls.append('apply')):
            m.main(['update', '--image', config()['image']])
        self.assertEqual(calls, ['backup', 'apply'])

    def test_service_commands(self):
        for cmd in ('start', 'stop', 'restart', 'status'):
            with patch.object(m.os, 'geteuid', return_value=0), patch.object(m, 'run', return_value=result()) as run:
                self.assertEqual(m.main([cmd]), 0)
                run.assert_called_once_with(['systemctl', cmd, 'astroneer'], False)

    def test_update_backup_failure_keeps_config(self):
        with patch.object(m.os, 'geteuid', return_value=0), patch.object(m, 'active', return_value=False), patch.object(m, 'run', return_value=result()), patch.object(m, 'backup', side_effect=RuntimeError('disk full')), patch.object(m, 'apply') as apply:
            with self.assertRaises(RuntimeError):
                m.main(['update', '--image', config()['image']])
        apply.assert_not_called()

    def test_restore_apply_failure_rolls_back_data(self):
        p = m.backup(config())
        path = self.saved/'SaveGames/SAVE_1.savegame'
        path.write_bytes(b'new live data')
        with patch.object(m, 'run', return_value=result()), patch.object(m, 'apply', side_effect=[RuntimeError('reload failed'), None]), self.assertRaises(RuntimeError):
            m.restore(p, config())
        self.assertEqual(path.read_bytes(), b'new live data')
        self.assertFalse((self.saved.parent/'Saved.rollback').exists())

    def test_config_redaction(self):
        out = io.StringIO()
        with patch.object(m.os, 'geteuid', return_value=0), contextlib.redirect_stdout(out):
            m.main(['config'])
        self.assertEqual(json.loads(out.getvalue())['password'], '[REDACTED]')
        self.assertNotIn('secret', json.loads(out.getvalue()).values())

    def test_restore_noninteractive_requires_yes(self):
        with patch.object(m.os, 'geteuid', return_value=0), patch.object(m.sys.stdin, 'isatty', return_value=False), patch.object(m, 'restore') as restore:
            with self.assertRaises(ValueError):
                m.main(['restore', '/unused'])
        restore.assert_not_called()

    def test_prestart_refuses_running_game(self):
        with patch.object(m.os, 'geteuid', return_value=0), patch.object(m, 'run', return_value=result('true')), patch.object(m, 'backup') as backup:
            with self.assertRaises(ValueError):
                m.main(['prestart'])
        backup.assert_not_called()

    def test_prestart_backup(self):
        with patch.object(m.os, 'geteuid', return_value=0), patch.object(m, 'run', return_value=result('false')), patch.object(m, 'backup') as backup:
            self.assertEqual(m.main(['prestart']), 0)
        backup.assert_called_once()

    def test_network_migration_prestart_never_prunes_old_backups(self):
        m.atomic(self.conf.parent/'network-transaction.json', '{}')
        with patch.object(m.os, 'geteuid', return_value=0), patch.object(m, 'run', return_value=result('false')), patch.object(m, 'backup') as backup:
            self.assertEqual(m.main(['prestart']), 0)
        backup.assert_called_once_with(config(), prune=False)

    def test_missing_tools_still_diagnostic_json(self):
        m.atomic(self.conf.parent/'server.env', m.env(config()))
        out = io.StringIO()
        with patch.object(m, 'run', side_effect=FileNotFoundError()), patch.object(m.socket, 'getaddrinfo', side_effect=OSError()), patch.object(m.socket, 'create_connection', side_effect=OSError()), contextlib.redirect_stdout(out):
            code = m.diagnostics(config(), True)
        self.assertEqual(code, 1)
        rows = {r['check']: r for r in json.loads(out.getvalue())}
        self.assertEqual(rows['runtime']['state'], 'FAIL')


    def test_unit_no_secret(self):
        text = m.unit(config()['image'])
        self.assertNotIn(config()['password'], text)
        self.assertIn('--cap-drop ALL', text)
        self.assertIn('--log-driver json-file --log-opt max-size=10m --log-opt max-file=3', text)
        self.assertIn('Restart=no', text)

    def test_diagnostics_separate_states_json(self):
        m.atomic(self.conf.parent/'server.env', m.env(config()))
        out = io.StringIO()
        with patch.object(m, 'run', return_value=result('true')), patch.object(m.socket, 'getaddrinfo', return_value=[]), patch.object(m.socket, 'create_connection', side_effect=OSError()), contextlib.redirect_stdout(out):
            m.diagnostics(config(), True)
        rows = {r['check']: r for r in json.loads(out.getvalue())}
        self.assertEqual(rows['process']['state'], 'PASS')
        self.assertEqual(rows['backend_registration']['state'], 'WARN')
        self.assertEqual(rows['public_udp_reachability']['state'], 'WARN')
        self.assertNotIn('secret', out.getvalue())


if __name__ == '__main__':
    unittest.main()
