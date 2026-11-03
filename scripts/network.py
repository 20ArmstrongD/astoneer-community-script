"""Networking profiles, interactive setup and transactions; no local-agent runtime dependency."""
import ipaddress
import json
from pathlib import Path
import stat
import sys
import time

import playit

MODES = ('playit-local', 'playit-existing', 'direct', 'lan')


def profile_path(m):
    return m.CONF.parent/'network.json'


def assigned_ipv4(m, include_bridges=False):
    result = m.run(['ip', '-j', '-4', 'addr', 'show'], False)
    if result.returncode:
        raise ValueError('Cannot read this server IPv4 addresses; check iproute2 and the guest network')
    try:
        values = json.loads(result.stdout)
        output = set()
        for interface in values:
            name = interface.get('ifname', '')
            if name in ('lo', 'lo0') or not include_bridges and name.startswith(('docker', 'br-', 'veth')):
                continue
            for item in interface.get('addr_info', []):
                if item.get('family') == 'inet' and item.get('scope') == 'global':
                    address = ipaddress.IPv4Address(item['local'])
                    if not address.is_loopback and not address.is_multicast and not address.is_unspecified:
                        output.add(str(address))
        return sorted(output)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError('Cannot parse this server IPv4 addresses; inspect ip -4 address locally') from None


def destination(m, supplied=None):
    addresses = assigned_ipv4(m, include_bridges=supplied is not None)
    if supplied is None:
        if len(addresses) != 1:
            raise ValueError('Specify --destination-ip using an IPv4 assigned to this server; automatic selection requires exactly one address')
        return addresses[0]
    try:
        supplied = str(ipaddress.IPv4Address(supplied))
    except ValueError:
        raise ValueError('Destination must be a numerical IPv4 assigned to this Astroneer server') from None
    if supplied not in addresses:
        raise ValueError('Destination does not match this Astroneer instance. Use its guest IPv4, not the shared agent address or remote loopback')
    return supplied


def profile(m):
    c = m.load()
    path = profile_path(m)
    if path.exists():
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or value.get('mode') not in MODES:
            raise ValueError('Invalid network profile; rerun astroneer network configure')
        return value
    # Upgrade legacy configurations without rewriting them or needing an agent.
    return {'mode': {'playit': 'playit-local', 'playit-external': 'playit-existing'}.get(c['mode'], c['mode']),
            'public_ip': c['public_ip'], 'port': c['external_port'], 'local_port': c['port'],
            'destination_ip': '127.0.0.1' if c['mode'] == 'playit' else None}


def plan(m, mode, public_ip, port, local_port=None, destination_ip=None):
    if mode not in MODES:
        raise ValueError('Choose playit-local, playit-existing, direct or lan')
    local = port if local_port is None else local_port
    if type(port) is not int or not 1024 <= port <= 65535 or type(local) is not int or not 1024 <= local <= 65535:
        raise ValueError('UDP ports must be integers 1024..65535')
    if local != port:
        raise ValueError(f'Unsupported port mapping: public UDP {port} must equal game UDP {local}. Change the tunnel/forward local destination to UDP {port}, then retry; no tunnel was modified')
    if mode == 'playit-local':
        if destination_ip not in (None, '127.0.0.1'):
            raise ValueError('The managed local agent uses 127.0.0.1 as its destination; choose existing-agent mode for an agent elsewhere')
        target = '127.0.0.1'
    else:
        target = destination(m, destination_ip)
    if mode == 'lan':
        if public_ip is not None and public_ip != target:
            raise ValueError('LAN testing advertises this server address; omit --public-ip or set it to --destination-ip')
        public_ip = target
    else:
        public_ip, _ = playit.endpoint(public_ip, port, local, source='direct' if mode == 'direct' else 'playit')
    return {'mode': mode, 'public_ip': public_ip, 'port': port, 'local_port': local,
            'destination_ip': target}


