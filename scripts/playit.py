"""In-place Playit migration using publisher images, IPC v2 and manual dashboard consent."""
import datetime
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sys
import time

from images import resolve

SERVICE = 'astroneer-playit'
NAME = 'astroneer-playit'


def paths(m):
    return m.CONF.parent / 'playit.json', m.CONF.parent / 'playit-secret', m.CONF.parent / 'network-transaction.json'


def agent_unit(image):
    return f'''[Unit]
Description=Playit agent for Astroneer
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
StartLimitIntervalSec=3600
StartLimitBurst=3
[Service]
RuntimeDirectory=astroneer-playit
RuntimeDirectoryMode=0700
ExecStartPre=-/usr/bin/docker rm {NAME}
ExecStart=/usr/bin/docker run --attach stdout --name {NAME} --network host --cap-drop ALL --security-opt no-new-privileges --log-driver json-file --log-opt max-size=10m --log-opt max-file=3 --mount type=bind,src=/etc/astroneer/playit-secret,dst=/run/secrets/playit,readonly --mount type=bind,src=/run/astroneer-playit,dst=/run/astroneer-playit --entrypoint /usr/local/bin/playitd {image} --secret-path /run/secrets/playit --socket-path /run/astroneer-playit/agent.sock --platform-docker
ExecStop=/usr/bin/docker stop --time 30 {NAME}
TimeoutStopSec=45
Restart=on-failure
RestartSec=30
StandardOutput=null
StandardError=journal
[Install]
WantedBy=multi-user.target
'''


