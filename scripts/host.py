#!/usr/bin/env python3
"""Standalone host wizard. Never operates on an existing guest."""
import argparse
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manage import validate, run

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
    print('Runtime image must be reviewed: barumel/docker-astroneer-server@sha256:...')
    c = {'image': ask('Image digest'), 'name': ask('Server name', 'Astroneer'),
         'owner': ask('Owner Steam username'), 'password': getpass.getpass('Server password: '),
         'port': number('Local UDP port', 8777, 1024, 65535),
         'mode': ask('Network mode direct/playit/lan', 'direct'), 'retention': 7,
         'autostart': yes_no('Autostart yes/no'),
         'public_ip': ask('Advertised IPv4 (assigned Playit IP if tunneling)'),
         'external_port': 8777, 'playit_image': '', 'playit_secret': ''}
    c['external_port'] = number('Actual external UDP port', c['port'], 1024, 65535)
    if c['mode'] == 'playit':
        print('Create an agent at https://playit.gg/account/setup/wizard/new-account/docker/docker-name')
        print(f'Create Custom UDP tunnel to 127.0.0.1:{c["port"]}. Disable Proxy Protocol.')
        c['playit_image'] = ask('Reviewed ghcr.io/playit-cloud/playit-agent image digest')
        c['playit_secret'] = getpass.getpass('Agent secret: ')
    return h, validate(c)


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


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--advanced', action='store_true')
    p.add_argument('--template', required=True, help='Existing trusted Debian 12/13 amd64 template volume ID')
    a = p.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+:vztmpl/debian-(12|13)-[A-Za-z0-9_.-]+_amd64.tar.(zst|xz|gz)', a.template):
        raise ValueError('Use a Debian 12/13 amd64 template from pveam')
    if os.geteuid() != 0:
        raise ValueError('Run on Proxmox as root')
    version = run(['pveversion']).stdout
    if not re.match(r'pve-manager/[89]\.', version):
        raise ValueError('Only Proxmox VE 8/9 are targeted; deployment compatibility requires testing')
    if not sys.stdin.isatty():
        raise ValueError('Host installation requires an interactive terminal')
    h, c = wizard(a.advanced)
    # A cluster-wide nextid is advisory; pct create provides final uniqueness check.
    if run(['pct', 'config', str(h['id'])], False).returncode == 0 or run(['qm', 'config', str(h['id'])], False).returncode == 0:
        raise ValueError('ID already exists; no guest was modified')
    print(json.dumps(h, indent=2))
    print('Unprivileged container; nesting/keyctl enabled. No firewall rules are changed.')
    if input('Type CREATE to create this new container: ') != 'CREATE':
        raise ValueError('Cancelled')
    created = False
    try:
        run(create_args(h, a.template, c['autostart'])); created = True
        run(['pct', 'start', h['id']])
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'project.tar'
            run(['tar', '-cf', archive, '-C', ROOT, 'install', 'scripts'])
            cfg = Path(tmp) / 'config.json'; cfg.write_text(json.dumps(c)); cfg.chmod(0o600)
            run(['pct', 'push', h['id'], archive, '/root/astroneer-project.tar', '--perms', '0600'])
            run(['pct', 'push', h['id'], cfg, '/root/astroneer-config.json', '--perms', '0600'])
        run(['pct', 'exec', h['id'], '--', 'mkdir', '-p', '/root/astroneer-project'])
        run(['pct', 'exec', h['id'], '--', 'tar', '-xf', '/root/astroneer-project.tar', '-C', '/root/astroneer-project'])
        run(['pct', 'exec', h['id'], '--', 'bash', '/root/astroneer-project/install/astroneer-install.sh', '/root/astroneer-config.json'])
        print(f'Installation command completed. Verify with: pct exec {h["id"]} -- astroneer diagnostics')
    except Exception:
        if created:
            print(f'Partial installation retained in CT {h["id"]}. No guest was destroyed. Inspect and rerun the guest installer after correcting the cause.', file=sys.stderr)
        raise
    finally:
        if created:
            # Remove only the staged secret file in the guest we just created.
            run(['pct', 'exec', h['id'], '--', 'rm', '-f', '/root/astroneer-config.json'], False)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError) as e:
        print(f'installer: {e}', file=sys.stderr)
        sys.exit(1)