def apply_plan(m, desired, start=False):
    # Validate again for callers other than the CLI, before stopping a game.
    desired = plan(m, desired['mode'], desired['public_ip'], desired['port'], desired['local_port'], desired['destination_ip'])
    c = m.load(); _, _, journal = playit.paths(m)
    if journal.exists():
        raise ValueError('Interrupted network change; run astroneer network rollback first')
    original_env = (m.CONF.parent/'server.env').read_text()
    actual = dict(line.split('=', 1) for line in original_env.splitlines() if '=' in line and not line.startswith('#'))
    new = dict(c)
    new.pop('network_preference', None)
    new.update({field: actual.get(variable, c[field]) for field, variable in [('name', 'ASTRO_SERVER_NAME'), ('owner', 'ASTRO_SERVER_OWNER_NAME'), ('password', 'ASTRO_SERVER_PASSWORD')]})
    new.update(mode={'playit-local': 'playit', 'playit-existing': 'playit-external'}.get(desired['mode'], desired['mode']),
               public_ip=desired['public_ip'], port=desired['local_port'], external_port=desired['port'],
               playit_image='', playit_secret='')
    local_meta = None
    if desired['mode'] == 'playit-local':
        local_meta = playit.load_meta(m)
        if not playit.paths(m)[1].is_file():
            raise ValueError('Local agent credentials missing; run astroneer playit setup. Shared agents need --mode playit-existing and no secret')
        new['playit_image'] = local_meta['image']
    m.validate(new)
    was_running = m.active()
    updated_env = playit.network_env(original_env, desired['public_ip'], desired['local_port'])
    runtime_changed = updated_env != original_env or not m.UNIT.is_file() or m.UNIT.read_text() != m.unit(new['image'])
    changed = new != c or profile(m) != desired or runtime_changed
    if not changed:
        print('Network settings already match; no configuration write or restart needed.')
    else:
        if runtime_changed:
            m.run(['systemctl', 'stop', 'astroneer'])
        target = playit.snapshot(m, c, include_game=runtime_changed)
        try:
            if runtime_changed:
                playit.port_free(desired['local_port'])
                m.backup(c, prune=False)
            m.apply(new)
            m.atomic(m.CONF.parent/'server.env', updated_env)
            m.atomic(profile_path(m), json.dumps(desired, indent=2)+'\n')
            if local_meta is not None:
                local_meta['operator_endpoint'] = {'public_ip': desired['public_ip'], 'port': desired['port'], 'origin': f'127.0.0.1:{desired["local_port"]}', 'custom_udp_confirmed': True}
                local_meta.update(configured=True, public_ip=desired['public_ip'], port=desired['port'])
                m.atomic(playit.paths(m)[0], json.dumps(local_meta, indent=2))
            journal.unlink()
        except (Exception, KeyboardInterrupt):
            try:
                playit.rollback(m)
            except Exception:
                raise RuntimeError('Configuration failed and rollback is incomplete. Keep the game stopped; run astroneer network rollback after correcting storage/permissions') from None
            raise
        print(f'Network settings saved. Recovery snapshot: {target}')
    print(f'{desired["mode"]}: {desired["public_ip"]}:{desired["port"]} -> {desired["destination_ip"]}:{desired["local_port"]}')
    print('Tunnel, internet reachability, backend registration and client join remain independent checks.')
    if start and not was_running or was_running and runtime_changed:
        maintenance = m.LOCK.parent/'astroneer-network-maintenance'
        try:
            m.atomic(maintenance, 'Network resume: preserve existing backups\n')
            m.run(['systemctl', 'start', 'astroneer'])
        except (RuntimeError, OSError):
            raise RuntimeError('Network settings are saved; GAME STARTUP failed. Read astroneer logs/diagnostics. Do not assume the tunnel caused the game exit; network rollback is optional') from None
        finally:
            maintenance.unlink(missing_ok=True)
        print('Local game-owned UDP startup checks completed; persistent uptime and external client access still need testing.')
    elif not was_running:
        print('Game remains stopped. Start it separately with astroneer start when ready.')


def prepare_local(m, args):
    meta, key, _ = playit.paths(m)
    if meta.exists() and key.exists():
        playit.setup(m)
        return
    if args.agent_secret_file is None:
        if not sys.stdin.isatty():
            raise ValueError('Local-agent automation needs --agent-secret-file and --approve-image, or use an interactive network configure session for browser linking')
        playit.setup(m)
        return
    if not args.approve_image:
        raise ValueError('Automated local-agent installation requires --approve-image after reviewing publisher/source policy')
    path = args.agent_secret_file
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError('Agent secret input must be a regular root-owned file with mode 0600; never pass the secret itself in argv')
    record = playit.resolve('playit', args.agent_tag or 'latest')
    print(json.dumps(record, indent=2, ensure_ascii=True))
    record['approved'] = True
    playit.install_agent(m, record['image'], path.read_text().strip(), record)


