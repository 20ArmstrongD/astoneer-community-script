# Troubleshooting and recovery

Start with `astroneer diagnostics` or `--json`. Exit 1 means a detected FAIL or an
operational error; WARN may accompany exit 0 because several external states cannot
be tested. Invalid command syntax returns argparse exit 2. Mutation commands require
root and serialize through a nonblocking lock; another active operation returns an
error rather than racing. Use the CLI for normal operations.

`astroneer status` reports systemd. `astroneer logs --tail 300` displays actual
SteamCMD/Proton/entrypoint output, `--game` reads the latest game log, and
`--previous` reads the last archived attempt. These commands redact known secrets;
review before sharing. Missing/disabled logs are not evidence of healthy startup.
Follow [STARTUP.md](STARTUP.md) for in-place repair and stage/exit-code investigation.
The game service does not automatically restart on failure. After reviewing evidence,
start once explicitly; after three starts within five minutes systemd rate-limits
further attempts until `systemctl reset-failed astroneer` or the interval expires.

Docker errors in an unprivileged CT: inspect `journalctl -u docker` locally, host
kernel/AppArmor versions and storage compatibility. Do not disable security or
make the CT privileged. The installer retains failed guests; use a separately
approved environment if nested Docker remains incompatible. No VM is silently made.

First launch: allow time for SteamCMD downloads and initial game-generated INI files.
The upstream initialization launches the server before applying environment settings;
its completion and behavior under this host require real-world testing. A missing
listener can mean initialization, a crash, or incompatible Proton. Verify the actual
pinned image's runtime and upstream issue tracker. Do not delete game volumes to
force an update. Disk free space must cover saves, retained backups and restore staging.

Updates: `astroneer update --image FULL_REVIEWED_DIGEST` pulls first, stops the service,
backs up Saved/config, changes the pin and returns the previous running/stopped state.
Every subsequent launch also backs up before upstream updates game content. Image
pinning does not pin the Steam game version. Backup retention is a count; repeated
restarts can evict older backups. Copy a known-good backup off the guest before an
update or restore. A failed restart is an error, not a rolled-back installation.
Restore a compatible save/config backup and choose a known-good reviewed image as
needed; game-depot rollback is not implemented.

Backup files are timestamped UTC `.tar.gz` archives with `.tar.gz.sha256` sidecars
under `/var/backups/astroneer`. Copy both. They contain Saved (saves, game INIs and
logs) and configuration; runtime binaries/SteamCMD/Proton prefix are outside scope.
Backups stop the game first, so they are filesystem consistent, but upstream's
signal handling has not been verified to save the last in-memory changes. Wait for
an autosave/perform an in-game save before taking a manual backup.

Restore with `astroneer restore PATH` and type RESTORE, or use `--yes` for automation.
A pre-restore backup is made before replacement. Existing agent identity/network mode
must match; cross-agent migration is refused. Whole-guest recovery is required for
runtime corruption. Keep saves compatible with the current game's format.

After an interrupted restore, stop only the affected guest's game service. Preserve
both `game/Astro/Saved` and `game/Astro/Saved.rollback` off-host. Inspect their contents
and selected backup, then have an administrator choose which tree to keep. The CLI
refuses a second restore while Saved.rollback exists. Do not blindly remove it.

Partial installation: config.json is the final configuration commit marker. Rerunning
the guest installer reconciles an identical installed configuration. Failures before
that marker retain data and can be retried using the original private configuration.
Failures after service start require diagnostics/recovery, not claiming success.
A failed image pull leaves the old service/config untouched. Config changes are
atomic per file, not a multi-file transaction across power loss; rerun provisioning
with the intended private configuration after inspecting all installed files.

Cannot join: confirm actual public IPv4/port, local UDP, router/CT firewall, NAT
hairpinning or tunnel allocation, backend registration, game/client version and
client encryption independently. CGNAT and Xbox policy are not solved by a local
listener. Unknown registration remains unknown until independently checked.

A running Node backup process or Docker container is not game health. If Proton/fsync
initializes and AstroServer disappears, use the in-place [Proton supervision repair](PROTON.md).
The launcher status is captured explicitly; game disappearance can fail the service
even when the launcher does not return. Start waits for game-owned IPv4 UDP readiness.
Do not treat a machine-id warning as the root cause or remove the initialized marker
without evidence. The runtime audit and Wine error/SEH output are now in startup logs.
Memory diagnostics resolve ancestor cgroup limits and show the LXC init limit;
unknown/hidden limits are reported as unknown instead of substituting host RAM.
