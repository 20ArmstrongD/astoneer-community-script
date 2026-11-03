#!/usr/bin/env python3
"""Standalone host wizard. Never operates on an existing guest."""
import argparse
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manage import validate, run
from images import resolve

ROOT = Path(__file__).resolve().parent.parent


def ask(label, default=''):
    answer = input(f'{label} [{default}]: ').strip()
    return answer or default


def yes_no(label, default='yes'):
    value = ask(label, default)
    if value not in ('yes', 'no'):
        raise ValueError(f'{label}: expected yes or no')
    return value == 'yes'


def number(label, default, lo, hi):
    val = ask(label, str(default))
    if not val.isdecimal() or not lo <= int(val) <= hi:
        raise ValueError(f'{label}: expected {lo}..{hi}')
    return int(val)


def token(value, label):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}', value):
        raise ValueError(f'Invalid {label}')
    return value


def wizard(advanced):
    nextid = run(['pvesh', 'get', '/cluster/nextid']).stdout.strip().strip('"')
    h = {'id': int(nextid), 'hostname': 'astroneer', 'storage': 'local-lvm',
         'cores': 2, 'ram': 4096, 'swap': 1024, 'disk': 30, 'bridge': 'vmbr0',
         'ip': 'dhcp', 'gateway': '', 'vlan': '', 'dns': '', 'search': ''}
    if advanced:
        for key, label, lo, hi in [('id', 'Container ID', 100, 999999999), ('cores', 'CPU cores', 2, 128), ('ram', 'RAM MiB', 4096, 1048576), ('swap', 'Swap MiB', 0, 1048576), ('disk', 'Disk GiB', 30, 65536)]:
            h[key] = number(label, h[key], lo, hi)
        for key in ('hostname', 'storage', 'bridge'):
            h[key] = token(ask(key, h[key]), key)
        h['ip'] = ask('IPv4 CIDR or dhcp', 'dhcp')
        if h['ip'] != 'dhcp':
            ipaddress.IPv4Interface(h['ip'])
            h['gateway'] = str(ipaddress.IPv4Address(ask('IPv4 gateway')))
        h['vlan'] = ask('VLAN tag (empty for none)')
        if h['vlan'] and (not h['vlan'].isdecimal() or not 1 <= int(h['vlan']) <= 4094):
            raise ValueError('Invalid VLAN')
        h['dns'] = ask('DNS IPv4 (empty to inherit)')
        if h['dns']:
            ipaddress.IPv4Address(h['dns'])
        h['search'] = ask('DNS search domain (empty to inherit)')
        if h['search']:
            token(h['search'], 'search domain')
    image, game_provenance = select_image('game')
    c = {'image': image, 'name': ask('Server name', 'Astroneer'),
         'owner': ask('Owner Steam username'), 'password': getpass.getpass('Server password: '),
         'retention': 7, 'autostart': yes_no('Autostart yes/no'),
         'playit_image': '', 'playit_secret': ''}
    playit_provenance = network(c)
    return h, validate(c), {'game': game_provenance, 'playit': playit_provenance}


def select_image(kind):
    print(f"Image selection: {'Docker Hub / barumel/docker-astroneer-server' if kind == 'game' else 'GHCR / playit-cloud/playit-agent'}")
    print('Resolve a publisher tag to an immutable linux/amd64 digest using HTTPS; no host Docker needed.')
    print('A verified digest identifies content; it does not prove publisher trust or source correspondence.')
    reference = ask('Publisher tag or sha256 digest', 'latest')
    record = resolve(kind, reference)
    print(json.dumps(record, indent=2, ensure_ascii=True))
    if input('Type APPROVE to use this exact image (anything else cancels): ') != 'APPROVE':
        raise ValueError('Image approval declined; no container was created')
    record['approved'] = True
    return record['image'], record


def network(c):
    print('Astroneer Dedicated Server Setup\nHow would you like players to connect?')
    print('1. Playit.gg — Create new agent (recommended if you have none)')
    print('2. Playit.gg — Use existing shared agent (no new agent or secret needed)')
    print('3. Direct connection (public IPv4 and router forwarding)')
    print('4. LAN testing (public reachability not established)')
    choice = ask('Select', '1')
    # Accept previous textual choices for scripts driving the old wizard.
    choice = {'playit': '1', 'direct': '3', 'lan': '4'}.get(choice, choice)
    if choice not in ('1', '2', '3', '4'):
        raise ValueError('Select 1, 2, 3 or 4')
    preference = {'1': 'playit-local', '2': 'playit-existing', '3': 'direct', '4': 'lan'}[choice]
    c.update(mode='network-pending', public_ip='', port=8777, external_port=8777, network_preference=preference)
    print('Connection settings will be completed inside the new guest with astroneer network configure.')
    print('No public endpoint is required before installation; this guest address is discovered after networking starts.')
    validate(c)
    print('Network setup INCOMPLETE: endpoint deferred; game stays stopped.')
    return None


