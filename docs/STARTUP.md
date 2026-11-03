# Startup failure investigation and in-place repair

## What is known

The operator reports PVE 9.0.3, Debian 12 unprivileged CT 105, Docker 20.10.24 and
Proton 10-27. Provisioning, image/game download, initial Proton execution and creation
of WindowsServer INIs succeeded. Subsequent startup exits 1 with no UDP listener.
These observations do not identify the failing command or establish registration.
The repository fix has not been applied to that live guest by the development agent.

The inspected upstream [entrypoint](https://github.com/barumel/docker-astroneer-server/blob/develop/files/usr/bin/entrypoint.sh)
uses `set -e`. Its SteamCMD self-update, game update, first-run Proton, restore and
Node configuration commands can terminate startup on nonzero status. `initialized`
is only touched after first-run Proton returns successfully. The final `wait -n`
can also exit before subsequent cleanup. `initConfig.js` awaits configuration;
missing/unreadable INIs or a rejected Node operation can prevent settings population.
See the [configuration implementation](https://github.com/barumel/docker-astroneer-server/blob/develop/files/srv/src/lib/Config.js).

A SteamCMD timeout line, or a subsequent "fully installed" line, is not an exit-status
measurement. No SteamCMD failure is ignored or retried. No manifest, Wine prefix,
initialization marker, save or volume is deleted to force progress. Determine the
actual failing command before attempting a behavioral fix. The installed image's
entrypoint may differ from today's branch; its captured source line is authoritative.

## Observability changes

The game container uses `--log-driver json-file --log-opt max-size=10m --log-opt
max-file=3`, supported by Docker 20.10. It captures stdout and stderr, including
SteamCMD, Proton and Node output. There is no global Docker daemon configuration
change. Logs persist while the stopped container exists. Before a later start
removes that stopped container, up to the last 2 MiB of 5000 lines plus exit state
are archived privately to `/var/log/astroneer/previous-1.log`; three attempts are kept.
Archival failure prevents removal. Old `log-driver none` output cannot be recovered.
Docker's own log files must be managed through Docker, not edited directly.
[Docker logging documentation](https://docs.docker.com/engine/logging/drivers/json-file/).

The service uses `Restart=no` and permits at most three explicit starts per five
minutes. A failed Docker container remains for inspection. `ExecStopPost` reports
container status, exit code, OOM state and any observed failure stage. If Docker
fails before creating a container, inspect `journalctl -u astroneer` for its launch
error or a failed pre-start/archive/backup. Starting the service returns when the
process launches; it does not wait for a UDP listener or backend readiness.

A root-owned read-only observer is mounted into the game container and launched by
Bash. It sources `/usr/bin/entrypoint.sh` from the same pinned image with ERR tracing,
without xtrace or an entrypoint copy. Nonzero status is preserved. A failure marker
contains exit status, source basename, line and a whitelisted stage:

| Stage | Command identified |
| --- | --- |
| steamcmd-self-update | `bash /steamcmd/steamcmd.sh +quit` |
| steamcmd-game-update | `bash /steamcmd/steamcmd.sh +runscript /tmp/install.txt` |
| proton-initialization | Initial foreground Proton server launch |
| upstream-restore | `node /srv/src/restore.js` |
| configuration | `node /srv/src/initConfig.js` |
| runtime-child-exit | `wait -n`; inspect output to identify the child |
| unclassified | Inspect captured source line; arbitrary arguments are not printed |

Explicit `exit` and signals may only produce an entrypoint-exit marker or Docker
exit state. No marker is not proof of success. Stage markers describe the command
boundary, not necessarily the root cause inside SteamCMD/Proton/Node. A zero child
exit can also end the upstream process without proving a functioning game server.

The observer changes the shell invocation while keeping the pinned image, steam user,
capability restrictions, no-new-privileges and persistent mounts. Test its live behavior
on the current image before claiming a runtime fix. No source image is rebuilt.

## Apply to the existing CT 105

These commands are for the administrator to run deliberately. They do not destroy,
recreate or reprovision the LXC. Choose a reviewed checkout containing this change;
push the local change first if transferring through Git. Do not run the host installer.

On the authorized Proxmox host, from the reviewed repository checkout:

```bash
# Read-only confirmation: make sure this is the intended existing guest.
pct config 105

# Copy only management scripts from the reviewed commit to a staging directory.
git archive --format=tar HEAD scripts install > /root/astroneer-repair.tar
pct exec 105 -- mkdir -p /root/astroneer-repair
pct push 105 /root/astroneer-repair.tar /root/astroneer-repair.tar --perms 0600
pct exec 105 -- tar -xf /root/astroneer-repair.tar -C /root/astroneer-repair

# Stop the old loop, install management files and regenerate only the game unit.
# Default repair leaves it stopped for inspection; no apt, pulls or data reseeding.
pct exec 105 -- bash /root/astroneer-repair/install/repair.sh

# The command is reachable even when pct enter omits /usr/local/bin from PATH.
pct exec 105 -- env PATH=/usr/bin:/bin astroneer version
pct exec 105 -- /usr/bin/astroneer config
```

Repair requires existing `/etc/astroneer/config.json` and `server.env`. It does not
rewrite these files, INIs, provenance, startup markers, game files, saved games,
Playit configuration or boot enablement. Repeated repair only stops the service and
reconciles managed code/unit/PATH entries. `astroneer repair` subsequently regenerates
the unit without reinstalling code. `repair.sh --start` / `astroneer repair --start`
opt into one immediate attempt; default repair does not start the game.

Capture one attempt, after reviewing the configuration and confirming the guest ID:

```bash
pct exec 105 -- /usr/bin/astroneer start
# After startup has progressed or exited:
pct exec 105 -- /usr/bin/astroneer logs --tail 500
pct exec 105 -- /usr/bin/astroneer logs --game --tail 200
pct exec 105 -- /usr/bin/astroneer diagnostics --json
pct exec 105 -- journalctl -u astroneer -n 80 --no-pager
pct exec 105 -- ss -lun
```

Log viewing is a snapshot; rerun it as needed. `--game` may report no log before
Proton starts. `--previous` displays the prior archived attempt after a subsequent
start. The CLI applies best-effort redaction of configured passwords/agent secret,
common token/password key formats and terminal control sequences. Docker's raw
logs can contain credentials: keep them private and review redacted output before
sharing. `logs --tail` accepts 1–5000; views and archives are bounded to 2 MiB.
Diagnostic JSON includes no raw log text and keeps UDP, registration and client
connection evidence separate.

If needed, compare the exact pinned image source without executing it again:

```bash
pct exec 105 -- docker cp astroneer-game:/usr/bin/entrypoint.sh /root/entrypoint.from-image.sh
```

Inspect that file locally at the reported line. Do not publish raw `docker inspect`
output (environment includes secrets). Useful findings are the failure marker,
redacted surrounding lines, Docker exit/OOM state and systemd failure messages.
If the marker points to Node configuration, investigate its actual exception and
INI permissions/paths before touching SteamCMD. If it points to SteamCMD, retain
its exit code and preceding output before considering recovery. If it points to
`wait -n`, identify which child ended and why. Do not change the image digest or
weaken LXC restrictions while establishing this evidence.

If manual attempts hit systemd's rate limit, inspect logs first, then explicitly
run `pct exec 105 -- systemctl reset-failed astroneer` before one further attempt.
The post-stop reporter can report a nonzero exit from an intentional stop as well;
correlate timestamps rather than assuming every exit is a spontaneous crash.

## Validation limits

Offline tests execute the observer against stubbed external commands, preserve
nonzero and explicit-exit statuses, reproduce configuration failures and interrupted
SteamCMD operations, and verify that timeout text with a zero status continues.
They also test repair preservation/idempotency, archive-before-remove, log redaction,
retention and service policy. macOS Bash lacks `wait -n`; that fixture confirms its
nonzero unsupported-option status locally, while Linux CI exercises child-exit status.
The real pinned image and CT 105 were not executed or modified during development.
