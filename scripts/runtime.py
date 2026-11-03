"""Read-only Linux runtime evidence, socket ownership and watchdog (stdlib only)."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time

EXE = 'AstroServer-Win64-Shipping.exe'


def probe(port, proc=Path('/proc')):
    pids = []
    sockets = set()
    for entry in proc.glob('[0-9]*'):
        try:
            if not (entry / 'comm').read_text().strip().startswith('AstroServer'):
                continue
            stat = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if stat[0] in ('Z', 'X'):
                continue
            pids.append(int(entry.name))
            for fd in (entry / 'fd').iterdir():
                try:
                    match = re.fullmatch(r'socket:\[([0-9]+)\]', os.readlink(fd))
                    if match:
                        sockets.add(match[1])
                except OSError:
                    pass
        except (OSError, IndexError):
            continue
    bound = set()
    try:
        for line in (proc / 'net/udp').read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) > 9 and int(fields[1].split(':')[1], 16) == port:
                bound.add(fields[9])
    except (OSError, ValueError):
        pass
    return {'game_pids': sorted(pids), 'udp_port': port, 'udp_bound': bool(bound),
            'game_owned_udp': bool(bound & sockets), 'ready': bool(pids and bound & sockets)}


def watch(port, timeout=300, interval=1):
    deadline = time.monotonic() + timeout
    seen = False; ready = False; samples = 0; missing_socket = 0
    while True:
        evidence = probe(port)
        if evidence['game_pids']:
            seen = True
        elif seen:
            print('ASTRONEER_GAME_EVENT stage=game-disappeared exit_status=unknown', flush=True)
            return 70
        samples = samples + 1 if evidence['ready'] else 0
        if samples >= 2 and not ready:
            ready = True
            print(f'ASTRONEER_LOCAL_READY udp={port} ownership=game backend=unknown', flush=True)
        if ready:
            missing_socket = 0 if evidence['ready'] else missing_socket + 1
            if missing_socket >= 5:
                print('ASTRONEER_GAME_EVENT stage=udp-lost exit_status=unknown', flush=True)
                return 72
        elif time.monotonic() >= deadline:
            print('ASTRONEER_GAME_EVENT stage=readiness-timeout exit_status=unknown', flush=True)
            return 71
        time.sleep(interval)


def audit():
    try:
        value = Path('/etc/machine-id').read_text().strip()
        machine = 'valid' if re.fullmatch('[a-f0-9]{32}', value) and int(value, 16) else 'empty-or-invalid'
    except OSError:
        machine = 'missing-or-unreadable'
    dependencies = []
    for filename in ('/geproton/files/bin/wine64', '/geproton/files/bin/wine', '/geproton/files/lib64/wine/x86_64-unix/ntdll.so'):
        if not Path(filename).is_file():
            continue
        row = {'file': filename, 'checked': False, 'missing': []}
        try:
            result = subprocess.run(['ldd', filename], capture_output=True, text=True, timeout=5)
            row['checked'] = result.returncode == 0
            row['missing'] = re.findall(r'^\s*([A-Za-z0-9_.+-]+) => not found', result.stdout, re.M)
        except (OSError, subprocess.TimeoutExpired):
            pass
        dependencies.append(row)
    return {'machine_id': machine, 'machine_id_causality': 'unknown', 'dependencies': dependencies,
            'dependency_scope': 'ELF imports only; Windows DLL/runtime failures require Wine logs',
            'proton_present': Path('/geproton/proton').is_file(),
            'wine_debug': 'errors and SEH; no environment dump; fsync unchanged'}


def memory_info(proc=Path('/proc'), root=Path('/')):
    """Resolve membership against mount roots and take the minimum visible ancestor limit."""
    def read(path):
        try:
            return path.read_text()
        except OSError:
            return ''
    def decode(value):
        return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)
    mounts = []
    for line in read(proc / 'self/mountinfo').splitlines():
        left, sep, right = line.partition(' - ')
        before = left.split(); after = right.split()
        if sep and len(before) >= 5 and len(after) >= 3:
            if after[0] == 'cgroup2' or after[0] == 'cgroup' and 'memory' in after[2].split(','):
                mounts.append((after[0], PurePosixPath(decode(before[3])), PurePosixPath(decode(before[4]))))
    def limits(member):
        rows = {}
        for line in read(proc / member / 'cgroup').splitlines():
            parts = line.split(':', 2)
            if len(parts) != 3:
                continue
            hierarchy, controllers, path = parts
            target = PurePosixPath(path)
            if not target.is_absolute() or '..' in target.parts:
                continue
            for kind, mountroot, mount in mounts:
                if not ((kind == 'cgroup2' and hierarchy == '0' and not controllers) or (kind == 'cgroup' and 'memory' in controllers.split(','))):
                    continue
                # mountinfo may expose a host-relative mount root while cgroup
                # membership is namespace-relative. Stay inside the visible mount.
                relative = target.relative_to(mountroot) if target.is_relative_to(mountroot) else target.relative_to('/')
                visible = root / str(mount).lstrip('/')
                current = visible / str(relative)
                filename = 'memory.max' if kind == 'cgroup2' else 'memory.limit_in_bytes'
                while current.is_relative_to(visible):
                    value = read(current / filename).strip()
                    if value:
                        limit = int(value) if value.isdecimal() and int(value) < 2**60 else None
                        rows[str(current / filename)] = {'limit_bytes': limit, 'value': value}
                    if current == visible:
                        break
                    current = current.parent
        return rows
    rows = limits('self'); initrows = limits('1')
    finite = [r['limit_bytes'] for r in rows.values() if r['limit_bytes'] is not None]
    initfinite = [r['limit_bytes'] for r in initrows.values() if r['limit_bytes'] is not None]
    effective = min(finite) if finite else None
    counters = {}
    for source, row in rows.items():
        if effective is not None and row['limit_bytes'] == effective:
            for event in read(Path(source).parent / 'memory.events').splitlines():
                parts = event.split()
                if len(parts) == 2 and parts[1].isdecimal():
                    counters[parts[0]] = int(parts[1])
    return {'memory_events_historical': counters, 'effective_limit_bytes': effective, 'init_limit_bytes': min(initfinite) if initfinite else None,
            'sources': rows, 'scope': 'minimum visible ancestor limit; hidden host ancestors cannot be inspected',
            'host_memtotal_used': False}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('probe', 'watch', 'audit'))
    parser.add_argument('--port', type=int, default=8777)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error('port must be 1024..65535')
    if args.command == 'watch':
        return watch(args.port)
    print(json.dumps(audit() if args.command == 'audit' else probe(args.port)), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