def create_args(h, template, autostart):
    net = f'name=eth0,bridge={h["bridge"]},ip={h["ip"]},firewall=1'
    if h['gateway']:
        net += f',gw={h["gateway"]}'
    if h['vlan']:
        net += f',tag={h["vlan"]}'
    args = ['pct', 'create', str(h['id']), template, '--hostname', h['hostname'],
            '--ostype', 'debian', '--unprivileged', '1', '--features', 'nesting=1,keyctl=1',
            '--cores', str(h['cores']), '--memory', str(h['ram']), '--swap', str(h['swap']),
            '--rootfs', f'{h["storage"]}:{h["disk"]}', '--net0', net,
            '--onboot', '1' if autostart else '0']
    if h['dns']:
        args += ['--nameserver', h['dns']]
    if h['search']:
        args += ['--searchdomain', h['search']]
    return args


def validate_host(h):
    for key, lo, hi in [('id', 100, 999999999), ('cores', 2, 128), ('ram', 4096, 1048576), ('swap', 0, 1048576), ('disk', 30, 65536)]:
        if type(h[key]) is not int or not lo <= h[key] <= hi:
            raise ValueError(f'{key}: expected {lo}..{hi}')
    for key in ('hostname', 'storage', 'bridge'):
        token(h[key], key)
    if h['ip'] != 'dhcp':
        ipaddress.IPv4Interface(h['ip'])
        if not h['gateway']:
            raise ValueError('Static IPv4 requires --gateway')
        ipaddress.IPv4Address(h['gateway'])
    elif h['gateway']:
        raise ValueError('DHCP mode does not use an explicit gateway')
    if h['vlan'] and (not str(h['vlan']).isdecimal() or not 1 <= int(h['vlan']) <= 4094):
        raise ValueError('VLAN must be 1..4094')
    if h['dns']:
        ipaddress.IPv4Address(h['dns'])
    if h['search']:
        token(h['search'], 'DNS search domain')
    return h


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--advanced', action='store_true', help='Show advanced interactive container options')
    p.add_argument('--template', required=True, help='Existing trusted Debian 12/13 amd64 template volume ID')
    p.add_argument('--yes', action='store_true', help='Create a new guest without prompts; requires identity/secret/image approval flags')
    p.add_argument('--ctid', type=int); p.add_argument('--hostname', default='astroneer')
    p.add_argument('--storage', default='local-lvm'); p.add_argument('--bridge', default='vmbr0')
    p.add_argument('--cores', type=int, default=2); p.add_argument('--ram', type=int, default=4096)
    p.add_argument('--swap', type=int, default=1024); p.add_argument('--disk', type=int, default=30)
    p.add_argument('--ipv4', default='dhcp'); p.add_argument('--gateway', default='')
    p.add_argument('--vlan', default=''); p.add_argument('--dns', default=''); p.add_argument('--search-domain', default='')
    p.add_argument('--server-name', default='Astroneer'); p.add_argument('--owner')
    p.add_argument('--password-file', type=Path, help='Regular root-owned 0600 file; never put the game password in argv')
    p.add_argument('--image-tag', default='latest'); p.add_argument('--approve-image', action='store_true')
    p.add_argument('--network-mode', choices=('playit-local', 'playit-existing', 'direct', 'lan'), default='playit-local')
    p.add_argument('--no-autostart', action='store_true')
    return p


def unattended(a):
    if a.ctid is None or not a.owner or a.password_file is None or not a.approve_image:
        raise ValueError('--yes requires --ctid, --owner, --password-file and --approve-image; secrets are never CLI values')
    h = validate_host({'id': a.ctid, 'hostname': a.hostname, 'storage': a.storage,
                       'cores': a.cores, 'ram': a.ram, 'swap': a.swap, 'disk': a.disk,
                       'bridge': a.bridge, 'ip': a.ipv4, 'gateway': a.gateway, 'vlan': str(a.vlan),
                       'dns': a.dns, 'search': a.search_domain})
    st = a.password_file.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o077:
        raise ValueError('--password-file must be a regular root-owned 0600 file')
    password = a.password_file.read_text().rstrip('\r\n')
    record = resolve('game', a.image_tag)
    print(json.dumps(record, indent=2, ensure_ascii=True))
    record['approved'] = True
    c = validate({'image': record['image'], 'name': a.server_name, 'owner': a.owner,
                  'password': password, 'retention': 7, 'autostart': not a.no_autostart,
                  'playit_image': '', 'playit_secret': '', 'mode': 'network-pending',
                  'public_ip': '', 'port': 8777, 'external_port': 8777,
                  'network_preference': a.network_mode})
    return h, c, {'game': record, 'playit': None}


