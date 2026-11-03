#!/usr/bin/env python3
"""Astroneer operations. Configuration is data, never executable shell."""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time

from runtime import memory_info

VERSION = '0.3.0'
PENDING = ('playit-pending', 'network-pending')
BASE = Path('/var/lib/astroneer')
CONF = Path('/etc/astroneer/config.json')
BACK = Path('/var/backups/astroneer')
LOCK = Path('/run/lock/astroneer.lock')
NAME = 'astroneer-game'
LOGDIR = Path('/var/log/astroneer')
UNIT = Path('/etc/systemd/system/astroneer.service')
LOG_LIMIT = 2 * 1024 * 1024
IMAGE = r'barumel/docker-astroneer-server@sha256:[a-f0-9]{64}'
PLAYIT = r'ghcr.io/playit-cloud/playit-agent@sha256:[a-f0-9]{64}'


def run(args, check=True):
    # Captured errors are deliberately not printed: upstream may echo secrets.
    p = subprocess.run([str(a) for a in args], capture_output=True, text=True)
    if check and p.returncode:
        raise RuntimeError(f'{args[0]} failed (exit {p.returncode}); inspect locally as root')
    return p


def atomic(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb' if isinstance(data, bytes) else 'w') as f:
            os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def validate(c):
    if not isinstance(c, dict):
        raise ValueError('Configuration must be a JSON object')
    expected = {'image', 'name', 'owner', 'password', 'port', 'public_ip', 'mode',
                'retention', 'autostart', 'playit_image', 'playit_secret', 'external_port'}
    if set(c) - {'network_preference'} != expected:
        raise ValueError('Configuration keys must match config/example.json')
    if not isinstance(c['image'], str) or not re.fullmatch(IMAGE, c['image']):
        raise ValueError('A reviewed barumel image sha256 digest is required')
    for k in ('name', 'owner', 'password'):
        if not isinstance(c[k], str) or not 1 <= len(c[k]) <= 128 or any(ord(x) < 32 for x in c[k]) or any(x in c[k] for x in '\n\r="\\'):
            raise ValueError(f'{k}: use 1–128 printable characters without quotes, equals or backslash')
    for k, low, high in [('port', 1024, 65535), ('external_port', 1024, 65535), ('retention', 1, 365)]:
        if type(c[k]) is not int or not low <= c[k] <= high:
            raise ValueError(f'{k}: expected integer {low}..{high}')
    if any(not isinstance(c[k], str) for k in ('public_ip', 'playit_image', 'playit_secret')):
        raise ValueError('Endpoint and optional local-agent fields must be strings')
    if type(c['autostart']) is not bool or c['mode'] not in ('direct', 'playit', 'playit-external', 'lan', 'playit-pending', 'network-pending'):
        raise ValueError('Invalid autostart or networking mode')
    if 'network_preference' in c and c['network_preference'] not in ('playit-local', 'playit-existing', 'direct', 'lan'):
        raise ValueError('Invalid pending network preference')
    if c['mode'] in PENDING:
        if c['public_ip'] or c['playit_image'] or c['playit_secret'] or c['external_port'] != c['port']:
            raise ValueError('Pending network setup must have no public endpoint/identity yet')
        return c
    address = ipaddress.IPv4Address(c['public_ip'])
    if address.is_loopback or address.is_multicast or address.is_unspecified or address.is_reserved or address.is_link_local:
        raise ValueError('Advertised endpoint must be an assigned unicast IPv4, not loopback/link-local/reserved')
    if c['external_port'] != c['port']:
        raise ValueError('Different advertised and local ports are not supported by this installer')
    if c['mode'] != 'lan' and not ipaddress.ip_address(c['public_ip']).is_global:
        raise ValueError('Direct/Playit mode requires a globally routable assigned IPv4')
    if c['mode'] == 'playit':
        if not re.fullmatch(PLAYIT, c['playit_image']) or c['playit_secret'] and not re.fullmatch(r'[A-Za-z0-9_-]{8,512}', c['playit_secret']):
            raise ValueError('Playit requires a reviewed image digest and agent secret')
    elif c['playit_image'] or c['playit_secret']:
        raise ValueError('Playit fields must be empty when disabled')
    return c


def load():
    return validate(json.loads(CONF.read_text()))


def env(c):
    keys = {'ASTRO_SERVER_NAME': c['name'], 'ASTRO_SERVER_OWNER_NAME': c['owner'],
            'ASTRO_SERVER_PASSWORD': c['password'], 'ASTRO_SERVER_PORT': c['port'],
            'ASTRO_SERVER_PUBLIC_IP': c['public_ip'], 'ASTRO_SERVER_DOMAIN_NAME': '',
            'ASTRO_SERVER_DISABLE_ENCRYPTION': 'false'}
    return ''.join(f'{k}={v}\n' for k, v in keys.items())


def active():
    return run(['systemctl', 'is-active', '--quiet', 'astroneer'], False).returncode == 0


@contextlib.contextmanager
def stopped():
    was = active()
    if was:
        run(['systemctl', 'stop', 'astroneer'])
    try:
        yield
    finally:
        if was:
            run(['systemctl', 'start', 'astroneer'])


def unit(image):
    # The LXC's network namespace avoids Docker NAT and makes ss checks useful.
    return f'''[Unit]
Description=Astroneer dedicated server (Proton)
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=3
[Service]
Type=simple
ExecStartPre=/usr/bin/python3 /usr/local/lib/astroneer/manage.py prepare
ExecStartPre=/usr/bin/python3 /usr/local/lib/astroneer/manage.py prestart
ExecStart=/usr/bin/docker run --attach stdout --name {NAME} --user steam --network host --cap-drop ALL --security-opt no-new-privileges --env-file /etc/astroneer/server.env --mount type=bind,src=/var/lib/astroneer/game,dst=/astroneer --mount type=bind,src=/var/lib/astroneer/steamcmd,dst=/steamcmd --mount type=bind,src=/var/lib/astroneer/upstream-backup,dst=/backup --mount type=bind,src=/usr/local/lib/astroneer/entrypoint-observer.sh,dst=/run/astroneer-observer.sh,readonly --mount type=bind,src=/usr/local/lib/astroneer/runtime.py,dst=/run/astroneer-runtime.py,readonly --log-driver json-file --log-opt max-size=10m --log-opt max-file=3 --entrypoint /bin/bash {image} /run/astroneer-observer.sh
ExecStop=/usr/bin/docker stop --time 120 {NAME}
TimeoutStopSec=150
ExecStartPost=/usr/bin/python3 /usr/local/lib/astroneer/manage.py wait-ready
TimeoutStartSec=900
ExecStopPost=/usr/bin/python3 /usr/local/lib/astroneer/manage.py finish
Restart=no
StandardOutput=null
StandardError=journal
SyslogIdentifier=astroneer
[Install]
WantedBy=multi-user.target
'''


def apply(c):
    atomic(CONF.parent / 'server.env', env(c))
    atomic(UNIT, unit(c['image']), 0o644)
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable' if c['autostart'] and c['mode'] not in PENDING else 'disable', 'astroneer'])
    atomic(CONF, json.dumps(c, indent=2) + '\n')


def redact(text, c):
    # Redact keyed values first: a short configured secret could itself occur
    # inside a key name (e.g. "secret" in SECRET_KEY).
    text = re.sub(r"(?im)(authorization[\"']?\s*[=:]\s*)[^\n]*", r'\1[REDACTED]', text)
    text = re.sub(r"(?i)((?:password|serverpassword|secret_key|access_token|token)[\"']?\s*[=:]\s*)(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s,;]+)", r'\1[REDACTED]', text)
    known = [c.get('password'), c.get('playit_secret')]
    try:
        known.extend(line.split('=', 1)[1] for line in (CONF.parent/'server.env').read_text().splitlines() if line.startswith('ASTRO_SERVER_PASSWORD='))
    except OSError:
        pass
    try:
        known.append((CONF.parent/'playit-secret').read_text().strip())
    except OSError:
        pass
    for value in known:
        if value:
            text = text.replace(value, '[REDACTED]')
    # Strip terminal escape/control sequences from untrusted application output.
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    return ''.join(ch for ch in text if ch in '\n\t' or ord(ch) >= 32 and ord(ch) != 127)


def capture_logs(tail=200):
    """Read via Docker's API, bounded in memory and time, keeping the newest bytes."""
    args = ['docker', 'logs', '--timestamps', '--tail', str(tail), NAME]
    data = bytearray(); truncated = False
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as proc:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + 20
            try:
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError('docker logs timed out; inspect Docker service health')
                    for key, _ in selector.select(min(remaining, 1)):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            data.extend(chunk)
                            if len(data) > LOG_LIMIT:
                                del data[:-LOG_LIMIT]; truncated = True
                code = proc.wait(timeout=max(0.1, deadline - time.monotonic()))
            except (Exception, KeyboardInterrupt):
                proc.kill(); proc.wait()
                raise
    prefix = '[older log bytes truncated]\n' if truncated else ''
    return subprocess.CompletedProcess(args, code, prefix + data.decode('utf-8', errors='replace'), '')


def state_info():
    try:
        p = run(['docker', 'inspect', '--format', '{{json .State}}', NAME], False)
    except OSError:
        return None
    if p.returncode:
        return None
    try:
        state = json.loads(p.stdout)
        if not isinstance(state, dict):
            return None
        return {k: state.get(k) for k in ('Status', 'Running', 'ExitCode', 'OOMKilled', 'Error', 'StartedAt', 'FinishedAt')}
    except ValueError:
        return None


def failure_marker(text):
    matches = re.findall(r'ASTRONEER_STARTUP_FAILURE status=([0-9]+) source=([A-Za-z0-9_.-]+) line=([0-9]+) stage=([a-z-]+)', text)
    if not matches:
        return None
    code, source, line, stage = matches[-1]
    return {'exit_code': int(code), 'source': source, 'line': int(line), 'stage': stage}


def prepare(c):
    run(['docker', 'info'])
    # Check existence without confusing a Docker outage with a missing container.
    found = run(['docker', 'ps', '-a', '--filter', f'name=^/{NAME}$', '--format', '{{.Names}}']).stdout.splitlines()
    if NAME not in found:
        return
    state = state_info()
    if state is None or state.get('Running'):
        raise ValueError('Refusing to replace a running or uninspectable game container; stop it and inspect first')
    logs = capture_logs(5000)
    # Preserve recent evidence before Docker removes the stopped container. Never
    # remove volumes, initialization markers, manifests or save/config files.
    LOGDIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(LOGDIR, 0o700)
    for index in (2, 1):
        previous = LOGDIR / f'previous-{index}.log'
        if previous.exists():
            os.replace(previous, LOGDIR / f'previous-{index + 1}.log')
    text = json.dumps(state) + '\n' + (logs.stdout if logs.returncode == 0 else 'Docker logs unavailable (old logging driver may be none).\n')
    atomic(LOGDIR / 'previous-1.log', redact(text, c))
    run(['docker', 'rm', NAME])


def show_logs(c, tail, previous=False, game=False):
    if previous:
        path = LOGDIR / 'previous-1.log'
        if not path.exists():
            raise ValueError('No archived previous startup; existing log-driver none output cannot be recovered')
        text = path.read_text(errors='replace')
    elif game:
        paths = sorted((BASE / 'game/Astro/Saved/Logs').glob('*.log'), key=lambda p: p.stat().st_mtime)
        if not paths:
            raise ValueError('No game log file yet; use astroneer logs for SteamCMD/Proton startup output')
        with paths[-1].open('rb') as f:
            f.seek(max(0, paths[-1].stat().st_size - LOG_LIMIT))
            text = f.read(LOG_LIMIT).decode('utf-8', errors='replace')
    else:
        output = capture_logs(tail)
        if output.returncode:
            raise RuntimeError('Cannot read container logs. Old log-driver none output is unrecoverable; apply repair and start once. Check journalctl -u astroneer for Docker launch errors.')
        text = output.stdout
    print(redact('\n'.join(text.splitlines()[-tail:]), c))


def report(c):
    state = state_info()
    if state is None:
        print('No inspectable game container. Check journalctl -u astroneer for pre-start/Docker launch errors.', file=sys.stderr)
        return
    summary = {k: state[k] for k in ('Status', 'ExitCode', 'OOMKilled', 'Error')}
    print(redact('Astroneer container state: ' + json.dumps(summary), c), file=sys.stderr)
    if state.get('ExitCode'):
        output = capture_logs(5000)
        marker = failure_marker(output.stdout) if output.returncode == 0 else None
        print('Startup failure: ' + json.dumps(marker) if marker else 'Exact failed command not yet identified; inspect astroneer logs and astroneer logs --game.', file=sys.stderr)
        print('No automatic retry. Review logs before astroneer start. UDP/registration are not proven.', file=sys.stderr)


def finish(c):
    # ExecStop is not guaranteed when ExecStartPost fails. Do not leave the Docker
    # workload running after a failed readiness check or lost foreground client.
    state = state_info()
    if state and state.get('Running'):
        run(['docker', 'stop', '--time', '30', NAME], False)
    report(c)


def runtime_probe(port):
    try:
        output = run(['docker', 'exec', NAME, 'python3', '/run/astroneer-runtime.py', 'probe', '--port', str(port)], False)
        data = json.loads(output.stdout) if output.returncode == 0 else None
        if not isinstance(data, dict) or type(data.get('ready')) is not bool:
            return None
        if data['ready'] and (not data.get('game_pids') or data.get('game_owned_udp') is not True or data.get('udp_port') != port):
            return None
        return data
    except (ValueError, OSError):
        return None


def runtime_audit():
    try:
        output = run(['docker', 'exec', NAME, 'python3', '/run/astroneer-runtime.py', 'audit'], False)
        data = json.loads(output.stdout) if output.returncode == 0 else None
        return data if isinstance(data, dict) else None
    except (ValueError, OSError):
        return None


def wait_ready(c, timeout=840, interval=2):
    deadline = time.monotonic() + timeout
    consecutive = 0
    while time.monotonic() < deadline:
        state = state_info()
        if state is not None and not state.get('Running'):
            raise RuntimeError('Game container exited before UDP readiness; inspect astroneer logs for child exit status and Wine diagnostics')
        evidence = runtime_probe(c['port']) if state and state.get('Running') else None
        consecutive = consecutive + 1 if evidence and evidence.get('ready') else 0
        if consecutive >= 2:
            print(f'Local readiness: actual game owns IPv4 UDP {c["port"]}; backend registration remains unknown')
            return
        time.sleep(interval)
    run(['docker', 'stop', '--time', '30', NAME], False)
    raise RuntimeError('Readiness deadline exceeded: actual game did not retain ownership of the configured UDP socket; inspect astroneer logs. Docker health is not readiness.')


def repair(c, start=False):
    if not (CONF.parent / 'server.env').is_file():
        raise ValueError('Existing server.env is missing; inspect configuration manually before repair')
    run(['systemctl', 'stop', 'astroneer'])
    # Only replace our unit. Keep private JSON/env, provenance, INIs and data intact.
    atomic(UNIT, unit(c['image']), 0o644)
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'reset-failed', 'astroneer'])
    print('Service repaired and stopped. Configuration, image pin and data preserved. Logs become available after one new start.')
    if start:
        run(['systemctl', 'start', 'astroneer'])
        print('One startup attempt completed local UDP readiness checks; verify backend/client connectivity separately.')