def configure(m, args):
    for value in (args.port, args.local_port):
        if value is not None and (type(value) is not int or not 1024 <= value <= 65535):
            raise ValueError('UDP ports must be integers 1024..65535; no settings changed')
    if args.port is not None and args.local_port is not None and args.port != args.local_port:
        raise ValueError('Port mismatch: public and local UDP ports must match. Adjust the tunnel local port; nothing was installed or changed')
    mode = args.mode
    interactive = mode is None
    if interactive:
        if args.yes:
            raise ValueError('--yes requires an explicit --mode')
        print('Astroneer Dedicated Server Setup\nHow would you like players to connect?')
        print('1. Playit.gg — New agent: install one here and link it in your browser')
        print('2. Playit.gg — Existing agent: use a shared agent elsewhere; no secret needed')
        print('3. Direct connection / LAN: router forwarding or local diagnostic testing')
        print('4. Cancel')
        current = m.load()
        preference = current.get('network_preference', {'playit-external': 'playit-existing'}.get(current['mode'], current['mode']))
        default = {'playit-existing': '2', 'direct': '3', 'lan': '3'}.get(preference, '1')
        choice = input(f'Select [{default}]: ').strip() or default
        if choice == '4':
            print('Cancelled; nothing changed.'); return
        if choice == '3':
            direct_default = '2' if preference == 'lan' else '1'
            choice = input(f'1. Direct public connection\n2. LAN testing (no internet reachability implied)\nSelect [{direct_default}]: ').strip() or direct_default
            if choice not in ('1', '2'):
                raise ValueError('Choose direct (1) or LAN testing (2)')
            mode = 'direct' if choice == '1' else 'lan'
        elif choice in ('1', '2'):
            mode = 'playit-local' if choice == '1' else 'playit-existing'
        else:
            raise ValueError('Choose a menu option from 1 to 4')
    if not interactive and not args.yes:
        if not sys.stdin.isatty():
            raise ValueError('Automation requires --yes; it asserts the supplied endpoint and any dashboard/forwarding choices were reviewed')
    if not interactive and mode in ('playit-existing', 'direct') and args.public_ip is None:
        raise ValueError('--public-ip is required for this mode; use the Playit allocation or your direct public IPv4')
    if mode != 'playit-local' and (args.agent_secret_file or args.approve_image or args.agent_tag):
        raise ValueError('Agent installation flags apply only to --mode playit-local; existing/direct/LAN modes install no agent')
    if mode == 'playit-local':
        prepare_local(m, args)
        if interactive:
            answer = input('Create a Custom UDP tunnel in the dashboard, Proxy Protocol None, origin 127.0.0.1:ASSIGNED_PORT. Press Enter when ready, or type later: ').strip()
            if answer == 'later':
                print('Agent linked; game network unchanged. Rerun astroneer network configure when the tunnel is ready.'); return
        ip, port = playit.select_endpoint(m, args.public_ip, args.port, args.local_port, args.yes)
        desired = plan(m, mode, ip, port, args.local_port, args.destination_ip)
    else:
        addresses = assigned_ipv4(m)
        target = args.destination_ip
        if interactive:
            default = addresses[0] if len(addresses) == 1 else ''
            target = input(f'This server destination IPv4 [{default}]: ').strip() or default
        if mode == 'playit-existing':
            print('In your shared agent dashboard create Custom UDP with Proxy Protocol None.')
            print('Set its origin to THIS server IPv4:ASSIGNED_PUBLIC_PORT. Remote 127.0.0.1 would point to the agent machine.')
            print('The shared agent must be able to reach this server; verify routing/firewall permissions yourself. No remote agent will be contacted or modified.')
        elif mode == 'direct':
            print('Forward the chosen UDP port on your router to this server IPv4 at the same port. CGNAT may prevent direct inbound hosting.')
        else:
            print('LAN testing does not establish public internet reachability or backend registration.')
        ip = args.public_ip
        if interactive and mode != 'lan':
            ip = input('Assigned Playit public IPv4: ' if mode == 'playit-existing' else 'Router/server public IPv4: ').strip()
        port = args.port
        if interactive:
            value = input('Public UDP port from dashboard/forwarding [8777]: ' if mode != 'lan' else 'Local UDP port [8777]: ').strip() or '8777'
            if not value.isdecimal():
                raise ValueError('UDP port must be an integer 1024..65535')
            port = int(value)
        if port is None:
            raise ValueError('--port is required; use the actual assigned external port, not a guessed default')
        desired = plan(m, mode, ip, port, args.local_port, target)
        print(f'Confirm: {desired["public_ip"]}:{port} -> {desired["destination_ip"]}:{port}')
        if not args.yes and input('Type CONFIGURE to save these settings: ') != 'CONFIGURE':
            raise ValueError('Cancelled; nothing changed')
    start = args.start
    if interactive and not start:
        answer = input('Start the game after saving? [yes]: ').strip() or 'yes'
        if answer not in ('yes', 'no'):
            raise ValueError('Enter yes or no')
        start = answer == 'yes'
    apply_plan(m, desired, start=start)


def guard(m):
    # Only protect a partial transaction. No agent/relay availability gate.
    if playit.paths(m)[2].exists() and not playit.transaction_active(m):
        raise ValueError('Interrupted network change; run astroneer network rollback before game start')


