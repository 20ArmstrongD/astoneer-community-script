"""Offline Playit IPC and migration fixtures; no account, Docker or guest access."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import manage as m
import playit as p
import network as n
from test_project import config, result

AGENT_IMAGE = 'ghcr.io/playit-cloud/playit-agent@sha256:'+'b'*64
AGENT_SECRET = 'agent-secret-example-1234'


def connected(port=12345, local=None):
    return {'phase': 'running', 'lifecycle': 'running', 'ipc_available': True,
            'tunnels': [{'display_address': f'8.8.8.8:{port}', 'destination': f'127.0.0.1:{local or port}', 'is_disabled': False}]}


def dns(*args, **kwargs):
    return [(2, 2, 17, '', ('8.8.8.8', 12345))]


class IPC(unittest.TestCase):
    def test_protocol_and_private_fields_not_exposed(self):
        messages = [
            {'message_kind': 'hello', 'data': {'protocol': {'ipc_version': 2}}},
            {'message_kind': 'response', 'data': {'ipc_version': 2, 'request_id': 1, 'response': {'type': 'status', 'data': {'phase': 'running'}}}},
            {'message_kind': 'response', 'data': {'ipc_version': 2, 'request_id': 2, 'response': {'type': 'state', 'data': {'state': 'running', 'data': {'login_link': 'private-token', 'tunnels': connected()['tunnels']}}}}},
        ]
        with patch.object(p.socket, 'socket') as client:
            conn = client.return_value.__enter__.return_value
            conn.recv.side_effect = [json.dumps(x).encode()+b'\n' for x in messages]
            state = p.state()
        self.assertEqual(state['phase'], 'running')
        self.assertNotIn('private-token', json.dumps(state))
        sent = [json.loads(c.args[0]) for c in conn.sendall.call_args_list]
        self.assertEqual([x['request']['type'] for x in sent], ['get_status', 'get_state'])

    def test_wrong_protocol_fails_closed(self):
        with patch.object(p.socket, 'socket') as client:
            conn = client.return_value.__enter__.return_value
            conn.recv.return_value = b'{"message_kind":"hello","data":{"protocol":{"ipc_version":1}}}\n'
            state = p.state()
        self.assertFalse(state['ipc_available'])
        self.assertEqual(state['phase'], 'unknown')

    def test_unavailable_socket_not_connected(self):
        with patch.object(p, 'ipc', side_effect=OSError()):
            self.assertFalse(p.state()['ipc_available'])


class Endpoint(unittest.TestCase):
    def test_invalid_or_missing_endpoint(self):
        for ip in ('', '192.168.1.150', 'hostname.playit.gg', '127.0.0.1', '224.0.0.1'):
            with self.subTest(ip=ip), self.assertRaises(ValueError):
                p.endpoint(ip, 12345, 12345)

    def test_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, '127.0.0.1:12345'):
            p.endpoint('8.8.8.8', 12345, 8777)

    def test_hostname_resolves_to_ipv4_not_advertised_as_hostname(self):
        tunnel = dict(connected()['tunnels'][0], display_address='sample.playit.gg:12345')
        with patch.object(p.socket, 'getaddrinfo', side_effect=dns):
            self.assertEqual(p.tunnel_endpoint(tunnel), (['8.8.8.8'], 12345))

    def test_origin_mismatch_is_not_silently_mapped(self):
        with patch.object(p.socket, 'getaddrinfo', side_effect=dns), self.assertRaises(ValueError):
            p.tunnel_endpoint(connected(local=8777)['tunnels'][0])

    def test_environment_only_network_fields_change(self):
        original = m.env(config())+'PROTON_CUSTOM=keep\n# keep comment\n'
        changed = p.network_env(original, '8.8.4.4', 23456)
        self.assertIn('ASTRO_SERVER_PASSWORD=secret', changed)
        self.assertIn('PROTON_CUSTOM=keep', changed)
        self.assertIn('# keep comment', changed)
        self.assertIn('ASTRO_SERVER_PUBLIC_IP=8.8.4.4', changed)
        self.assertIn('ASTRO_SERVER_PORT=23456', changed)


class Migration(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.conf = root/'etc/config.json'; self.unit = root/'units/astroneer.service'
        self.base = root/'data'; self.back = root/'backups'
        for key, value in [('CONF', self.conf), ('UNIT', self.unit), ('BASE', self.base), ('BACK', self.back), ('LOCK', root/'run/astroneer.lock')]:
            patcher = patch.object(m, key, value); patcher.start(); self.addCleanup(patcher.stop)
        self.old = dict(config(), mode='lan', public_ip='192.168.1.150', owner='ExistingOwner')
        m.atomic(self.conf, json.dumps(self.old)); m.atomic(self.conf.parent/'server.env', m.env(self.old)+'CUSTOM_RUNTIME=keep\n')
        m.atomic(self.unit, 'old managed unit\n')
        self.ini = self.base/'game/Astro/Saved/Config/WindowsServer/Engine.ini'; self.ini.parent.mkdir(parents=True)
        self.ini.write_bytes(b'original-\xff-ini')
        (self.base/'persistent.savegame').write_bytes(b'keep save')
        m.atomic(p.paths(m)[0], json.dumps({'image': AGENT_IMAGE, 'configured': False}))
        m.atomic(p.paths(m)[1], AGENT_SECRET)
        self.output = contextlib.redirect_stdout(io.StringIO()); self.output.__enter__(); self.addCleanup(self.output.__exit__, None, None, None)

    def test_lan_to_playit_preserves_identity_image_data_and_backups(self):
        with patch.object(m, 'run', return_value=result()) as run, patch.object(m, 'backup') as backup, patch.object(p, 'port_free'), patch.object(p, 'stable'):
            n.apply_plan(m, n.plan(m, 'playit-local', '8.8.8.8', 12345))
        c = m.load()
        for key in ('password', 'owner', 'name', 'image', 'autostart'):
            self.assertEqual(c[key], self.old[key])
        self.assertEqual((c['mode'], c['public_ip'], c['port']), ('playit', '8.8.8.8', 12345))
        self.assertIn('CUSTOM_RUNTIME=keep', (self.conf.parent/'server.env').read_text())
        self.assertEqual((self.base/'persistent.savegame').read_bytes(), b'keep save')
        self.assertEqual(self.ini.read_bytes(), b'original-\xff-ini')
        backup.assert_called_once_with(self.old, prune=False)
        calls = [call.args[0] for call in run.call_args_list]
        self.assertFalse(any(p.SERVICE in call for call in calls))
        self.assertNotIn('Requires=astroneer-playit.service', self.unit.read_text())
        self.assertEqual(c['playit_secret'], '')
        self.assertFalse(p.paths(m)[2].exists())

    def test_identical_configuration_does_not_restart(self):
        with patch.object(m, 'run', return_value=result()), patch.object(m, 'backup'), patch.object(p, 'port_free'), patch.object(p, 'stable'):
            n.apply_plan(m, n.plan(m, 'playit-local', '8.8.8.8', 12345))
        with patch.object(m, 'run') as run, patch.object(m, 'backup') as backup:
            n.apply_plan(m, n.plan(m, 'playit-local', '8.8.8.8', 12345))
        self.assertFalse(any('start' in call.args[0] or 'stop' in call.args[0] for call in run.call_args_list)); backup.assert_not_called()

    def test_configuration_write_failure_restores_exact_ini_bytes(self):
        before = self.conf.read_bytes(), (self.conf.parent/'server.env').read_bytes(), self.unit.read_bytes(), self.ini.read_bytes()
        original_apply = m.apply
        def fail(values):
            original_apply(values)
            self.ini.write_bytes(b'new generated ini')
            raise RuntimeError('WRITE FAILURE')
        with patch.object(m, 'run', return_value=result()), patch.object(m, 'backup'), patch.object(p, 'port_free'), patch.object(m, 'apply', side_effect=fail), self.assertRaisesRegex(RuntimeError, 'WRITE FAILURE'):
            n.apply_plan(m, n.plan(m, 'playit-local', '8.8.8.8', 12345))
        after = self.conf.read_bytes(), (self.conf.parent/'server.env').read_bytes(), self.unit.read_bytes(), self.ini.read_bytes()
        self.assertEqual(before, after)
        self.assertEqual((self.base/'persistent.savegame').read_bytes(), b'keep save')
        self.assertFalse(p.paths(m)[2].exists())

    def test_occupied_port_rolls_back_without_starting(self):
        with patch.object(m, 'run', return_value=result()) as run, patch.object(p, 'port_free', side_effect=ValueError('occupied')), self.assertRaises(ValueError):
            n.apply_plan(m, n.plan(m, 'playit-local', '8.8.8.8', 12345))
        self.assertEqual(m.load(), self.old)
        self.assertNotIn(['systemctl', 'start', 'astroneer'], [c.args[0] for c in run.call_args_list])

    def test_interrupted_transaction_requires_explicit_recovery(self):
        p.snapshot(m, self.old)
        with self.assertRaisesRegex(ValueError, 'Interrupted network change'):
            n.apply_plan(m, n.plan(m, 'playit-local', '8.8.8.8', 12345))
        with patch.object(m, 'run', return_value=result()):
            p.rollback(m)
        self.assertEqual(m.load(), self.old)

    def test_no_tunnel_configured_is_actionable(self):
        with patch.object(p, 'state', return_value=dict(connected(), tunnels=[])), patch.object(p, 'setup'), self.assertRaisesRegex(ValueError, 'No enabled tunnel'):
            p.configure(m, yes=True)

    def test_configure_checks_actual_assigned_port_and_confirmation(self):
        with patch.object(p, 'state', return_value=connected()), patch.object(p.socket, 'getaddrinfo', side_effect=dns), patch.object(n, 'apply_plan') as migrate, patch.object(p, 'setup'):
            p.configure(m, yes=True)
        self.assertEqual(migrate.call_args.args[1]['public_ip'], '8.8.8.8')
        self.assertEqual(migrate.call_args.args[1]['port'], 12345)

    def test_explicit_bad_port_mapping_refused(self):
        with patch.object(p, 'state', return_value=connected()), self.assertRaisesRegex(ValueError, 'Port mismatch'):
            p.configure(m, '8.8.8.8', 12345, 8777, True)

    def test_agent_failure_does_not_gate_game_observation(self):
        with patch.object(p, 'state') as agent, patch.object(m, 'runtime_probe', return_value={'ready': True}), patch.object(p.time, 'monotonic', side_effect=[0, 0.1, 2]), patch.object(p.time, 'sleep'):
            p.stable(m, seconds=1)
        agent.assert_not_called()
        with patch.object(m, 'runtime_probe', return_value={'ready': False}), self.assertRaisesRegex(RuntimeError, 'GAME FAILURE'):
            p.stable(m, seconds=1)

    def test_status_separates_unverified_transport_from_local_game(self):
        m.atomic(self.conf, json.dumps(dict(self.old, mode='playit', public_ip='8.8.8.8', playit_image=AGENT_IMAGE, playit_secret=AGENT_SECRET)))
        with patch.object(p, 'state', return_value=connected()), patch.object(p.socket, 'getaddrinfo', side_effect=dns), patch.object(m, 'run', return_value=result()), patch.object(m, 'runtime_probe', return_value={'ready': False, 'game_pids': []}):
            rows = {row['check']: row for row in p.status(m, True)}
        self.assertEqual(rows['playit_agent']['state'], 'PASS')
        self.assertEqual(rows['game_process']['state'], 'FAIL')
        self.assertEqual(rows['tunnel_transport_connected']['state'], 'WARN')
        self.assertEqual(rows['steam_client_connected']['state'], 'WARN')
        self.assertNotIn(AGENT_SECRET, json.dumps(rows))

    def test_agent_install_uses_secret_file_not_secret_argv(self):
        def run(args, check=True):
            return result('--secret-path --socket-path') if '--help' in args else result()
        with patch.object(m, 'pull_verified') as pull, patch.object(m, 'run', side_effect=run):
            p.install_agent(m, AGENT_IMAGE, AGENT_SECRET)
        pull.assert_called_once_with(AGENT_IMAGE, None)
        text = (self.unit.parent/f'{p.SERVICE}.service').read_text()
        self.assertIn('--secret-path /run/secrets/playit', text)
        self.assertIn('--network host --cap-drop ALL', text)
        self.assertNotIn(AGENT_SECRET, text)
        self.assertNotIn('--secret=', text)

    def test_existing_setup_reuses_agent_identity(self):
        before = p.paths(m)[1].read_bytes()
        with patch.object(m, 'run', return_value=result()), patch.object(p, 'resolve') as resolve, patch.object(p.getpass, 'getpass') as password:
            p.setup(m)
        resolve.assert_not_called(); password.assert_not_called()
        self.assertEqual(p.paths(m)[1].read_bytes(), before)

    def test_agent_without_required_options_rejected(self):
        with patch.object(m, 'pull_verified'), patch.object(m, 'run', return_value=result('old options')), self.assertRaisesRegex(ValueError, 'lacks required'):
            p.install_agent(m, AGENT_IMAGE, 'new-agent-secret')
        self.assertEqual(p.paths(m)[1].read_text(), AGENT_SECRET)

    def test_disable_preserves_endpoint_credentials_and_saves(self):
        c = dict(self.old, mode='playit', public_ip='8.8.8.8', playit_image=AGENT_IMAGE, playit_secret=AGENT_SECRET)
        m.atomic(self.conf, json.dumps(c)); before = self.conf.read_bytes()
        with patch.object(m, 'run', return_value=result()):
            p.disable(m)
        self.assertEqual(self.conf.read_bytes(), before)
        self.assertTrue((self.conf.parent/'playit-disabled').exists())
        p.guard(m)  # The agent pause does not gate game startup.

    def test_fresh_setup_requires_approval_and_hides_agent_secret(self):
        p.paths(m)[0].unlink(); p.paths(m)[1].unlink()
        record = {'image': AGENT_IMAGE, 'platform': 'linux/amd64'}
        out = io.StringIO()
        with patch.object(p.sys.stdin, 'isatty', return_value=True), patch.object(p, 'resolve', return_value=record), patch('builtins.input', side_effect=['latest', 'APPROVE']), patch.object(p.getpass, 'getpass', return_value=AGENT_SECRET), patch.object(p, 'install_agent') as install, contextlib.redirect_stdout(out):
            p.setup(m)
        install.assert_called_once_with(m, AGENT_IMAGE, AGENT_SECRET, record)
        self.assertTrue(record['approved'])
        self.assertNotIn(AGENT_SECRET, out.getvalue())
        self.assertEqual(m.load(), self.old)

    def test_fresh_setup_noninteractive_refused_before_secret(self):
        p.paths(m)[0].unlink(); p.paths(m)[1].unlink()
        with patch.object(p.sys.stdin, 'isatty', return_value=False), patch.object(p.getpass, 'getpass') as password, self.assertRaisesRegex(ValueError, 'interactive terminal'):
            p.setup(m)
        password.assert_not_called()

    def test_cancel_during_configuration_rolls_back(self):
        with patch.object(m, 'run', return_value=result()), patch.object(m, 'backup'), patch.object(p, 'port_free'), patch.object(m, 'apply', side_effect=KeyboardInterrupt()), self.assertRaises(KeyboardInterrupt):
            n.apply_plan(m, n.plan(m, 'playit-local', '8.8.8.8', 12345))
        self.assertEqual(m.load(), self.old)
        self.assertFalse(p.paths(m)[2].exists())

    def test_rollback_retains_new_game_ini_without_deleting_data(self):
        self.ini.unlink()
        p.snapshot(m, self.old)
        self.ini.write_bytes(b'new generated ini')
        with patch.object(m, 'run', return_value=result()):
            p.rollback(m)
        self.assertEqual(self.ini.read_bytes(), b'new generated ini')

    def test_dead_transaction_owner_blocks_start(self):
        m.atomic(p.paths(m)[2], json.dumps({'pid': 999999999, 'start_ticks': '0', 'snapshot': 'unused'}))
        with self.assertRaisesRegex(ValueError, 'Interrupted network change'):
            p.guard(m)

    def test_failed_rollback_retains_recovery_journal(self):
        with patch.object(m, 'run', return_value=result()), patch.object(m, 'backup'), patch.object(p, 'port_free'), patch.object(m, 'apply', side_effect=RuntimeError('WRITE FAILURE')), patch.object(p, 'rollback', side_effect=OSError('disk full')), self.assertRaisesRegex(RuntimeError, 'rollback is incomplete'):
            n.apply_plan(m, n.plan(m, 'playit-local', '8.8.8.8', 12345))
        self.assertTrue(p.paths(m)[2].exists())

    def test_manual_dashboard_endpoint_keeps_independent_match_unknown(self):
        current = dict(self.old, mode='playit', public_ip='8.8.8.8', port=12345, external_port=12345, playit_image=AGENT_IMAGE, playit_secret=AGENT_SECRET)
        m.atomic(self.conf, json.dumps(current))
        meta = {'image': AGENT_IMAGE, 'operator_endpoint': {'public_ip': '8.8.8.8', 'port': 12345, 'origin': '127.0.0.1:12345', 'custom_udp_confirmed': True}}
        m.atomic(p.paths(m)[0], json.dumps(meta))
        agent = connected(); agent['tunnels'][0]['display_address'] = 'unparsed-provider-display'
        with patch.object(p, 'state', return_value=agent), patch.object(m, 'run', return_value=result()):
            rows = {row['check']: row for row in p.status(m)}
            p.guard(m)
        self.assertEqual(rows['endpoint_configuration']['state'], 'PASS')
        self.assertEqual(rows['endpoint_match']['state'], 'WARN')

    def test_stale_operator_confirmation_cannot_override_live_conflict(self):
        current = dict(self.old, mode='playit', public_ip='8.8.4.4', port=12345, external_port=12345, playit_image=AGENT_IMAGE, playit_secret=AGENT_SECRET)
        m.atomic(self.conf, json.dumps(current))
        m.atomic(p.paths(m)[0], json.dumps({'image': AGENT_IMAGE, 'operator_endpoint': {'public_ip': '8.8.4.4', 'port': 12345, 'origin': '127.0.0.1:12345', 'custom_udp_confirmed': True}}))
        with patch.object(p, 'state', return_value=connected()), patch.object(p.socket, 'getaddrinfo', side_effect=dns), patch.object(m, 'run', return_value=result()):
            p.guard(m)
            rows = {row['check']: row for row in p.status(m)}
        self.assertEqual(rows['endpoint_configuration']['state'], 'WARN')

    def test_stabilization_checks_game_agent_and_endpoint(self):
        current = dict(self.old, mode='playit', public_ip='8.8.8.8', port=12345, external_port=12345, playit_image=AGENT_IMAGE, playit_secret=AGENT_SECRET)
        m.atomic(self.conf, json.dumps(current))
        with patch.object(p.time, 'monotonic', side_effect=[0, 1, 99]), patch.object(p.time, 'sleep'), patch.object(p, 'state', return_value=connected()), patch.object(p.socket, 'getaddrinfo', side_effect=dns), patch.object(m, 'run', return_value=result()), patch.object(m, 'runtime_probe', return_value={'ready': True}):
            p.stable(m)

    def test_pending_mode_never_enables_game_at_boot(self):
        c = dict(self.old, mode='playit-pending', public_ip='')
        with patch.object(m, 'run', return_value=result()) as run:
            m.apply(c)
        self.assertIn(['systemctl', 'disable', 'astroneer'], [c.args[0] for c in run.call_args_list])
