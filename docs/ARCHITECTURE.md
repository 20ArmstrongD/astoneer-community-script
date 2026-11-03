# Architecture decision — 2026-11-02

Select Docker/Proton (Option A). Keep this implementation standalone. Bash performs
OS/package setup; Python's standard library handles structured configuration,
validation, subprocess argument arrays, locking and archives without shell evaluation.
There is one management module, avoiding separate scripts with divergent backup logic.

| Concern | Docker + Proton | Native AstroTuxLauncher + Wine |
| --- | --- | --- |
| Maintenance | Upstream image integrates runtime; digest can be reviewed | Launcher plus Python requirements and host Wine need maintenance |
| LXC | Nested Docker requires nesting/keyctl and compatible host kernel/storage | Fewer namespace/storage layers |
| Encryption | Current upstream advertises enabled encryption | Current launcher documentation defaults to disabling encryption |
| Security | Non-root game, dropped capabilities, unprivileged LXC; Docker daemon is guest-root equivalent | Can run under dedicated unprivileged user without Docker daemon |
| Updates | Image pinned; Steam game updates at every start | Launcher can automatically update game at start |
| Overhead | Extra Docker daemon, image and Proton disk use | Less runtime layering; Wine still required |
| Operations | Familiar container packaging; nested Docker failure modes | Wine prefix and Python dependency management on Debian |

The encryption advantage drives this choice, despite native Wine's simpler LXC
permission model. This is a design decision, not a deployment benchmark.
[Docker project](https://github.com/barumel/docker-astroneer-server),
[launcher README](https://github.com/JoeJoeTV/AstroTuxLauncher).

Minimum requested LXC features are nesting and keyctl. No privileged LXC,
unconfined AppArmor, host Docker socket, host mount or firewall disable is used.
Docker shares only the **guest** network namespace with the game/agent, avoiding
an extra NAT layer. It does not share the Proxmox host's network namespace.
Nested Docker compatibility depends on host kernel, AppArmor and backing storage;
`docker info`, pulling and running the pinned image must succeed. No automatic
security downgrade is attempted. If this fails, retain the stopped failed guest,
inspect errors and use a separately approved Debian machine or VM, or evaluate
native Wine with its encryption caveat. This installer never creates a VM.

The upstream [entrypoint](https://github.com/barumel/docker-astroneer-server/blob/develop/files/usr/bin/entrypoint.sh)
updates Steam and the game before initializing configuration. The image is
immutable by digest but the downloaded game and SteamCMD are mutable. The wrapper
therefore makes a stopped save/config backup before each start and explicit update.
This protects persisted files, not unsaved in-memory progress. Upstream signal
handling and graceful save-on-stop require integration validation.

Persistent bind mounts map the upstream's three data roots. The installer discovers
the steam UID/GID and seeds SteamCMD from the image because an empty bind mount
hides image content. The systemd unit explicitly runs the game as steam. Saved
configuration uses the upstream environment interface; passwords never appear in
shell arguments or unit text. No compose dependency is needed.

Community Scripts now separates the shared engine from application scripts;
[core](https://github.com/community-scripts/core) supplies the wizard and build
functions. [ProxmoxVE's Docker script](https://github.com/community-scripts/ProxmoxVE/blob/main/ct/docker.sh)
uses that engine. This repository does not source mutable remote shell code or
pretend its standalone wizard follows that API. For upstreaming, adapt the wizard
to a reviewed core revision, add ct/install metadata and submit first to
[DevScripts](https://github.com/community-scripts/DevScripts). See research notes
for unavailable provenance and deployment gates.

## Startup observability revision

The game now uses bounded Docker json-file logs and no automatic restart. A read-only
Bash observer sources the selected image's entrypoint and reports whitelisted failure
stages with line/status; it does not retry or suppress errors. It changes the shell
entrypoint invocation for observation while retaining the image digest and all existing
mounts/security restrictions. Recent evidence is archived before Docker container
replacement; persistent game directories are never removed. See STARTUP.md for the
unverified live cause and deployment repair procedure.

## Runtime supervision revision

The observer intercepts the inspected entrypoint's runtime `wait -n` handoff using
its game/helper PIDs. It polls for completion and explicitly waits each exited PID,
including already-finished zero-status children, then terminates remaining children
and returns a service failure. This avoids the upstream final wait with only Node
remaining. A Python watchdog checks the actual Wine game process and owns-socket
relationship using read-only procfs, independently of the Proton launcher's lifetime.
The systemd ExecStartPost gate uses the same evidence before activation; ExecStopPost
also stops a leftover Docker workload after failed activation. No image rebuild,
machine-id injection, library installation or capability/security changes occur.

## Playit migration revision

The existing guest can now be migrated without the host wizard. The agent is a
separate digest-pinned systemd/Docker workload with guest host networking, dropped
capabilities and no-new-privileges. Direct daemon secret-file flags avoid exposing
its secret in argv. A private IPC v2 client inspects status/tunnels; dashboard
linking and Custom UDP creation remain explicit operator actions. Only provider
interfaces verified in upstream source are implemented; unknown protocol versions
fail closed and transport reachability is not inferred from daemon lifecycle.

New installs default to a two-stage pending mode. Existing migration journals exact
network/config files, takes a non-pruning backup, sets agent-before-game ordering,
applies the confirmed allocation and tests local stability for 60 seconds. Failure
restores configuration while preserving saves/evidence and leaves services stopped.
The game digest, Wine supervisor, cgroup diagnostics and LXC permissions are retained.
