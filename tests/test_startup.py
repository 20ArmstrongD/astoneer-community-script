"""Execute the Bash observer against temporary fixtures; no Docker/CT needed."""
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
sys.path.insert(0, str(ROOT / 'scripts'))
import manage as m
from test_project import config, result


class ObserverTests(unittest.TestCase):
    def execute(self, body, code=0, output=''):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root/'entrypoint.sh'; fixture.write_text('set -e\n' + body)
            # These are external command stubs, so failures are attributed to the
            # calling upstream command, rather than a Bash function body.
            for name in ('bash', 'node'):
                stub = root/name
                stub.write_text('#!/bin/sh\nprintf "%s\\n" "$FIXTURE_OUTPUT"\nexit "$FIXTURE_CODE"\n')
                stub.chmod(0o755)
            environ = dict(os.environ, PATH=str(root)+':'+os.environ['PATH'], FIXTURE_CODE=str(code), FIXTURE_OUTPUT=output)
            return subprocess.run(['/bin/bash', str(ROOT/'scripts/entrypoint-observer.sh'), str(fixture)], capture_output=True, text=True, env=environ)

    def test_interrupted_steam_self_update_stops_before_game_update(self):
        p = self.execute('bash /steamcmd/steamcmd.sh +quit\necho SHOULD_NOT_RUN\n', 42,
                         'Timed out waiting for update to start, bailing\nSuccess! App fully installed')
        self.assertEqual(p.returncode, 42)
        self.assertNotIn('SHOULD_NOT_RUN', p.stdout)
        marker = m.failure_marker(p.stderr)
        self.assertEqual(marker['stage'], 'steamcmd-self-update')
        self.assertEqual(marker['exit_code'], 42)
        self.assertEqual(marker['line'], 2)
        self.assertIn('Success! App fully installed', p.stdout)

    def test_game_update_failure_stops_before_configuration(self):
        p = self.execute('bash /steamcmd/steamcmd.sh +runscript /tmp/install.txt\necho CONFIG_NOT_REACHED\n', 7)
        self.assertEqual(p.returncode, 7)
        self.assertNotIn('CONFIG_NOT_REACHED', p.stdout)
        self.assertEqual(m.failure_marker(p.stderr)['stage'], 'steamcmd-game-update')

    def test_timeout_text_with_zero_exit_is_not_a_failure(self):
        p = self.execute('bash /steamcmd/steamcmd.sh +quit\necho CONTINUED\n', 0, 'Timed out waiting for update to start, bailing')
        self.assertEqual(p.returncode, 0)
        self.assertIn('CONTINUED', p.stdout)
        self.assertIsNone(m.failure_marker(p.stderr))

    def test_initconfig_failure_is_identified(self):
        p = self.execute('node /srv/src/initConfig.js\necho SERVER_NOT_STARTED\n', 1, 'Error: missing WindowsServer Engine.ini')
        self.assertEqual(p.returncode, 1)
        self.assertNotIn('SERVER_NOT_STARTED', p.stdout)
        self.assertEqual(m.failure_marker(p.stderr)['stage'], 'configuration')
        self.assertIn('missing WindowsServer', p.stdout)

    def test_wait_n_failure_not_hidden(self):
        p = self.execute('(exit 19) &\nwait -n\necho CLEANUP_NOT_REACHED\n')
        # macOS ships Bash 3.2 (no wait -n); Debian/image Bash supports it.
        support = subprocess.run(['/bin/bash', '-c', 'wait -n'], capture_output=True).returncode != 2
        self.assertEqual(p.returncode, 19 if support else 2)
        self.assertEqual(m.failure_marker(p.stderr)['stage'], 'runtime-child-exit')
        self.assertNotIn('CLEANUP_NOT_REACHED', p.stdout)

    def test_unknown_command_not_dumped(self):
        p = self.execute('test sensitive-password = wrong\n')
        self.assertEqual(p.returncode, 1)
        self.assertNotIn('sensitive-password', p.stderr)
        self.assertEqual(m.failure_marker(p.stderr)['stage'], 'unclassified')

    def test_explicit_exit_status_preserved(self):
        p = self.execute('exit 31\n')
        self.assertEqual(p.returncode, 31)
        self.assertIn('ASTRONEER_ENTRYPOINT_EXIT status=31', p.stderr)
        self.assertIsNone(m.failure_marker(p.stderr))