def main(argv=None):
    a = parser().parse_args(argv)
    if a.yes and a.advanced:
        raise ValueError('--advanced is interactive; use resource flags with --yes instead')
    explicit = (a.ctid is not None or a.owner or a.password_file is not None or a.approve_image or a.no_autostart or a.hostname != 'astroneer' or a.storage != 'local-lvm' or a.bridge != 'vmbr0' or a.cores != 2 or a.ram != 4096 or a.swap != 1024 or a.disk != 30 or a.ipv4 != 'dhcp' or a.gateway or a.vlan or a.dns or a.search_domain or a.server_name != 'Astroneer' or a.image_tag != 'latest' or a.network_mode != 'playit-local')
    if explicit and not a.yes:
        raise ValueError('Automation resource/identity flags require --yes; use --advanced for interactive choices')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+:vztmpl/debian-(12|13)-[A-Za-z0-9_.-]+_amd64.tar.(zst|xz|gz)', a.template):
        raise ValueError('Use a Debian 12/13 amd64 template from pveam')
    if os.geteuid() != 0:
        raise ValueError('Run on Proxmox as root')
    version = run(['pveversion']).stdout
    if not re.match(r'pve-manager/[89]\.', version):
        raise ValueError('Only Proxmox VE 8/9 are targeted; deployment compatibility requires testing')
    if not a.yes and not sys.stdin.isatty():
        raise ValueError('Use an interactive terminal, or --yes with the documented automation flags')
    if os.uname().machine != 'x86_64':
        raise ValueError('Requires an amd64/x86_64 Proxmox host; ARM emulation is unsupported')
    h, c, provenance = unattended(a) if a.yes else wizard(a.advanced)
    validate_host(h)
    # A cluster-wide nextid is advisory; pct create provides final uniqueness check.
    if run(['pct', 'config', str(h['id'])], False).returncode == 0 or run(['qm', 'config', str(h['id'])], False).returncode == 0:
        raise ValueError('ID already exists; no guest was modified')
    print(json.dumps(h, indent=2))
    print('Unprivileged container; nesting/keyctl enabled. No firewall rules are changed.')
    if not a.yes and input('Type CREATE to create this new container: ') != 'CREATE':
        raise ValueError('Cancelled')
    created = False
    try:
        run(create_args(h, a.template, c['autostart'])); created = True
        run(['pct', 'start', h['id']])
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'project.tar'
            run(['tar', '-cf', archive, '-C', ROOT, 'install', 'scripts'])
            proof = Path(tmp) / 'image-provenance.json'
            proof.write_text(json.dumps(provenance, indent=2)); proof.chmod(0o600)
            run(['pct', 'push', h['id'], proof, '/root/astroneer-image-provenance.json', '--perms', '0600'])
            cfg = Path(tmp) / 'config.json'; cfg.write_text(json.dumps(c)); cfg.chmod(0o600)
            run(['pct', 'push', h['id'], archive, '/root/astroneer-project.tar', '--perms', '0600'])
            run(['pct', 'push', h['id'], cfg, '/root/astroneer-config.json', '--perms', '0600'])
        run(['pct', 'exec', h['id'], '--', 'mkdir', '-p', '/root/astroneer-project'])
        run(['pct', 'exec', h['id'], '--', 'tar', '-xf', '/root/astroneer-project.tar', '-C', '/root/astroneer-project'])
        run(['pct', 'exec', h['id'], '--', 'bash', '/root/astroneer-project/install/astroneer-install.sh', '/root/astroneer-config.json', '/root/astroneer-image-provenance.json'])
        print(f'Installation command completed. Verify with: pct exec {h["id"]} -- astroneer diagnostics')
        if c['mode'] in ('playit-pending', 'network-pending'):
            print(f'Network setup INCOMPLETE; game intentionally stopped. Run pct enter {h["id"]}, then astroneer network configure.')
    except Exception:
        if created:
            print(f'Partial installation retained in CT {h["id"]}. No guest was destroyed. Inspect and rerun the guest installer after correcting the cause.', file=sys.stderr)
        raise
    finally:
        if created:
            # Remove only the staged secret file in the guest we just created.
            run(['pct', 'exec', h['id'], '--', 'rm', '-f', '/root/astroneer-config.json', '/root/astroneer-image-provenance.json'], False)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError) as e:
        print(f'installer: {e}', file=sys.stderr)
        sys.exit(1)
