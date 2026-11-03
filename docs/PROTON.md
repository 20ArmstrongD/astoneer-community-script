> For all host commands below, set `CTID` to your existing guest ID first. Prefer [the current upgrade procedure](UPGRADE.md).

# Proton game exit: supervision, readiness and evidence

The latest deployment report establishes successful SteamCMD and Node configuration,
followed by Proton/fsync initialization and loss of the Windows game process. There
is no UDP socket and Node remains alive. The actual Wine/game crash cause is still
unverified: neither `fsync: up and running` nor `Failed to open /etc/machine-id`
proves success or causality. No live CT was accessed to develop this repair.

## Confirmed supervision defect and repair

The inspected [upstream entrypoint](https://github.com/barumel/docker-astroneer-server/blob/develop/files/usr/bin/entrypoint.sh)
starts Proton and Node in the background. After a zero-status first child exit,
`wait -n` succeeds, the status comparison does not kill the surviving child, and
the subsequent wait can block forever. The Node
[health checker](https://github.com/barumel/docker-astroneer-server/blob/develop/files/srv/src/lib/HealtCheck.js)
examines save-file conditions, not game-process/socket readiness. It cannot certify
that a game server is running. This explains a false-active service; it does not
explain why the Windows executable exited.

The read-only observer now interposes only the known runtime wait with the upstream
p1/p2 child PIDs. It polls child completion and uses explicit `wait PID` in a
conditional to capture its status, including children that finished before the
handoff. It logs `ASTRONEER_CHILD_EXIT role=... status=...`, terminates remaining
children, and returns a nonzero service result. Launcher status 0 is logged as 0
but mapped to service exit 1, because this long-running server unexpectedly ended.
A nonzero launcher code is retained. No errors are silently retried or accepted.

A third child, the watchdog, detects the actual AstroServer process through Linux
procfs even if its launcher remains alive. Zombies are excluded. Its socket inode
must match a file descriptor owned by the game, so an unrelated listener or a
remaining Node process cannot establish readiness. Watchdog failures are:

| Status | Meaning |
| --- | --- |
| 70 | Game process was seen and then disappeared |
| 71 | No stable game-owned UDP socket within 300 seconds after runtime handoff |
| 72 | Previously ready process/socket evidence lost for five consecutive samples |

These are watchdog statuses, **not invented Windows exit codes**. A Windows result
is only known to the extent Proton propagates it through its launcher. If the
launcher stays alive after the executable disappears, the game exit status remains
unknown; the watchdog still fails the service rather than reporting health.

Systemd activation uses ExecStartPost to require two consecutive samples (two
seconds apart) of a live game process owning the configured IPv4 UDP port (8777 by
default). The service remains activating during preparation/SteamCMD/configuration.
The game watchdog samples once per second. A later process or socket loss still
fails a service that previously became active. The host readiness deadline is 840
seconds, bounded by the unit's 900-second startup timeout including pre-start work.
A cold/large deployment can exceed these deadlines; investigate logs before changing
them. No automatic game restart is enabled. Failed activation explicitly stops any
leftover Docker workload; a surviving Node process is never sufficient for health.

Local readiness is time-specific evidence, not a backend registration or client
connection test. Read failures/unknown socket ownership fail closed, without adding
ptrace or other capabilities. A changed upstream handoff might not expose the same
PIDs; the independent systemd gate still refuses a game without owned-UDP evidence.

## Proton and machine-id investigation

GE-Proton [10-27's launcher](https://github.com/GloriousEggroll/proton-ge-custom/blob/GE-Proton10-27/proton)
redirects Wine output to its native file when PROTON_LOG is enabled. The observer
sets PROTON_LOG=0 and enables limited Wine errors/SEH via WINEDEBUG, leaving fsync,
esync, encryption, prefix and game arguments unchanged. This sends diagnostics to
the existing bounded Docker logs instead of an unbounded Proton file. Existing
explicit WINEDEBUG settings are retained. No environment dump or all-channel trace
is enabled. Logs remain root-private and CLI output uses best-effort redaction.

An `ASTRONEER_RUNTIME_AUDIT` JSON record precedes startup. It checks readable,
nonzero 32-hex machine-id format without printing the identifier, and ELF imports
of existing known Wine/ntdll binaries using ldd inside the pinned container. It does
not check every dynamically loaded dependency or Windows DLL. Read the Wine error
lines for module-load failures, unhandled exceptions and their surrounding context.
A clean ELF audit does not prove that every Windows runtime dependency exists.

A missing machine-id is a reproducible environmental observation, not proof of a
fatal dependency. The identifier is private system identity per the
[systemd documentation](https://www.freedesktop.org/software/systemd/man/latest/machine-id.html).
This repair does not copy the host/LXC machine-id into Docker, generate a replacement,
install libraries, disable fsync or recreate Wine/game files. A controlled machine-id
experiment should only follow an actual trace implicating identity initialization,
with an explicitly reviewed private per-server identity and before/after comparison.

## Effective LXC memory diagnostics

The CLI resolves `/proc/self/cgroup` and `/proc/self/mountinfo`, maps the process to
its visible controller mount and walks ancestors. It uses the minimum finite
memory.max (v2) or memory.limit_in_bytes (v1), respecting mount roots and treating
v1's huge unlimited sentinel as unlimited. It also resolves `/proc/1/cgroup` to show
the LXC init limit separately from any tighter CLI/systemd scope. Historical v2
memory.events counters are reported but are not proof this crash was OOM-related.
The [kernel cgroup documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html)
describes inherited restrictions: a leaf saying max can still have a finite ancestor.

Host MemTotal is never substituted for a container limit. If visible files reveal
no finite limit, the result is unknown/WARN, since host ancestors hidden by the
cgroup namespace cannot be inspected. In the reported CT, expect approximately
4294967296 bytes if the configured 4096 MiB cap is exposed. Inspect evidence before
concluding that a missing limit means unrestricted host RAM or an OOM crash.

## Apply safely to the existing guest

Push the reviewed local commit first if updating through Git. On the authorized
Proxmox host, from the reviewed checkout containing this revision:

```bash
pct config "$CTID"
git archive --format=tar HEAD scripts install > /root/astroneer-repair.tar
pct exec "$CTID" -- mkdir -p /root/astroneer-repair
pct push "$CTID" /root/astroneer-repair.tar /root/astroneer-repair.tar --perms 0600
pct exec "$CTID" -- tar -xf /root/astroneer-repair.tar -C /root/astroneer-repair
pct exec "$CTID" -- bash /root/astroneer-repair/install/repair.sh
```

Repair stops the game service, updates managed code/unit/read-only helper mounts
and PATH entries, and leaves it stopped. It preserves the existing guest, the pinned image,
Docker/Proton versions, boot enablement, config.json/server.env, INIs, Wine prefix,
saves, existing machine identities and container security restrictions. It performs
no pull, apt upgrade, game reseed or data deletion. Repeating repair is safe.

Capture one attempt; start now waits for local readiness and can correctly fail:

```bash
pct exec "$CTID" -- /usr/bin/astroneer start
pct exec "$CTID" -- /usr/bin/astroneer logs --tail 500
pct exec "$CTID" -- /usr/bin/astroneer logs --game --tail 200
pct exec "$CTID" -- /usr/bin/astroneer diagnostics --json
pct exec "$CTID" -- journalctl -u astroneer -n 80 --no-pager
```

If still running, independently inspect actual game/socket evidence and the image
runtime audit (these JSON outputs omit environment and machine-id values):

```bash
pct exec "$CTID" -- docker exec astroneer-game python3 /run/astroneer-runtime.py probe --port 8777
pct exec "$CTID" -- docker exec astroneer-game python3 /run/astroneer-runtime.py audit
```

Look for CHILD_EXIT, GAME_EVENT, STARTUP_FAILURE and the preceding Wine errors.
Review log excerpts for credentials before sharing. Preserve the observed launcher
status and any unhandled-exception/module-load lines. If the watchdog detects a game
exit while the launcher lives, report the unknown status honestly. Do not change
multiple runtime settings while establishing causality. Docker being running or
healthy and Node's "health check running" message are not game readiness evidence.

## Test scope

Offline tests execute real Bash child-supervision fixtures (zero/nonzero/already
exited children, helper exit and watchdog exit), and fixture Linux procfs/cgroup
layouts. They cover unrelated UDP listeners, zombie/helper-only processes, readiness
deadlines, inherited memory limits and failed activation cleanup. No live Proton
crash, real-image ownership visibility, LXC runtime, UDP connection or backend
registration test was executed in this development environment.
