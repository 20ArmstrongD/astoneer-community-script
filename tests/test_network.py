"""Networking acceptance fixtures: no Proxmox, Playit account or public UDP traffic."""
import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import manage as m
import network as n
import playit as p
from test_project import config, result
from test_playit import AGENT_IMAGE, AGENT_SECRET, connected, dns

GUEST = '10.23.45.10'


class NetworkTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for key, value in [('CONF', self.root/'etc/config.json'), ('UNIT', self.root/'systemd/astroneer.service'), ('BASE', self.root/'data'), ('BACK', self.root/'backups'), ('LOCK', self.root/'run/astroneer.lock')]:
            patcher = patch.object(m, key, value); patcher.start(); self.addCleanup(patcher.stop)
        self.old = dict(config(), mode='lan', public_ip=GUEST)
        m.atomic(m.CONF, json.dumps(self.old)); m.atomic(m.CONF.parent/'server.env', m.env(self.old)+'CUSTOM_RUNTIME=keep\n')
        m.atomic(m.UNIT, 'original unit')
        self.ini = m.BASE/'game/Astro/Saved/Config/WindowsServer/Engine.ini'
        self.ini.parent.mkdir(parents=True); self.ini.write_bytes(b'original ini\xff')
        self.save = m.BASE/'world.savegame'; self.save.write_bytes(b'keep world')
        self.was_running = False
        self.calls = []
        def run(args, check=True):
            self.calls.append(args)
            if args[0] == 'ip':
                return result(json.dumps([{'ifname': 'eth0', 'addr_info': [{'family': 'inet', 'scope': 'global', 'local': GUEST}]}]))
            if args[:3] == ['systemctl', 'is-active', '--quiet']:
                return result(code=0 if self.was_running else 3)
            return result()
        self.runner = patch.object(m, 'run', side_effect=run); self.runner.start(); self.addCleanup(self.runner.stop)
        self.backup = patch.object(m, 'backup'); self.backup.start(); self.addCleanup(self.backup.stop)
        free = patch.object(p, 'port_free'); free.start(); self.addCleanup(free.stop)
        self.output = contextlib.redirect_stdout(io.StringIO()); self.output.__enter__(); self.addCleanup(self.output.__exit__, None, None, None)

    def args(self, *argv):
        return m.parser().parse_args(['network', 'configure', *argv])

    def linked(self):
        m.atomic(p.paths(m)[0], json.dumps({'image': AGENT_IMAGE, 'configured': False}))
        m.atomic(p.paths(m)[1], AGENT_SECRET)

    def test_existing_agent_works_without_local_install_or_credentials(self):
        args = self.args('--mode', 'playit-existing', '--public-ip', '8.8.8.8', '--port', '12345', '--destination-ip', GUEST, '--yes')
        with patch.object(p, 'setup') as setup, patch.object(p, 'state') as state, patch.object(p, 'load_meta') as meta:
            n.configure(m, args)
        setup.assert_not_called(); state.assert_not_called(); meta.assert_not_called()
        current = m.load()
        self.assertEqual(current['mode'], 'playit-external')
        self.assertEqual(current['playit_secret'], '')
        self.assertFalse(p.paths(m)[1].exists())
        for key in ('image', 'name', 'owner', 'password'):
            self.assertEqual(current[key], self.old[key])
        self.assertFalse(any(p.SERVICE in call for call in self.calls))

    def test_legacy_explicit_endpoint_command_no_longer_requires_local_agent(self):
        p.configure(m, '8.8.8.8', 12345, 12345, yes=True)
        self.assertEqual(m.load()['mode'], 'playit-external')
        self.assertEqual(n.profile(m)['destination_ip'], GUEST)

    def test_legacy_alias_retains_shared_mode_when_local_metadata_remains(self):
        self.linked()
        n.apply_plan(m, n.plan(m, 'playit-existing', '8.8.8.8', 12345))
        with patch.object(p, 'state', side_effect=AssertionError('no local query')):
            p.configure(m, '8.8.4.4', 12345, yes=True)
        self.assertEqual(m.load()['mode'], 'playit-external')
        self.assertEqual(m.load()['public_ip'], '8.8.4.4')

    def test_foreign_destination_refused_without_changes(self):
        before = m.CONF.read_bytes()
        with self.assertRaisesRegex(ValueError, 'does not match this Astroneer'):
            n.plan(m, 'playit-existing', '8.8.8.8', 12345, destination_ip='10.23.45.99')
        self.assertEqual(before, m.CONF.read_bytes())
        self.assertFalse(any('stop' in call for call in self.calls))

    def test_remote_loopback_is_not_the_game_guest(self):
        with self.assertRaises(ValueError):
            n.plan(m, 'playit-existing', '8.8.8.8', 12345, destination_ip='127.0.0.1')

    def test_matching_ports_required_in_all_modes(self):
        for mode in n.MODES:
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'Unsupported port mapping'):
                n.plan(m, mode, '8.8.8.8', 12345, 8777)

    def test_invalid_ports(self):
        for port in (0, -1, 1023, 65536, True, '8777'):
            with self.subTest(port=port), self.assertRaises(ValueError):
                n.plan(m, 'direct', '8.8.8.8', port)

    def test_invalid_public_addresses(self):
        for address in ('', GUEST, 'host.example', '::1', '224.0.0.1'):
            with self.subTest(address=address), self.assertRaises(ValueError):
                n.plan(m, 'playit-existing', address, 12345)

    def test_direct_and_lan_never_access_playit(self):
        with patch.object(p, 'setup') as setup, patch.object(p, 'state') as state:
            for mode in ('direct', 'lan'):
                desired = n.plan(m, mode, '8.8.8.8' if mode == 'direct' else None, 12345)
                n.apply_plan(m, desired)
                n.status(m, testing=False)
        setup.assert_not_called(); state.assert_not_called()
        self.assertFalse(any(p.SERVICE in call for call in self.calls))

    def test_switch_all_modes_preserves_data_and_credentials(self):
        self.linked()
        for mode in n.MODES:
            with self.subTest(mode=mode):
                desired = n.plan(m, mode, None if mode == 'lan' else '8.8.8.8', 12345)
                n.apply_plan(m, desired)
                self.assertEqual(self.save.read_bytes(), b'keep world')
                self.assertEqual(m.load()['password'], self.old['password'])
                self.assertEqual(m.load()['image'], self.old['image'])
                self.assertIn('CUSTOM_RUNTIME=keep', (m.CONF.parent/'server.env').read_text())
                self.assertNotIn('astroneer-playit.service', m.UNIT.read_text())
        self.assertEqual(p.paths(m)[1].read_text(), AGENT_SECRET)

    def test_local_to_shared_leaves_agent_identity_and_process_alone(self):
        self.linked()
        n.apply_plan(m, n.plan(m, 'playit-local', '8.8.8.8', 12345))
        before = p.paths(m)[1].read_bytes(); self.calls.clear()
        n.apply_plan(m, n.plan(m, 'playit-existing', '8.8.8.8', 12345))
        self.assertEqual(before, p.paths(m)[1].read_bytes())
        self.assertFalse(any(p.SERVICE in call for call in self.calls))
        self.assertEqual(m.load()['playit_secret'], '')

    def test_repeated_configuration_does_not_restart_or_rewrite(self):
        desired = n.plan(m, 'playit-existing', '8.8.8.8', 12345)
        n.apply_plan(m, desired); before = m.CONF.stat().st_mtime_ns; self.calls.clear()
        n.apply_plan(m, desired)
        self.assertEqual(before, m.CONF.stat().st_mtime_ns)
        self.assertFalse(any('start' in call or 'stop' in call for call in self.calls))

    def test_role_only_switch_does_not_interrupt_running_game(self):
        self.linked()
        n.apply_plan(m, n.plan(m, 'playit-local', '8.8.8.8', 12345))
        self.was_running = True; self.calls.clear()
        n.apply_plan(m, n.plan(m, 'playit-existing', '8.8.8.8', 12345))
        self.assertNotIn(['systemctl', 'stop', 'astroneer'], self.calls)
        self.assertNotIn(['systemctl', 'start', 'astroneer'], self.calls)

    def test_stopped_game_stays_stopped_without_start(self):
        n.apply_plan(m, n.plan(m, 'direct', '8.8.8.8', 12345))
        self.assertNotIn(['systemctl', 'start', 'astroneer'], self.calls)

    def test_game_start_failure_does_not_revert_valid_endpoint(self):
        original = m.run
        def fail(args, check=True):
            if args == ['systemctl', 'start', 'astroneer']:
                raise RuntimeError('premature game exit')
            return original(args, check)
        with patch.object(m, 'run', side_effect=fail), self.assertRaisesRegex(RuntimeError, 'Network settings are saved; GAME STARTUP failed'):
            n.apply_plan(m, n.plan(m, 'playit-existing', '8.8.8.8', 12345), start=True)
        self.assertEqual(m.load()['public_ip'], '8.8.8.8')
        self.assertFalse(p.paths(m)[2].exists())

    def test_transaction_write_failure_restores_exact_files_no_agent_actions(self):
        before = m.CONF.read_bytes(), m.UNIT.read_bytes(), self.ini.read_bytes()
        original = m.apply
        def fail(values):
            original(values); self.ini.write_bytes(b'partial'); raise OSError('write failed')
        with patch.object(m, 'apply', side_effect=fail), self.assertRaises(OSError):
            n.apply_plan(m, n.plan(m, 'playit-existing', '8.8.8.8', 12345))
        self.assertEqual(before, (m.CONF.read_bytes(), m.UNIT.read_bytes(), self.ini.read_bytes()))
        self.assertFalse(any(p.SERVICE in call for call in self.calls))

    def test_committed_snapshot_can_be_rolled_back_by_cli(self):
        n.apply_plan(m, n.plan(m, 'direct', '8.8.8.8', 12345))
        snapshot = next(m.BACK.glob('network-*'))
        n.rollback(m, snapshot=snapshot, yes=True)
        self.assertEqual(m.load(), self.old)
        self.assertEqual(self.save.read_bytes(), b'keep world')

    def test_startup_never_requires_local_agent_even_for_legacy_playit(self):
        self.linked()
        current = dict(self.old, mode='playit', public_ip='8.8.8.8', playit_image=AGENT_IMAGE, playit_secret=AGENT_SECRET)
        m.atomic(m.CONF, json.dumps(current))
        with patch.object(p, 'state', side_effect=AssertionError('must not query local agent')):
            n.guard(m)
        self.assertNotIn('astroneer-playit.service', m.unit(current['image']))

    def test_external_agent_and_transport_report_unknown(self):
        n.apply_plan(m, n.plan(m, 'playit-existing', '8.8.8.8', 12345))
        with patch.object(p, 'state', side_effect=AssertionError('no local IPC')), patch.object(m, 'runtime_probe', return_value={'game_pids': [1], 'ready': True}):
            rows = {r['check']: r for r in n.status(m)}
        self.assertEqual(rows['external_agent']['state'], 'WARN')
        self.assertEqual(rows['game_process']['state'], 'PASS')
        self.assertEqual(rows['tunnel_transport_connected']['state'], 'WARN')
        self.assertNotIn('playit_agent', rows)

    def test_interactive_existing_agent_menu(self):
        args = self.args()
        with patch('builtins.input', side_effect=['2', '', '8.8.8.8', '12345', 'CONFIGURE', 'no']), patch.object(p, 'setup') as setup:
            n.configure(m, args)
        self.assertEqual(m.load()['mode'], 'playit-external')
        setup.assert_not_called()

    def test_interactive_cancel_has_no_side_effects(self):
        before = m.CONF.read_bytes()
        with patch('builtins.input', return_value='4'):
            n.configure(m, self.args())
        self.assertEqual(before, m.CONF.read_bytes())
        self.assertEqual(self.calls, [])

    def test_interactive_direct_and_lan_menu(self):
        for choice, answers, mode in [('1', ['3', '1', '', '8.8.8.8', '12345', 'CONFIGURE', 'no'], 'direct'), ('2', ['3', '2', '', '12345', 'CONFIGURE', 'no'], 'lan')]:
            with self.subTest(choice=choice), patch('builtins.input', side_effect=answers):
                n.configure(m, self.args())
                self.assertEqual(m.load()['mode'], mode)

    def test_automated_new_agent_requires_approval_and_private_file(self):
        args = self.args('--mode', 'playit-local', '--yes')
        args.agent_secret_file = MagicMock()
        args.agent_secret_file.lstat.return_value = SimpleNamespace(st_mode=0o100600, st_uid=0)
        args.agent_secret_file.read_text.return_value = AGENT_SECRET
        with self.assertRaisesRegex(ValueError, '--approve-image'):
            n.prepare_local(m, args)
        args.approve_image = True
        proof = {'image': AGENT_IMAGE, 'platform': 'linux/amd64'}
        with patch.object(p, 'resolve', return_value=proof), patch.object(p, 'install_agent') as install:
            n.prepare_local(m, args)
        install.assert_called_once_with(m, AGENT_IMAGE, AGENT_SECRET, proof)
        args.agent_secret_file.lstat.return_value.st_mode = 0o100644
        with self.assertRaisesRegex(ValueError, 'root-owned file'):
            n.prepare_local(m, args)

    def test_missing_local_credentials_does_not_block_external_mode(self):
        self.linked(); p.paths(m)[1].unlink()
        with self.assertRaisesRegex(ValueError, 'Local agent credentials missing'):
            n.apply_plan(m, n.plan(m, 'playit-local', '8.8.8.8', 12345))
        n.apply_plan(m, n.plan(m, 'playit-existing', '8.8.8.8', 12345))
        self.assertEqual(m.load()['mode'], 'playit-external')

    def test_account_limit_is_actionable_and_does_not_delete_agent(self):
        self.linked()
        with patch.object(p, 'state', return_value=dict(connected(), phase='disabled_over_limit')), self.assertRaisesRegex(ValueError, 'account limit'):
            p.select_endpoint(m, yes=True)
        self.assertEqual(p.paths(m)[1].read_text(), AGENT_SECRET)

    def test_missing_external_tunnel_cannot_be_invented_by_diagnostics(self):
        n.apply_plan(m, n.plan(m, 'playit-existing', '8.8.8.8', 12345))
        with patch.object(m, 'runtime_probe', return_value={'game_pids': [], 'ready': False}):
            rows = {r['check']: r for r in n.status(m)}
        self.assertEqual(rows['game_process']['state'], 'FAIL')
        self.assertEqual(rows['external_agent']['state'], 'WARN')
        self.assertEqual(rows['external_udp_reachability']['state'], 'WARN')

    def test_upgrade_repair_preserves_legacy_config_removes_agent_dependency(self):
        self.linked()
        current = dict(self.old, mode='playit', public_ip='8.8.8.8', playit_image=AGENT_IMAGE, playit_secret=AGENT_SECRET)
        m.atomic(m.CONF, json.dumps(current)); before = m.CONF.read_bytes()
        m.repair(current)
        self.assertEqual(m.CONF.read_bytes(), before)
        self.assertNotIn('astroneer-playit.service', m.UNIT.read_text())
