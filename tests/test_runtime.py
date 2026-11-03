import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import runtime as r
import manage as m
from test_project import config, result


class Evidence(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.proc = self.root/'proc'; self.proc.mkdir()

    def game(self, comm='AstroServer-Win', state='S'):
        p = self.proc/'123'; p.mkdir(); (p/'fd').mkdir()
        (p/'comm').write_text(comm)
        (p/'stat').write_text(f'123 (name) {state} 1 0')
        (p/'fd/7').symlink_to('socket:[9001]')

    def udp(self, inode='9001', port=8777):
        (self.proc/'net').mkdir()
        (self.proc/'net/udp').write_text(f'header\n0: 00000000:{port:04X} 00000000:0000 07 0:0 0:0 0 1000 0 {inode}\n')

    def test_only_game_owned_udp_is_ready(self):
        self.game(); self.udp()
        self.assertTrue(r.probe(8777, self.proc)['ready'])
        self.assertFalse(r.probe(9999, self.proc)['ready'])

    def test_other_process_port_not_ready(self):
        self.game(); self.udp('9999')
        evidence = r.probe(8777, self.proc)
        self.assertTrue(evidence['udp_bound']); self.assertFalse(evidence['ready'])

    def test_helper_only_is_not_game(self):
        self.game(comm='node'); self.udp()
        self.assertFalse(r.probe(8777, self.proc)['ready'])

    def test_zombie_is_not_game(self):
        self.game(state='Z'); self.udp()
        self.assertEqual(r.probe(8777, self.proc)['game_pids'], [])

    def mount(self, kind='cgroup2', mountroot='/', membership='/init.scope'):
        (self.proc/'self').mkdir(); (self.proc/'1').mkdir()
        tail = 'cgroup2 cgroup rw' if kind == 'cgroup2' else 'cgroup cgroup rw,memory'
        (self.proc/'self/mountinfo').write_text(f'1 0 0:1 {mountroot} /sys/fs/cgroup rw - {tail}\n')
        value = ('0::' if kind == 'cgroup2' else '3:memory:') + membership + '\n'
        (self.proc/'self/cgroup').write_text(value)
        (self.proc/'1/cgroup').write_text('0::/\n' if kind == 'cgroup2' else '3:memory:/\n')
        cgroup = self.root/'sys/fs/cgroup'; cgroup.mkdir(parents=True)
        return cgroup

    def test_cgroup_v2_root_limit_applies_to_nested_service(self):
        c = self.mount(); (c/'init.scope').mkdir()
        (c/'memory.max').write_text(str(4*1024**3)); (c/'init.scope/memory.max').write_text('max')
        evidence = r.memory_info(self.proc, self.root)
        self.assertEqual(evidence['effective_limit_bytes'], 4*1024**3)
        self.assertEqual(evidence['init_limit_bytes'], 4*1024**3)
        self.assertFalse(evidence['host_memtotal_used'])

    def test_tighter_service_limit_wins(self):
        c = self.mount(); (c/'init.scope').mkdir()
        (c/'memory.max').write_text(str(4*1024**3)); (c/'init.scope/memory.max').write_text(str(2*1024**3))
        evidence = r.memory_info(self.proc, self.root)
        self.assertEqual(evidence['effective_limit_bytes'], 2*1024**3)
        self.assertEqual(evidence['init_limit_bytes'], 4*1024**3)

    def test_host_relative_mount_root(self):
        c = self.mount(mountroot='/lxc/105', membership='/lxc/105/init.scope')
        (c/'init.scope').mkdir(); (c/'memory.max').write_text(str(4*1024**3))
        self.assertEqual(r.memory_info(self.proc, self.root)['effective_limit_bytes'], 4*1024**3)

    def test_cgroup_v1_unlimited_leaf_finite_parent(self):
        c = self.mount(kind='cgroup'); (c/'init.scope').mkdir()
        (c/'memory.limit_in_bytes').write_text(str(4*1024**3))
        (c/'init.scope/memory.limit_in_bytes').write_text(str(2**63-4096))
        self.assertEqual(r.memory_info(self.proc, self.root)['effective_limit_bytes'], 4*1024**3)

    def test_unlimited_visible_cgroup_is_unknown_not_host_ram(self):
        c = self.mount(); (c/'memory.max').write_text('max')
        self.assertIsNone(r.memory_info(self.proc, self.root)['effective_limit_bytes'])


class Watchdog(unittest.TestCase):
    def test_actual_game_disappearance_fails(self):
        with patch.object(r, 'probe', side_effect=[{'game_pids': [1], 'ready': False}, {'game_pids': [], 'ready': False}]), patch.object(r.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(r.watch(8777), 70)

    def test_never_listening_times_out(self):
        with patch.object(r, 'probe', return_value={'game_pids': [1], 'ready': False}), patch.object(r.time, 'monotonic', side_effect=[0, 301]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(r.watch(8777), 71)

    def test_socket_loss_after_ready_fails(self):
        states = [{'game_pids': [1], 'ready': True}]*2 + [{'game_pids': [1], 'ready': False}]*5
        out = io.StringIO()
        with patch.object(r, 'probe', side_effect=states), patch.object(r.time, 'sleep'), contextlib.redirect_stdout(out):
            self.assertEqual(r.watch(8777), 72)
        self.assertIn('ASTRONEER_LOCAL_READY', out.getvalue())
        self.assertIn('udp-lost', out.getvalue())


class ChildSupervision(unittest.TestCase):
    def execute(self, body, monitor='import time; time.sleep(20)'):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp)/'entry.sh'; fixture.write_text('set -e\n'+body)
            helper = Path(tmp)/'monitor.py'; helper.write_text(monitor)
            return subprocess.run(['/bin/bash', str(ROOT/'scripts/entrypoint-observer.sh'), str(fixture), str(helper)], capture_output=True, text=True, timeout=8)

    def test_zero_game_exit_fails_even_when_helper_alive_and_already_exited(self):
        output = self.execute('(exit 0) & p1=$!\n/bin/sleep 20 & p2=$!\n/bin/sleep 0.1\nwait -n\necho FALSE_SUCCESS\n')
        self.assertEqual(output.returncode, 1)
        self.assertIn('ASTRONEER_CHILD_EXIT role=game-launcher status=0', output.stderr)
        self.assertNotIn('FALSE_SUCCESS', output.stdout)

    def test_nonzero_game_exit_preserved_without_parent_err_trap(self):
        output = self.execute('(exit 23) & p1=$!\n/bin/sleep 20 & p2=$!\nwait -n\n')
        self.assertEqual(output.returncode, 23)
        self.assertIn('role=game-launcher status=23', output.stderr)

    def test_helper_clean_exit_is_not_a_healthy_service(self):
        output = self.execute('/bin/sleep 20 & p1=$!\n(exit 0) & p2=$!\nwait -n\n')
        self.assertEqual(output.returncode, 1)
        self.assertIn('role=backup-helper status=0', output.stderr)

    def test_game_gone_while_launcher_stays_alive(self):
        output = self.execute('/bin/sleep 20 & p1=$!\n/bin/sleep 20 & p2=$!\nwait -n\n', 'import sys; print("game disappeared, status unknown", flush=True); sys.exit(70)')
        self.assertEqual(output.returncode, 70)
        self.assertIn('role=game-watchdog status=70', output.stderr)


class SystemdReadiness(unittest.TestCase):
    def test_healthy_docker_with_only_node_never_ready(self):
        with patch.object(m, 'state_info', return_value={'Running': True}), patch.object(m, 'runtime_probe', return_value={'ready': False}), patch.object(m.time, 'monotonic', side_effect=[0, 1, 1000]), patch.object(m.time, 'sleep'), patch.object(m, 'run', return_value=result()) as run:
            with self.assertRaisesRegex(RuntimeError, 'Readiness deadline'):
                m.wait_ready(config())
        run.assert_called_once_with(['docker', 'stop', '--time', '30', m.NAME], False)

    def test_requires_two_samples(self):
        with patch.object(m, 'state_info', return_value={'Running': True}), patch.object(m, 'runtime_probe', side_effect=[{'ready': False}, {'ready': True}, {'ready': True}]) as probe, patch.object(m.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            m.wait_ready(config())
        self.assertEqual(probe.call_count, 3)

    def test_exited_docker_cannot_pass_ready(self):
        with patch.object(m, 'state_info', return_value={'Running': False}), patch.object(m, 'runtime_probe') as probe, self.assertRaisesRegex(RuntimeError, 'exited before'):
            m.wait_ready(config())
        probe.assert_not_called()

    def test_docker_health_payload_is_not_game_readiness(self):
        payload = {'Status': 'healthy', 'Running': True}
        with patch.object(m, 'run', return_value=result(json.dumps(payload))):
            self.assertIsNone(m.runtime_probe(8777))

    def test_inconsistent_ready_payload_refused(self):
        payload = {'ready': True, 'game_pids': [], 'game_owned_udp': False, 'udp_port': 8777}
        with patch.object(m, 'run', return_value=result(json.dumps(payload))):
            self.assertIsNone(m.runtime_probe(8777))

    def test_failed_start_post_stops_leftover_container(self):
        with patch.object(m, 'state_info', return_value={'Running': True}), patch.object(m, 'run', return_value=result()) as run, patch.object(m, 'report'):
            m.finish(config())
        run.assert_called_once_with(['docker', 'stop', '--time', '30', m.NAME], False)