def pull_verified(image, proof=None):
    # Docker verifies downloaded layers; independently inspect platform and the
    # selected immutable reference before running any publisher entrypoint.
    run(['docker', 'pull', '--platform', 'linux/amd64', image])
    info = json.loads(run(['docker', 'image', 'inspect', image]).stdout)
    if not isinstance(info, list) or len(info) != 1:
        raise ValueError('Docker did not return exactly one image')
    info = info[0]
    if info.get('Os') != 'linux' or info.get('Architecture') != 'amd64':
        raise ValueError('Pulled image must be linux/amd64')
    names = info.get('RepoDigests') or []
    if image not in names and 'docker.io/' + image not in names:
        raise ValueError('Pulled image RepoDigests does not contain the approved immutable reference')
    if proof is not None:
        if proof.get('image') != image or proof.get('approved') is not True or proof.get('platform') != 'linux/amd64' or proof.get('config_digest') != info.get('Id'):
            raise ValueError('Pulled image does not match approved provenance/config digest')
    return {'image': image, 'config_digest': info.get('Id'), 'platform': 'linux/amd64',
            'verified_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}


def read_provenance(path, c):
    if path is None:
        return None
    proof = json.loads(Path(path).read_text())
    if not isinstance(proof, dict):
        raise ValueError('Image provenance must be a JSON object')
    for key, image in [('game', c['image']), ('playit', c['playit_image'])]:
        record = proof.get(key)
        if not image and record is None:
            continue
        if not isinstance(record, dict) or record.get('image') != image or record.get('approved') is not True or record.get('platform') != 'linux/amd64' or not re.fullmatch(r'sha256:[a-f0-9]{64}', str(record.get('config_digest', ''))):
            raise ValueError('Missing or mismatched approved image provenance')
    return proof


def provision(path, provenance_path=None):
    c = validate(json.loads(Path(path).read_text()))
    proof = read_provenance(provenance_path, c)
    if CONF.exists():
        if load() != c:
            raise ValueError('Already installed with different configuration; use astroneer config FILE')
        repair(c, start=c['mode'] not in PENDING)
        print('Existing installation reconciled; persistent data preserved')
        return
    inspected = pull_verified(c['image'], proof['game'] if proof else None)
    # Inspect numeric UID from the pinned image rather than assuming 1000.
    uid = run(['docker', 'run', '--rm', '--entrypoint', 'id', c['image'], '-u', 'steam']).stdout.strip()
    gid = run(['docker', 'run', '--rm', '--entrypoint', 'id', c['image'], '-g', 'steam']).stdout.strip()
    if not uid.isdigit() or not gid.isdigit() or int(uid) == 0:
        raise ValueError('Image must contain a non-root steam user')
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in ('game', 'steamcmd', 'upstream-backup'):
        d = BASE / name
        d.mkdir(exist_ok=True)
        os.chown(d, int(uid), int(gid))
        os.chmod(d, 0o700)
    # Empty bind mounts hide image content: seed SteamCMD, but never overwrite it.
    if not (BASE / 'steamcmd/steamcmd.sh').exists():
        run(['docker', 'run', '--rm', '--user', '0', '--entrypoint', '/bin/cp',
             '--mount', f'type=bind,src={BASE}/steamcmd,dst=/seed', c['image'], '-a', '/steamcmd/.', '/seed/'])
    saved = BASE / 'game/Astro/Saved'
    saved.mkdir(parents=True, exist_ok=True)
    for d in (BASE / 'game/Astro', saved):
        os.chown(d, int(uid), int(gid))
    if c['mode'] == 'playit':
        setup_playit(c, proof['playit'] if proof else None)
    atomic(CONF.parent / 'image-provenance.json', json.dumps({'selection': proof, 'installed_game': inspected}, indent=2) + '\n')
    apply(c)  # Commit marker only after dependencies and storage succeed.
    if c['mode'] in PENDING:
        print('Network setup INCOMPLETE. Runtime installed; game stopped and boot activation deferred. Run astroneer network configure.')
        return
    run(['systemctl', 'start', 'astroneer'])
    print('Service started. Run astroneer diagnostics; registration and client join remain unverified.')


def setup_playit(c, proof=None):
    import playit
    playit.install_agent(sys.modules[__name__], c['playit_image'], c['playit_secret'], proof)


def digest(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def check_archive(p):
    if digest(p) != p.with_suffix(p.suffix + '.sha256').read_text().strip():
        raise ValueError('Backup checksum mismatch')
    with tarfile.open(p, 'r:gz') as t:
        members = t.getmembers()
        for m in members:
            parts = Path(m.name).parts
            if not parts or parts[0] not in ('saved', 'config.json') or '..' in parts or m.name.startswith('/') or not (m.isfile() or m.isdir()):
                raise ValueError('Unsafe backup member (links and devices are forbidden)')
        if 'config.json' not in t.getnames() or 'saved' not in t.getnames():
            raise ValueError('Incomplete backup')
        for m in members:
            if m.isfile():
                with t.extractfile(m) as f:
                    while f.read(1024 * 1024):
                        pass


def backup(c, prune=True):
    BACK.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    p = BACK / f'astroneer-{stamp}.tar.gz'
    tmp = p.with_suffix('.partial')
    try:
        with tarfile.open(tmp, 'w:gz', dereference=False) as t:
            t.add(BASE / 'game/Astro/Saved', arcname='saved')
            t.add(CONF, arcname='config.json')
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
        atomic(p.with_suffix('.gz.sha256'), digest(p) + '\n')
        check_archive(p)
    except Exception:
        p.unlink(missing_ok=True)
        p.with_suffix('.gz.sha256').unlink(missing_ok=True)
        raise
    finally:
        tmp.unlink(missing_ok=True)
    # Only remove verified complete older backups after successful creation.
    old = sorted(BACK.glob('astroneer-*.tar.gz'))[:-c['retention']] if prune else []
    for item in old:
        check_archive(item)
        item.unlink()
        item.with_suffix('.gz.sha256').unlink()
    print(p)
    return p


def restore(p, c, keep_network=False):
    p = p.resolve()
    check_archive(p)
    with tempfile.TemporaryDirectory(dir=BASE.parent) as tmp:
        stage = Path(tmp)
        with tarfile.open(p, 'r:gz') as t:
            # Already rejects links/devices/traversal; numeric ownership is needed.
            t.extractall(stage, filter='fully_trusted')
        if not (stage / 'saved').is_dir():
            raise ValueError('Saved backup member must be a directory')
        saved = validate(json.loads((stage / 'config.json').read_text()))
        if keep_network:
            saved.update({key: c[key] for key in ('mode', 'playit_image', 'playit_secret', 'public_ip', 'port', 'external_port')})
            validate(saved)
            print('Restore retains the current managed network endpoint and Playit identity.')
        elif any(saved[k] != c[k] for k in ('mode', 'playit_image', 'playit_secret', 'public_ip', 'port', 'external_port')):
            raise ValueError('Restore cannot change network mode, endpoint or agent identity; use --keep-network to restore saves while preserving the current endpoint')
        run(['docker', 'pull', saved['image']])
        backup(c)  # Preserve pre-restore state before any rename.
        target = BASE / 'game/Astro/Saved'
        rollback = BASE / 'game/Astro/Saved.rollback'
        if rollback.exists():
            raise ValueError('Previous interrupted restore exists; inspect Saved.rollback')
        os.replace(target, rollback)
        try:
            os.replace(stage / 'saved', target)
            apply(saved)
        except Exception:
            if target.exists():
                shutil.rmtree(target)
            os.replace(rollback, target)
            apply(c)
            raise
        shutil.rmtree(rollback)


def diagnostics(c, as_json):
    rows = []
    def probe(args):
        try:
            return run(args, False)
        except OSError:
            return subprocess.CompletedProcess(args, 127, '', '')
    def add(name, state, detail):
        rows.append({'check': name, 'state': state, 'detail': detail})
    if c['mode'] in PENDING:
        add('network_setup', 'WARN', 'Network setup incomplete; game intentionally stopped until connection settings are configured')
    add('os', 'PASS' if Path('/etc/debian_version').exists() else 'WARN', 'Requires Debian 12/13 amd64')
    p = probe(['systemd-detect-virt', '--container'])
    add('container', 'PASS' if p.stdout.strip() == 'lxc' else 'WARN', p.stdout.strip() or 'not detected')
    add('resources', 'PASS' if (os.cpu_count() or 0) >= 2 and shutil.disk_usage(BASE).free > 2**30 else 'WARN', f'CPUs={os.cpu_count()}, free_disk_bytes={shutil.disk_usage(BASE).free}; see /proc/meminfo and cgroup memory.max for LXC limit')
    memory = memory_info()
    effective = memory['effective_limit_bytes']
    add('memory', 'PASS' if effective is not None and effective >= 4 * 1024**3 else 'WARN', json.dumps(memory))
    for label, args in [('runtime', ['docker', 'info']), ('service', ['systemctl', 'is-active', '--quiet', 'astroneer'])]:
        add(label, 'PASS' if probe(args).returncode == 0 else 'WARN' if label == 'service' and c['mode'] in PENDING else 'FAIL', 'Command health check')
    p = probe(['docker', 'inspect', '--format', '{{.State.Running}}', NAME])
    add('process', 'PASS' if p.stdout.strip() == 'true' else 'WARN' if c['mode'] in PENDING else 'FAIL', 'Container running is not backend registration')
    game = probe(['docker', 'top', NAME, '-eo', 'comm'])
    found_game = any('AstroServer' in line for line in game.stdout.splitlines())
    add('game_process', 'PASS' if found_game else 'WARN' if c['mode'] in PENDING else 'FAIL', 'AstroServer executable seen in process names' if found_game else 'Game executable not observed; initialization or failure possible')
    p = probe(['ss', '-H', '-lun'])
    listening = any(re.search(rf':{c["port"]}\s', line) for line in p.stdout.splitlines())
    add('udp_listener', 'PASS' if listening else 'WARN', f'Local UDP {c["port"]}; socket ownership not proven')
    evidence = runtime_probe(c['port'])
    add('game_readiness', 'PASS' if evidence and evidence.get('ready') else 'WARN' if c['mode'] in PENDING else 'FAIL', 'Actual game PID owns configured IPv4 UDP socket; backend unknown' if evidence and evidence.get('ready') else 'Game process/socket ownership not established; Docker/systemd health alone is insufficient')
    audit = runtime_audit()
    add('proton_runtime', 'WARN' if not audit or audit.get('machine_id') != 'valid' or any(d.get('missing') for d in audit.get('dependencies', [])) else 'PASS', json.dumps(audit) if audit else 'Runtime audit unavailable; read ASTRONEER_RUNTIME_AUDIT in startup logs. Missing machine-id causality remains unknown.')
    ips = probe(['hostname', '-I']).stdout.strip()
    add('endpoint', 'WARN' if c['mode'] in ('lan', 'playit-pending', 'network-pending') else 'PASS', 'Public endpoint unassigned; run astroneer network configure' if c['mode'] in PENDING else f'local={ips}; configured={c["public_ip"]}:{c["external_port"]}; assignment must be checked in provider/router')
    if c['mode'] == 'playit':
        p = probe(['systemctl', 'is-active', '--quiet', 'astroneer-playit'])
        add('playit_service', 'PASS' if p.returncode == 0 else 'FAIL', 'Agent process only')
    import network
    rows.extend(network.status(sys.modules[__name__], testing=False))
    for label in ('tunnel_connected', 'public_udp_reachability', 'backend_registration', 'client_join'):
        add(label, 'WARN', 'Unknown; requires independent provider/backend/client verification')
    try:
        socket.getaddrinfo('astroneer.space', 443)
        add('dns', 'PASS', 'astroneer.space resolves')
    except OSError:
        add('dns', 'FAIL', 'Resolution failed')
    try:
        with socket.create_connection(('astroneer.space', 443), timeout=5):
            add('outbound_tcp', 'PASS', 'TCP 443 only; does not test game UDP')
    except OSError:
        add('outbound_tcp', 'WARN', 'Could not connect')
    safe = all(x.exists() and (x.stat().st_mode & 0o077) == 0 for x in (CONF, CONF.parent / 'server.env', BASE))
    try:
        actual = dict(line.split('=', 1) for line in (CONF.parent / 'server.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
        expected = dict(line.split('=', 1) for line in env(c).splitlines())
        matches = all(actual.get(k) == v for k, v in expected.items())
    except OSError:
        matches = False
    add('environment_file', 'PASS' if matches else 'WARN', 'Managed environment matches private configuration' if matches else 'Missing or different server.env; review locally without sharing credentials. Repair preserves it.')
    add('permissions', 'PASS' if safe else 'FAIL', 'Root configuration and data parent must be private')
    state = state_info()
    add('docker_exit', 'WARN' if state is None else 'FAIL' if state.get('OOMKilled') or not state.get('Running') and state.get('ExitCode') else 'PASS',
        'No inspectable container; check journalctl -u astroneer' if state is None else f"status={state.get('Status')}, exit={state.get('ExitCode')}, OOMKilled={state.get('OOMKilled')}")
    installed = (BASE / 'game/Astro/Binaries/Win64/AstroServer-Win64-Shipping.exe').is_file()
    initialized = (BASE / 'game/initialized').is_file()
    add('startup_files', 'PASS' if installed else 'WARN', f'game executable present={installed}; upstream initialized marker={initialized}; neither proves a running game')
    cfgdir = BASE / 'game/Astro/Saved/Config/WindowsServer'
    add('game_configuration', 'PASS' if all((cfgdir / n).is_file() for n in ('Engine.ini', 'AstroServerSettings.ini')) else 'WARN', 'INI presence only; contents and configuration success require startup logs')
    try:
        output = capture_logs(5000)
        marker = failure_marker(output.stdout) if output.returncode == 0 else None
        add('startup_failure', 'FAIL' if marker else 'WARN', json.dumps(marker) if marker else 'Exact failed command unknown; astroneer logs and journalctl -u astroneer')
    except (OSError, RuntimeError):
        add('startup_failure', 'WARN', 'Logs unavailable; repair old logging driver or check Docker health')
    count = error_count()
    has_logs = any((BASE / 'game/Astro/Saved/Logs').glob('*.log'))
    add('recent_errors', 'WARN' if count or not has_logs else 'PASS', f'{count} error/fatal lines in the tail of the newest game log; content suppressed')
    print(json.dumps(rows, indent=2) if as_json else '\n'.join(f'{r["state"]:4} {r["check"]}: {r["detail"]}' for r in rows))
    return 1 if any(r['state'] == 'FAIL' for r in rows) else 0


def error_count():
    logs = sorted((BASE / 'game/Astro/Saved/Logs').glob('*.log'), key=lambda p: p.stat().st_mtime)
    if not logs:
        return 0
    with logs[-1].open('rb') as f:
        f.seek(max(0, logs[-1].stat().st_size - 65536))
        return sum(bool(re.search(rb'error|fatal', line, re.I)) for line in f.read().splitlines())


def parser():
    p = argparse.ArgumentParser(prog='astroneer')
    s = p.add_subparsers(dest='cmd', required=True, metavar='COMMAND')
    for n in ('start', 'stop', 'restart', 'status', 'backup', 'version', 'help'):
        s.add_parser(n, help=f'{n.capitalize()} the managed server' if n in ('start', 'stop', 'restart') else n.capitalize())
    d = s.add_parser('logs', help='Read redacted startup/game logs'); d.add_argument('--tail', type=int, default=200)
    group = d.add_mutually_exclusive_group(); group.add_argument('--previous', action='store_true'); group.add_argument('--game', action='store_true')
    import network
    network.add_parser(s)
    d = s.add_parser('playit', help='Set up, configure, test or disable Playit networking')
    actions = d.add_subparsers(dest='playit_action', required=True)
    setup = actions.add_parser('setup'); setup.add_argument('--relink', action='store_true')
    for action in ('status', 'test'):
        action_parser = actions.add_parser(action); action_parser.add_argument('--json', action='store_true')
    actions.add_parser('disable')
    action = actions.add_parser('configure'); action.add_argument('--rollback', action='store_true')
    action.add_argument('--public-ip'); action.add_argument('--port', type=int); action.add_argument('--local-port', type=int); action.add_argument('--yes', action='store_true')
    action.add_argument('--agent', choices=('local', 'existing')); action.add_argument('--destination-ip'); action.add_argument('--start', action='store_true')
    d = s.add_parser('repair', help='Repair the unit while preserving configuration/data'); d.add_argument('--start', action='store_true')
    for internal in ('prepare', 'report', 'finish', 'wait-ready'):
        s.add_parser(internal)
    d = s.add_parser('diagnostics', help='Check process, UDP and startup evidence'); d.add_argument('--json', action='store_true')
    d = s.add_parser('update', help='Update to an explicitly pinned image'); d.add_argument('--image', required=True)
    d = s.add_parser('restore', help='Restore a selected backup'); d.add_argument('backup', type=Path); d.add_argument('--yes', action='store_true'); d.add_argument('--keep-network', action='store_true')
    d = s.add_parser('config', help='View or apply private configuration'); d.add_argument('file', nargs='?', type=Path)
    s.add_parser('prestart')
    d = s.add_parser('provision'); d.add_argument('file', type=Path); d.add_argument('--provenance', type=Path)
    return p


def main(argv=None):
    p = parser(); a = p.parse_args(argv)
    if a.cmd == 'version':
        print(VERSION); return 0
    if a.cmd == 'help':
        p.print_help(); return 0
    if os.geteuid() != 0:
        raise ValueError('Run astroneer as root')
    os.umask(0o077)
    if a.cmd == 'network' and a.network_action in ('status', 'test'):
        import network
        if a.network_action == 'test':
            network.observe(sys.modules[__name__], a.observe)
        rows = network.status(sys.modules[__name__])
        print(json.dumps(rows, indent=2) if a.json else '\n'.join(f'{r["state"]:4} {r["check"]}: {r["detail"]}' for r in rows))
        return 1 if any(r['state'] == 'FAIL' for r in rows) else 0
    if a.cmd == 'diagnostics':
        return diagnostics(load(), a.json)
    if a.cmd == 'playit' and a.playit_action in ('status', 'test'):
        import playit
        rows = playit.status(sys.modules[__name__], testing=a.playit_action == 'test')
        print(json.dumps(rows, indent=2) if a.json else '\n'.join(f'{r["state"]:4} {r["check"]}: {r["detail"]}' for r in rows))
        return 1 if any(r['state'] == 'FAIL' for r in rows) else 0
    if a.cmd == 'logs':
        if not 1 <= a.tail <= 5000:
            raise ValueError('--tail must be between 1 and 5000')
        show_logs(load(), a.tail, a.previous, a.game)
        return 0
    if a.cmd in ('prepare', 'report', 'finish', 'wait-ready'):
        {'prepare': prepare, 'report': report, 'finish': finish, 'wait-ready': wait_ready}[a.cmd](load())
        return 0
    if a.cmd == 'prestart':
        run(['docker', 'info'])
        state = run(['docker', 'inspect', '--format', '{{.State.Running}}', NAME], False)
        if state.stdout.strip() == 'true':
            raise ValueError('Refusing pre-start backup while a game container is running')
        # Called synchronously by systemd with the game stopped. A separate hook
        # avoids deadlocking the management lock held by start/update/restore.
        c = load()
        if c['mode'] in PENDING:
            raise ValueError('Network setup incomplete; run astroneer network configure before starting the game')
        import network
        network.guard(sys.modules[__name__])
        if (CONF.parent/'network-transaction.json').exists() or (LOCK.parent/'astroneer-network-maintenance').exists():
            backup(c, prune=False)
        else:
            backup(c)
        return 0
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if a.cmd == 'provision':
            provision(a.file, a.provenance); return 0
        c = load()
        if a.cmd == 'network':
            import network
            if a.network_action == 'configure':
                network.configure(sys.modules[__name__], a)
            else:
                network.rollback(sys.modules[__name__], a.snapshot, a.yes)
            return 0
        if a.cmd == 'playit':
            import playit
            if a.playit_action == 'configure' and a.rollback:
                if a.public_ip is not None or a.port is not None or a.local_port is not None or a.yes:
                    raise ValueError('--rollback cannot be combined with endpoint or approval options')
                playit.rollback(sys.modules[__name__])
            elif a.playit_action == 'setup':
                playit.setup(sys.modules[__name__], a.relink)
            elif a.playit_action == 'configure':
                playit.configure(sys.modules[__name__], a.public_ip, a.port, a.local_port, a.yes, a.agent, a.destination_ip, a.start)
            else:
                getattr(playit, a.playit_action)(sys.modules[__name__])
            return 0
        if a.cmd in ('start', 'stop', 'restart', 'status'):
            if a.cmd in ('start', 'restart') and (CONF.parent/'network-transaction.json').exists():
                raise ValueError('Incomplete network transaction; run astroneer playit configure --rollback first')
            result = run(['systemctl', a.cmd, 'astroneer'], False)
            print(redact(result.stdout.strip(), c) or f'{a.cmd}: exit {result.returncode}')
            if a.cmd in ('start', 'restart'):
                print('Local game/UDP startup checks completed; verify diagnostics and backend/client connectivity separately. Automatic retries are disabled.' if result.returncode == 0 else 'Start request failed; inspect journalctl -u astroneer and astroneer logs before retrying.')
            return result.returncode
        if a.cmd == 'repair':
            repair(c, a.start); return 0
        if a.cmd == 'config' and not a.file:
            print(json.dumps({k: ('[REDACTED]' if k in ('password', 'playit_secret') else v) for k, v in c.items()}, indent=2)); return 0
        if a.cmd == 'restore' and not a.yes:
            if not sys.stdin.isatty() or input('Restore overwrites game/config data. Type RESTORE: ') != 'RESTORE':
                raise ValueError('Restore cancelled; automation requires --yes')
        if a.cmd == 'config':
            new = validate(json.loads(a.file.read_text()))
            if any(new[k] != c[k] for k in ('image', 'playit_image', 'playit_secret', 'mode', 'public_ip', 'port', 'external_port')):
                raise ValueError('Use update for game image; use astroneer network configure for networking')
            with stopped():
                backup(c); apply(new)
        elif a.cmd == 'backup':
            with stopped():
                backup(c)
        elif a.cmd == 'restore':
            with stopped():
                restore(a.backup, c, a.keep_network)
        elif a.cmd == 'update':
            new = dict(c, image=a.image); validate(new)
            run(['docker', 'pull', new['image']])
            with stopped():
                backup(c)
                try:
                    apply(new)
                except Exception:
                    apply(c)
                    raise
            print('Pinned image updated; game content updates on next start. Run diagnostics and test a client. Restore the pre-update backup for game-content rollback.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError, tarfile.TarError) as e:
        print(f'astroneer: {e}', file=sys.stderr)
        sys.exit(1)