def ipc(path='/run/astroneer-playit/agent.sock'):
    """Official newline-delimited IPC v2; never expose raw lifecycle/login/error data."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect(str(path))
        pending = bytearray(); deadline = time.monotonic() + 8
        def receive():
            while b'\n' not in pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError('Playit IPC deadline exceeded')
                client.settimeout(remaining)
                data = client.recv(min(65536, 1024*1024 + 1 - len(pending)))
                if not data or len(pending) + len(data) > 1024*1024:
                    raise ValueError('Playit IPC closed or response exceeded limit')
                pending.extend(data)
            line, _, rest = pending.partition(b'\n')
            pending[:] = rest
            return json.loads(line)
        hello = receive()
        if hello.get('message_kind') != 'hello' or hello.get('data', {}).get('protocol', {}).get('ipc_version') != 2:
            raise ValueError('Unsupported Playit IPC version; use a reviewed IPC v2 agent image')
        output = {}
        for request_id, (command, expected) in enumerate((('get_status', 'status'), ('get_state', 'state')), 1):
            client.sendall((json.dumps({'ipc_version': 2, 'request_id': request_id, 'request': {'type': command}})+'\n').encode())
            for _ in range(16):
                envelope = receive()
                if envelope.get('message_kind') != 'response':
                    continue
                reply = envelope.get('data', {})
                if reply.get('ipc_version') != 2 or reply.get('request_id') != request_id:
                    raise ValueError('Playit IPC response identity mismatch')
                response = reply.get('response', {})
                if response.get('type') != expected:
                    raise ValueError('Playit IPC request failed; inspect agent state privately')
                output[expected] = response.get('data', {})
                break
            else:
                raise ValueError('No matching Playit IPC response')
        return output


def state():
    try:
        value = ipc()
        phase = value['status'].get('phase', 'unknown')
        lifecycle = value['state'].get('state', 'unknown')
        allowed = ('waiting_for_secret', 'has_invalid_secret', 'disabled_over_limit', 'starting', 'running', 'stopping', 'error')
        phase = phase if phase in allowed else 'unknown'
        lifecycle = lifecycle if lifecycle in allowed else 'unknown'
        tunnels = value['state'].get('data', {}).get('tunnels', []) if lifecycle == 'running' else []
        enabled = [t for t in tunnels if t.get('is_disabled') is False]
        return {'phase': phase, 'lifecycle': lifecycle, 'tunnels': enabled, 'ipc_available': True}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {'phase': 'unknown', 'lifecycle': 'unknown', 'tunnels': [], 'ipc_available': False}


def endpoint(ip, port, local):
    try:
        address = ipaddress.IPv4Address(ip)
    except ValueError:
        raise ValueError('Enter the numerical assigned Playit IPv4 from the dashboard, not a hostname or claim URL') from None
    if not address.is_global or address.is_multicast or address.is_reserved:
        raise ValueError('Use the assigned globally routable Playit IPv4, never a LAN/ISP outbound address or hostname')
    if type(port) is not int or type(local) is not int or not 1024 <= port <= 65535:
        raise ValueError('UDP port must be an integer 1024..65535')
    if local != port:
        raise ValueError(f'Port mismatch: change Playit local origin to 127.0.0.1:{port}, then rerun configure; no game configuration changed')
    return str(address), port


def tunnel_endpoint(tunnel):
    public = tunnel.get('display_address', '')
    origin = tunnel.get('destination', '')
    display = re.fullmatch(r'([A-Za-z0-9.-]+):([0-9]+)', public)
    local = re.fullmatch(r'127\.0\.0\.1:([0-9]+)', origin)
    if not display or not local:
        raise ValueError('Tunnel must show host:port and origin 127.0.0.1:port. Set a Custom UDP tunnel with Proxy Protocol None in the dashboard')
    host, external = display[1], int(display[2])
    if not 1024 <= external <= 65535:
        raise ValueError('Playit external UDP port must be 1024..65535 under the non-root game policy')
    if int(local[1]) != external:
        raise ValueError(f'Port mismatch: set Playit local origin to 127.0.0.1:{external}')
    candidates = sorted({record[4][0] for record in socket.getaddrinfo(host, external, socket.AF_INET, socket.SOCK_DGRAM)})
    candidates = [ip for ip in candidates if ipaddress.ip_address(ip).is_global]
    if not candidates:
        raise ValueError('Playit endpoint has no globally routable IPv4')
    endpoint(candidates[0], external, int(local[1]))
    return candidates, external


def port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        try:
            listener.bind(('0.0.0.0', port))
        except OSError:
            raise ValueError(f'UDP {port} is occupied. Stop the conflicting service or choose another Playit allocation; do not use a mismatched port') from None


def load_meta(m):
    meta, _, _ = paths(m)
    if not meta.exists():
        raise ValueError('Playit is not installed/linked; run astroneer playit setup')
    value = json.loads(meta.read_text())
    if not isinstance(value, dict) or not re.fullmatch(m.PLAYIT, str(value.get('image', ''))):
        raise ValueError('Invalid Playit metadata; inspect privately before setup')
    return value


def install_agent(m, image, secret, proof=None):
    if not re.fullmatch(m.PLAYIT, image) or not re.fullmatch(r'[A-Za-z0-9_-]{8,512}', secret):
        raise ValueError('Invalid approved agent image or agent secret')
    m.pull_verified(image, proof)
    helptext = m.run(['docker', 'run', '--rm', '--network', 'none', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--entrypoint', '/usr/local/bin/playitd', image, '--help']).stdout
    if '--secret-path' not in helptext or '--socket-path' not in helptext:
        raise ValueError('Pinned agent lacks required secret-file/IPC options; no game settings changed')
    meta, key, _ = paths(m)
    m.atomic(key, secret)
    m.atomic(meta, json.dumps({'image': image, 'provenance': proof, 'configured': False}, indent=2))
    m.atomic(m.UNIT.parent / f'{SERVICE}.service', agent_unit(image), 0o644)
    m.run(['systemctl', 'daemon-reload'])
    m.run(['systemctl', 'enable', '--now', SERVICE])


def setup(m, relink=False):
    meta, key, _ = paths(m)
    if (relink or not meta.exists() or not key.exists()) and not sys.stdin.isatty():
        raise ValueError('Account linking requires an interactive terminal. Use pct enter, then astroneer playit setup; never pass secrets as command arguments')
    if meta.exists() and key.exists() and relink:
        record = load_meta(m)
        if input('Type RELINK to replace this agent identity (game credentials stay unchanged): ') != 'RELINK':
            raise ValueError('Agent relinking cancelled')
        secret = getpass.getpass('New linked agent secret (hidden): ')
        target = m.BACK / ('agent-identity-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ'))
        target.mkdir(parents=True, mode=0o700)
        old = {meta: meta.read_bytes(), key: key.read_bytes(), m.UNIT.parent/f'{SERVICE}.service': (m.UNIT.parent/f'{SERVICE}.service').read_bytes()}
        for index, data in enumerate(old.values()):
            m.atomic(target/str(index), data)
        m.run(['systemctl', 'stop', SERVICE])
        try:
            install_agent(m, record['image'], secret, record.get('provenance'))
        except Exception:
            for path, data in old.items():
                m.atomic(path, data, 0o644 if path.suffix == '.service' else 0o600)
            m.run(['systemctl', 'daemon-reload'])
            raise
        print('Agent identity replaced. Run status and configure to confirm its endpoint; game password/saves unchanged.')
        return
    if meta.exists() and key.exists():
        record = load_meta(m)
        unit = m.UNIT.parent/f'{SERVICE}.service'
        desired = agent_unit(record['image'])
        if not unit.exists() or unit.read_text() != desired:
            m.run(['systemctl', 'stop', SERVICE], False)
            m.atomic(unit, desired, 0o644)
            m.run(['systemctl', 'daemon-reload'])
        m.run(['systemctl', 'enable', '--now', SERVICE])
        print('Existing agent identity reused. Create/link a Custom UDP tunnel, then run astroneer playit configure.')
        return
    print('Link an agent to your account using the official Docker setup page:')
    print('https://playit.gg/account/setup/wizard/new-account/docker/docker-name')
    print('Sign in privately in your browser; obtain the agent secret. No account password is requested here.')
    record = resolve('playit', input('Approved publisher tag [latest]: ').strip() or 'latest')
    print(json.dumps(record, indent=2, ensure_ascii=True))
    if input('Type APPROVE to install this pinned agent: ') != 'APPROVE':
        raise ValueError('Agent installation cancelled')
    record['approved'] = True
    secret = getpass.getpass('Linked agent secret (hidden): ')
    install_agent(m, record['image'], secret, record)
    print('Agent installed; transport health not yet proven. In the dashboard create Custom UDP, Proxy Protocol None.')
    print('Record the assigned public IPv4 and external port; set local origin to 127.0.0.1:THAT_EXTERNAL_PORT.')
    print('Then run astroneer playit configure. No game/network configuration has been changed.')


def snapshot(m, c):
    _, _, journal = paths(m)
    if journal.exists():
        raise ValueError('Interrupted network transaction exists. Run astroneer playit configure --rollback before another change')
    target = m.BACK / ('network-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ'))
    target.mkdir(parents=True, mode=0o700)
    files = [m.CONF, m.CONF.parent/'server.env', m.UNIT, m.CONF.parent/'playit-disabled', paths(m)[0],
             m.BASE/'game/Astro/Saved/Config/WindowsServer/Engine.ini',
             m.BASE/'game/Astro/Saved/Config/WindowsServer/AstroServerSettings.ini']
    records = []
    for index, path in enumerate(files):
        if path.exists():
            st = path.stat(); name = str(index)
            (target/name).write_bytes(path.read_bytes()); (target/name).chmod(0o600)
            records.append({'path': str(path), 'file': name, 'mode': st.st_mode & 0o777, 'uid': st.st_uid, 'gid': st.st_gid})
        else:
            records.append({'path': str(path), 'file': None})
    (target/'manifest.json').write_text(json.dumps({'files': records, 'mode': c['mode']})); (target/'manifest.json').chmod(0o600)
    try:
        ticks = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        ticks = None
    m.atomic(journal, json.dumps({'snapshot': str(target), 'pid': os.getpid(), 'start_ticks': ticks}))
    return target


def rollback(m):
    _, _, journal = paths(m)
    if not journal.exists():
        raise ValueError('No pending network transaction to roll back')
    target = Path(json.loads(journal.read_text())['snapshot'])
    if target.parent != m.BACK or not target.name.startswith('network-'):
        raise ValueError('Invalid recovery snapshot location')
    manifest = json.loads((target/'manifest.json').read_text())
    m.run(['systemctl', 'stop', 'astroneer'])
    m.run(['systemctl', 'stop', SERVICE])
    for record in manifest['files']:
        path = Path(record['path'])
        if record['file'] is None:
            # Only remove newly generated INIs/unit files absent at snapshot time.
            if not path.is_relative_to(m.BASE):
                path.unlink(missing_ok=True)
        else:
            m.atomic(path, (target/record['file']).read_bytes(), record['mode'])
            os.chown(path, record['uid'], record['gid'])
    m.run(['systemctl', 'daemon-reload'])
    old = m.load()
    m.run(['systemctl', 'enable' if old['autostart'] and old['mode'] != 'playit-pending' else 'disable', 'astroneer'])
    m.run(['systemctl', 'disable', SERVICE])
    journal.unlink()
    print(f'Network configuration restored from {target}. Game and agent left stopped; forensic logs/saves retained.')


def stable(m, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        evidence = m.runtime_probe(m.load()['port'])
        if not evidence or not evidence.get('ready'):
            raise RuntimeError('GAME FAILURE during 60-second stabilization. Inspect CHILD_EXIT/GAME_EVENT and Wine logs; do not infer a tunnel failure')
        agent = state()
        if agent['phase'] != 'running' or agent['lifecycle'] != 'running' or not agent['tunnels']:
            raise RuntimeError('PLAYIT CONTROL FAILURE during stabilization; local game readiness and external reachability are separate')
        checks = status(m)
        if any(row['state'] != 'PASS' for row in checks if row['check'] in ('playit_agent', 'endpoint_configuration')):
            raise RuntimeError('PLAYIT ENDPOINT/CONTROL FAILURE during stabilization; allocation or agent state changed')
        time.sleep(2)


def network_env(text, ip, port):
    replacements = {'ASTRO_SERVER_PUBLIC_IP': ip, 'ASTRO_SERVER_PORT': str(port), 'ASTRO_SERVER_DOMAIN_NAME': ''}
    rows = []; seen = set()
    for line in text.splitlines():
        key = line.split('=', 1)[0]
        if key in replacements:
            if key not in seen:
                rows.append(f'{key}={replacements[key]}'); seen.add(key)
        else:
            rows.append(line)
    rows.extend(f'{key}={value}' for key, value in replacements.items() if key not in seen)
    return '\n'.join(rows) + '\n'


def migrate(m, ip, port):
    c = m.load(); meta = load_meta(m); _, key, journal = paths(m)
    if journal.exists():
        raise ValueError('Interrupted transaction exists; configure --rollback before another change')
    ip, port = endpoint(ip, port, port)
    secret = key.read_text().strip()
    original_env = (m.CONF.parent/'server.env').read_text()
    actual = dict(line.split('=', 1) for line in original_env.splitlines() if '=' in line and not line.startswith('#'))
    identity = {field: actual.get(variable, c[field]) for field, variable in [('name', 'ASTRO_SERVER_NAME'), ('owner', 'ASTRO_SERVER_OWNER_NAME'), ('password', 'ASTRO_SERVER_PASSWORD')]}
    new = dict(c, **identity)
    new.update(mode='playit', public_ip=ip, port=port, external_port=port, playit_image=meta['image'], playit_secret=secret)
    m.validate(new)
    if new == c and not (m.CONF.parent/'playit-disabled').exists():
        print('Already configured identically; no restart or game-file changes. Use playit test for current health.')
        return
    m.run(['systemctl', 'stop', 'astroneer'])
    snapshot(m, c)
    try:
        port_free(port)
        m.backup(c, prune=False)
        m.run(['systemctl', 'enable', '--now', SERVICE])
        (m.CONF.parent/'playit-disabled').unlink(missing_ok=True)
        meta['operator_endpoint'] = {'public_ip': ip, 'port': port, 'origin': f'127.0.0.1:{port}', 'custom_udp_confirmed': True}
        m.atomic(paths(m)[0], json.dumps(meta, indent=2))
        m.apply(new)
        m.atomic(m.CONF.parent/'server.env', network_env(original_env, ip, port))
        m.run(['systemctl', 'start', 'astroneer'])
        stable(m)
        meta.update(configured=True, public_ip=ip, port=port, confirmed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
        m.atomic(paths(m)[0], json.dumps(meta, indent=2))
        journal.unlink()
    except (Exception, KeyboardInterrupt):
        try:
            rollback(m)
        except Exception:
            raise RuntimeError('Network change failed and rollback is incomplete. Services must remain stopped; use configure --rollback and inspect the private transaction journal') from None
        raise
    print(f'Configured {ip}:{port} -> 127.0.0.1:{port}. Local game stable for 60s; external UDP, registration and Steam join remain unverified.')


def configure(m, public_ip=None, public_port=None, local_port=None, yes=False):
    load_meta(m)
    agent = state()
    if agent['phase'] != 'running' or agent['lifecycle'] != 'running':
        raise ValueError('Agent IPC is unavailable/not running. Check account linking and agent service; no game settings changed')
    if not agent['tunnels']:
        raise ValueError('No enabled tunnel configured. Create Custom UDP in Playit dashboard and rerun configure')
    if public_ip is not None or public_port is not None or local_port is not None:
        if public_ip is None or public_port is None:
            raise ValueError('Manual endpoint requires both --public-ip and --port, verified in the dashboard')
        ip, port = endpoint(public_ip, public_port, public_port if local_port is None else local_port)
        matched = False
        for tunnel in agent['tunnels']:
            if tunnel.get('destination') != f'127.0.0.1:{port}':
                continue
            raw = tunnel.get('display_address', '')
            match = re.fullmatch(r'([A-Za-z0-9.-]+):([0-9]+)', raw)
            if match:
                if int(match[2]) != port:
                    continue
                try:
                    ips, observed_port = tunnel_endpoint(tunnel)
                    if ip not in ips or observed_port != port:
                        continue
                except OSError:
                    pass  # Dashboard-supplied IPv4 must be explicitly confirmed.
            matched = True
        if not matched:
            raise ValueError('Manual endpoint conflicts with current tunnel origin/allocation. Correct the dashboard first; no settings changed')
    else:
        candidates = []
        for tunnel in agent['tunnels']:
            try:
                ips, port = tunnel_endpoint(tunnel)
                for ip in ips:
                    candidates.append((ip, port))
            except (ValueError, OSError):
                continue
        candidates = sorted(set(candidates))
        if not candidates:
            raise ValueError('No compatible IPv4 tunnel with matching ports. Set origin to 127.0.0.1:ASSIGNED_EXTERNAL_PORT. If the display cannot be resolved, use --public-ip IPV4 --port PORT after checking the dashboard')
        for index, (candidate_ip, candidate_port) in enumerate(candidates, 1):
            print(f'{index}: {candidate_ip}:{candidate_port} -> 127.0.0.1:{candidate_port}')
        if yes and len(candidates) != 1:
            raise ValueError('Multiple endpoints; select explicitly with --public-ip and --port')
        choice = '1' if yes else input('Select endpoint number [1]: ').strip() or '1'
        if not choice.isdecimal() or not 1 <= int(choice) <= len(candidates):
            raise ValueError('Invalid endpoint selection')
        ip, port = candidates[int(choice)-1]
    print('Compare this exact IPv4/port with the Playit dashboard. Confirm tunnel type Custom UDP and Proxy Protocol None.')
    print('No router forwarding is required. A public endpoint does not guarantee the game will stay running.')
    if not yes and input('Type CONFIGURE to approve this endpoint and a 60-second game test: ') != 'CONFIGURE':
        raise ValueError('Network change cancelled')
    migrate(m, ip, port)


def status(m, testing=False):
    c = m.load(); meta, _, journal = paths(m)
    agent = state() if meta.exists() else {'phase': 'not-installed', 'lifecycle': 'unknown', 'tunnels': [], 'ipc_available': False}
    rows = []
    def add(check, state, detail):
        rows.append({'check': check, 'state': state, 'detail': detail})
    add('operator_disabled', 'WARN' if (m.CONF.parent/'playit-disabled').exists() else 'PASS', 'Disabled; run setup/configure' if (m.CONF.parent/'playit-disabled').exists() else 'No operator pause')
    add('network_mode', 'PASS' if c['mode'] == 'playit' else 'WARN', c['mode'])
    applying = journal.exists() and transaction_active(m)
    add('transaction', 'WARN' if applying else 'FAIL' if journal.exists() else 'PASS', 'Migration in progress; not committed yet' if applying else 'Recovery needed: configure --rollback' if journal.exists() else 'No incomplete network transaction')
    running = m.run(['systemctl', 'is-active', '--quiet', SERVICE], False).returncode == 0
    add('playit_agent', 'PASS' if running and agent['phase'] == 'running' else 'FAIL', 'Agent phase=' + str(agent['phase']))
    add('tunnel_configured', 'PASS' if agent['tunnels'] else 'WARN', 'Enabled tunnel reported by IPC; UDP type needs dashboard confirmation' if agent['tunnels'] else 'No enabled tunnel observed; run setup/configure')
    match = False; conflict = False
    for tunnel in agent['tunnels']:
        relevant = tunnel.get('destination') == f'127.0.0.1:{c["port"]}'
        raw = re.fullmatch(r'([A-Za-z0-9.-]+):([0-9]+)', tunnel.get('display_address', ''))
        try:
            ips, port = tunnel_endpoint(tunnel)
            match = match or c['public_ip'] in ips and port == c['port'] == c['external_port']
            if relevant and (c['public_ip'] not in ips or port != c['port']):
                conflict = True
        except ValueError:
            if relevant and raw:
                conflict = True
        except OSError:
            pass
    try:
        approval = load_meta(m).get('operator_endpoint', {})
    except ValueError:
        approval = {}
    manual = (not conflict and approval.get('public_ip') == c['public_ip'] and approval.get('port') == c['port'] == c['external_port'] and approval.get('custom_udp_confirmed') is True and any(t.get('destination') == approval.get('origin') == f'127.0.0.1:{c["port"]}' for t in agent['tunnels']))
    add('endpoint_configuration', 'PASS' if c['mode'] == 'playit' and (match or manual) else 'WARN', 'Live IPC/DNS allocation matches' if match else 'Operator-confirmed dashboard allocation and live local origin; public assignment not independently resolved' if manual else 'Endpoint/origin configuration not confirmed')
    add('endpoint_match', 'PASS' if match and c['mode'] == 'playit' else 'WARN', 'Advertised IPv4/port and local origin match live IPC/DNS' if match else 'Configured endpoint not confirmed in current IPC tunnel list')
    for check in ('tunnel_transport_connected', 'external_udp_reachability', 'backend_registration', 'steam_client_connected'):
        add(check, 'WARN', 'Unknown: IPC lifecycle/tunnel listing does not prove this; independent dashboard/external-client verification required')
    if testing:
        evidence = m.runtime_probe(c['port'])
        add('game_process', 'PASS' if evidence and evidence.get('game_pids') else 'FAIL', 'Actual game PID evidence, independent of Docker/agent')
        add('game_owned_udp', 'PASS' if evidence and evidence.get('ready') else 'FAIL', f'Local IPv4 UDP {c["port"]} must be owned by the game')
    return rows


def transaction_active(m):
    journal = paths(m)[2]
    if not journal.exists():
        return False
    try:
        record = json.loads(journal.read_text())
        current = Path(f'/proc/{int(record["pid"])}/stat').read_text().rsplit(')', 1)[1].split()[19]
        return current == record.get('start_ticks')
    except (OSError, ValueError, KeyError, IndexError):
        return False


def guard(m):
    if paths(m)[2].exists() and not transaction_active(m):
        raise ValueError('Interrupted network transaction; configure --rollback before starting the game')
    if (m.CONF.parent/'playit-disabled').exists():
        raise ValueError('Playit disabled by operator; run setup/configure before game start')
    checks = status(m)
    required = ('playit_agent', 'tunnel_configured', 'endpoint_configuration')
    if any(row['state'] != 'PASS' for row in checks if row['check'] in required):
        raise ValueError('Playit agent/tunnel/endpoint not ready; run astroneer playit status/configure. Transport and backend remain independent checks')


def disable(m):
    c = m.load(); _, _, journal = paths(m)
    if journal.exists():
        raise ValueError('Pending transaction exists; use configure --rollback first')
    if c['mode'] in ('playit', 'playit-pending'):
        m.run(['systemctl', 'stop', 'astroneer'])
        m.run(['systemctl', 'disable', 'astroneer'])
    m.run(['systemctl', 'disable', '--now', SERVICE], paths(m)[0].exists())
    # Keep endpoint/config/identity intact for safe re-enable; prevent game starts
    # against a disabled tunnel using a private operator pause marker.
    m.atomic(m.CONF.parent/'playit-disabled', 'Disabled by operator; run playit setup/configure to re-enable\n')
    print('Game stopped and agent disabled. Endpoint, credentials and saves retained. No LAN/direct fallback; setup/configure re-enables explicitly.')