def rollback(m, snapshot=None, yes=False):
    journal = playit.paths(m)[2]
    if snapshot is not None:
        if journal.exists():
            raise ValueError('A pending transaction exists; recover it with network rollback before choosing a committed snapshot')
        snapshot = snapshot.resolve()
        if snapshot.parent != m.BACK.resolve() or not snapshot.name.startswith('network-') or not (snapshot/'manifest.json').is_file():
            raise ValueError('Choose a network-* snapshot directory under /var/backups/astroneer')
        if not yes and (not sys.stdin.isatty() or input('Type ROLLBACK to restore network settings and leave the game stopped: ') != 'ROLLBACK'):
            raise ValueError('Rollback cancelled; automation requires --yes')
        m.atomic(journal, json.dumps({'snapshot': str(snapshot)}))
    playit.rollback(m)


def status(m, testing=True):
    c = m.load(); current = profile(m); mode = current['mode']
    rows = [{'check': 'network_mode', 'state': 'WARN' if mode in m.PENDING else 'PASS', 'detail': mode},
            {'check': 'configured_endpoint', 'state': 'WARN' if mode in ('lan', 'playit-pending', 'network-pending') else 'PASS', 'detail': f'public={c["public_ip"] or "unassigned"}:{c["external_port"]}; destination={current.get("destination_ip") or "legacy/unrecorded"}:{c["port"]}; configuration only'}]
    if mode == 'playit-local':
        rows.extend(row for row in playit.local_status(m) if row['check'] not in ('network_mode', 'game_process', 'game_owned_udp'))
    elif mode == 'playit-existing':
        rows.append({'check': 'external_agent', 'state': 'WARN', 'detail': 'Unknown: managed elsewhere; no local agent/secret required. Check its dashboard, route and firewall to the recorded destination.'})
    if mode != 'playit-local':
        for check in ('tunnel_transport_connected', 'external_udp_reachability', 'backend_registration', 'steam_client_connected'):
            rows.append({'check': check, 'state': 'WARN', 'detail': 'Unknown; verify independently. A configured endpoint is not an internet/client test.'})
    if testing:
        evidence = m.runtime_probe(c['port'])
        rows.extend([{'check': 'game_process', 'state': 'PASS' if evidence and evidence.get('game_pids') else 'WARN' if mode in m.PENDING else 'FAIL', 'detail': 'Actual game PID evidence; read astroneer logs for CHILD_EXIT and Wine exceptions if absent'},
                     {'check': 'game_owned_udp', 'state': 'PASS' if evidence and evidence.get('ready') else 'WARN' if mode in m.PENDING else 'FAIL', 'detail': f'Game must own IPv4 UDP {c["port"]}; a brief listener is not persistent uptime'}])
    return rows


def observe(m, seconds):
    if not 0 <= seconds <= 3600:
        raise ValueError('--observe must be between 0 and 3600 seconds')
    deadline = time.monotonic() + seconds
    while seconds and time.monotonic() < deadline:
        evidence = m.runtime_probe(m.load()['port'])
        if not evidence or not evidence.get('ready'):
            raise RuntimeError('GAME FAILURE during observation; network settings retained. Read CHILD_EXIT/GAME_EVENT/Wine logs; tunnel availability is a separate check')
        time.sleep(1)


def add_parser(subparsers):
    parser = subparsers.add_parser('network', help='Configure or diagnose new/shared Playit, direct and LAN networking')
    actions = parser.add_subparsers(dest='network_action', required=True)
    config = actions.add_parser('configure', help='Interactive connection wizard, or explicit --mode flags')
    config.add_argument('--mode', choices=MODES, help='Omit for the guided connection menu')
    config.add_argument('--public-ip', help='Numerical Playit allocation or direct public router/server IPv4'); config.add_argument('--port', type=int, help='Actual public UDP port; also the game listening port')
    config.add_argument('--local-port', type=int, help='Must equal --port; defaults to it'); config.add_argument('--destination-ip', help='This guest IPv4 for shared agents/direct/LAN; detected if unambiguous')
    config.add_argument('--yes', action='store_true', help='Confirm supplied allocation/tunnel choices for automation'); config.add_argument('--start', action='store_true', help='Start a stopped game after saving; otherwise preserve its state')
    config.add_argument('--agent-tag', help='New local agent only: approved publisher tag/digest, default latest'); config.add_argument('--agent-secret-file', type=Path, help='New local agent only: root-owned 0600 file with the linked agent secret')
    config.add_argument('--approve-image', action='store_true', help='Explicitly approve publisher/tag resolution when automating a new local agent')
    for action in ('status', 'test'):
        check = actions.add_parser(action); check.add_argument('--json', action='store_true')
        if action == 'test':
            check.add_argument('--observe', type=int, default=0)
    recover = actions.add_parser('rollback'); recover.add_argument('--snapshot', type=Path); recover.add_argument('--yes', action='store_true')