class StartupOperations(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.base = self.root/'data'; self.base.mkdir()
        self.conf = self.root/'etc/config.json'
        m.atomic(self.conf, json.dumps(config()))
        m.atomic(self.conf.parent/'server.env', m.env(config()))
        self.unit = self.root/'astroneer.service'
        self.logs = self.root/'logs'
        for key, value in [('BASE', self.base), ('CONF', self.conf), ('UNIT', self.unit), ('LOGDIR', self.logs)]:
            p = patch.object(m, key, value); p.start(); self.addCleanup(p.stop)

    def test_repair_repeat_preserves_config_env_data_pin(self):
        sentinel = self.base/'game.savegame'; sentinel.write_bytes(b'keep')
        before = self.conf.read_bytes(), (self.conf.parent/'server.env').read_bytes()
        with patch.object(m, 'run', return_value=result()) as run, contextlib.redirect_stdout(io.StringIO()):
            for _ in range(2):
                m.repair(config())
        self.assertEqual(before, (self.conf.read_bytes(), (self.conf.parent/'server.env').read_bytes()))
        self.assertEqual(sentinel.read_bytes(), b'keep')
        self.assertIn(config()['image'], self.unit.read_text())
        self.assertFalse(any(call.args[0][0] == 'docker' for call in run.call_args_list))
        self.assertFalse(any('start' in call.args[0] for call in run.call_args_list))

    def test_repair_opt_in_start_only_once(self):
        with patch.object(m, 'run', return_value=result()) as run, contextlib.redirect_stdout(io.StringIO()):
            m.repair(config(), True)
        self.assertEqual(sum(call.args[0] == ['systemctl', 'start', 'astroneer'] for call in run.call_args_list), 1)

    def test_logs_show_actual_output_and_redact(self):
        out = io.StringIO()
        with patch.object(m, 'capture_logs', return_value=result('SteamCMD update failed\nPassword=secret\nnode initConfig.js failed\n')), contextlib.redirect_stdout(out):
            m.show_logs(config(), 200)
        self.assertIn('SteamCMD update failed', out.getvalue())
        self.assertIn('node initConfig.js failed', out.getvalue())
        self.assertNotIn('secret', out.getvalue())
        self.assertIn('[REDACTED]', out.getvalue())

    def test_failed_container_evidence_saved_before_remove(self):
        state = {'Status': 'exited', 'Running': False, 'ExitCode': 1, 'OOMKilled': False}
        def run(args, check=True):
            if args[:3] == ['docker', 'ps', '-a']:
                return result(m.NAME+'\n')
            if args == ['docker', 'rm', m.NAME]:
                self.assertTrue((self.logs/'previous-1.log').exists())
            return result()
        with patch.object(m, 'run', side_effect=run), patch.object(m, 'state_info', return_value=state), patch.object(m, 'capture_logs', return_value=result('SteamCMD interrupted, password=secret')):
            for _ in range(5):
                m.prepare(config())
        self.assertEqual(len(list(self.logs.glob('*.log'))), 3)
        self.assertNotIn('secret', (self.logs/'previous-1.log').read_text())
        self.assertIn('SteamCMD interrupted', (self.logs/'previous-1.log').read_text())
        self.assertEqual((self.logs/'previous-1.log').stat().st_mode & 0o777, 0o600)

    def test_prepare_never_removes_running_game(self):
        with patch.object(m, 'run', return_value=result(m.NAME+'\n')) as run, patch.object(m, 'state_info', return_value={'Running': True}), self.assertRaises(ValueError):
            m.prepare(config())
        self.assertNotIn(['docker', 'rm', m.NAME], [c.args[0] for c in run.call_args_list])

    def test_archive_failure_prevents_container_removal(self):
        with patch.object(m, 'run', return_value=result(m.NAME+'\n')) as run, patch.object(m, 'state_info', return_value={'Running': False}), patch.object(m, 'capture_logs', side_effect=RuntimeError('timeout')), self.assertRaises(RuntimeError):
            m.prepare(config())
        self.assertNotIn(['docker', 'rm', m.NAME], [c.args[0] for c in run.call_args_list])

    def test_legacy_no_logs_reports_unrecoverable(self):
        with patch.object(m, 'capture_logs', return_value=result(code=1)), self.assertRaisesRegex(RuntimeError, 'unrecoverable'):
            m.show_logs(config(), 200)

    def test_report_identifies_config_failure_without_guessing_steamcmd(self):
        out = io.StringIO()
        state = dict(Status='exited', ExitCode=1, OOMKilled=False, Error='')
        logs = 'ASTRONEER_STARTUP_FAILURE status=1 source=entrypoint.sh line=45 stage=configuration'
        with patch.object(m, 'state_info', return_value=state), patch.object(m, 'capture_logs', return_value=result(logs)), contextlib.redirect_stderr(out):
            m.report(config())
        self.assertIn('configuration', out.getvalue())
        self.assertIn('No automatic retry', out.getvalue())
        self.assertNotIn('steamcmd-game-update', out.getvalue())

    def test_unit_keeps_security_and_forensics(self):
        text = m.unit(config()['image'])
        for fragment in ('--user steam', '--cap-drop ALL', '--security-opt no-new-privileges', '--log-driver json-file', 'max-size=10m', 'max-file=3', 'Restart=no', 'manage.py prepare', 'manage.py report', '--attach stdout', '--entrypoint /bin/bash'):
            self.assertIn(fragment, text)
        self.assertNotIn('--rm ', text)
        self.assertNotIn('--privileged', text)
        self.assertNotIn('ExecStartPre=-/usr/bin/docker rm', text)

    def test_capture_logs_keeps_bounded_tail_and_both_streams(self):
        popen = subprocess.Popen
        def external_fixture(args, **kwargs):
            return popen([sys.executable, '-c', 'import sys; sys.stdout.write("x"*10000); sys.stdout.flush(); sys.stderr.write("LATEST_ERROR")'], **kwargs)
        with patch.object(m.subprocess, 'Popen', side_effect=external_fixture), patch.object(m, 'LOG_LIMIT', 1024):
            output = m.capture_logs()
        self.assertEqual(output.returncode, 0)
        self.assertIn('LATEST_ERROR', output.stdout)
        self.assertLess(len(output.stdout), 1100)
        self.assertIn('truncated', output.stdout)

    def test_capture_logs_timeout_kills_child(self):
        popen = subprocess.Popen; children = []
        def external_fixture(args, **kwargs):
            proc = popen([sys.executable, '-c', 'import time; time.sleep(5)'], **kwargs)
            children.append(proc)
            return proc
        with patch.object(m.subprocess, 'Popen', side_effect=external_fixture), patch.object(m.time, 'monotonic', side_effect=[0, 25]), self.assertRaisesRegex(RuntimeError, 'timed out'):
            m.capture_logs()
        self.assertIsNotNone(children[0].poll())

    def test_redacts_json_tokens_and_authorization(self):
        text = 'SECRET_KEY=unknown-value\n"token": "other-value"\nAuthorization: Bearer sensitive-bearer\nServerPassword=secret\n'
        value = m.redact(text, config())
        for credential in ('unknown-value', 'other-value', 'sensitive-bearer', 'secret'):
            self.assertNotIn(credential, value)

    def test_invalid_log_tail_refused_before_access(self):
        with patch.object(m.os, 'geteuid', return_value=0), patch.object(m, 'show_logs') as logs:
            with self.assertRaisesRegex(ValueError, '--tail'):
                m.main(['logs', '--tail', '0'])
        logs.assert_not_called()

    def test_path_entry_installed_for_minimal_guest_path(self):
        for name in ('astroneer-install.sh', 'repair.sh'):
            text = (ROOT/'install'/name).read_text()
            self.assertIn('ln -sfn /usr/local/bin/astroneer /usr/bin/astroneer', text)
        self.assertIn('exec /usr/bin/python3', (ROOT/'scripts/astroneer').read_text())
