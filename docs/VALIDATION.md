# Validation and release gates

Development environment: macOS, Python 3.14. No Proxmox host/target CT was supplied,
no LXC was created and no game, Docker image or Playit tunnel was executed.

Executed locally:

- `python3 -m unittest discover -s tests -v`: 134 offline tests passed after the Playit migration revision.
- `python3 -m py_compile scripts/*.py`: passed.
- `bash -n install.sh install/*.sh scripts/astroneer scripts/entrypoint-observer.sh`: passed.

ShellCheck and shfmt are not installed in this environment. Restricted shell DNS
prevented fetching tooling. Their execution is configured in GitHub Actions, but
that workflow has not run here. No claim of lint or CI success is made.

Tests cover validation, argument rejection, environment generation, Proxmox create
arguments, systemd command routing, update backup ordering, stopped-service recovery,
repeated provision behavior, partial failure, missing dependency errors, archive
roundtrip/checksum/traversal/link safety, retention and diagnostic classification.
Mocks do not establish correctness of Docker, systemd, pct or SteamCMD integration.

Public release blockers:

- Record reviewed full source SHAs and image digests; reconcile source/image contents.
- Resolve Docker image and Playit license inventories before redistribution decisions.
- Run ShellCheck/shfmt and CI on Debian Python 3.11 and 3.13 as well as current Python.
- Complete INSTALLATION's PVE 8/9 × Debian 12/13 real-host matrix with representative
  backing filesystems, kernel and AppArmor versions.
- Verify SteamCMD seeding, initial game setup, non-root UID permissions, dropped
  capabilities, encrypted PC join, autosave, graceful stop, reboot, update and restore.
- Verify actual Playit allocation, agent secret-file UID access, connectivity,
  backend registration and external PC join. Console testing is separate and optional.
- Review log-redaction tradeoffs and game-file schema evolution; add tested diagnostics
  for registration only if a stable supported source becomes available.
- For an upstream contribution, adapt to the current core wizard and submit to
  DevScripts with its required metadata and real-host results. This standalone
  repository is not a ready-to-merge Community Scripts PR.

## Image selection regression validation

The reported PVE 9.0.3 / Debian 12 deployment stopped before CT creation because
manual image-digest input failed validation. This is evidence of wizard execution,
not of a successful guest/game deployment. The revised wizard resolves and verifies
registry metadata itself and requires explicit approval. Tests use synthetic
manifest/config bytes and compute fixture hashes; they contain no fabricated release
pins. Coverage includes multiarch selection, digest/header/config/size tampering,
wrong/missing architecture, malformed metadata, network errors, response bounds,
HTTPS redirects/token handling, approval refusal, the default wizard, all network
modes and guest Docker inspect matching.

Local real-registry lookup: blocked by DNS. Local Docker info: socket permission
denied. No build, pull, new CT, live host access or real game test was performed.
See IMAGES.md for the distinction between documented publication and current live
registry/artifact availability. The newly selected image still needs real-host
acceptance testing; no reproducible source-build claim is made.

## Startup-observability revision

Executed locally: 77 offline tests, Python compilation, Bash syntax checks and
`git diff --check`. The observer itself was executed against temporary Bash fixtures,
not merely mocked. Tests cover failing/interrupted SteamCMD calls, misleading timeout
text with zero exit, Node configuration failure, explicit exits, unknown-command
redaction, the native wait-n behavior, bounded log reads and child timeout cleanup,
previous-attempt retention, repair idempotency/preservation and minimal PATH installation.
No live CT, pinned image, game startup, Docker logging rotation or UDP/client connection
was tested here. ShellCheck/shfmt remain unavailable locally; CI includes both and
now covers the new observer and repair scripts. The underlying live failing command
remains unidentified until the administrator captures one instrumented attempt.

## Proton supervision and readiness revision

Executed: 99 offline tests, Python compilation, Bash syntax and whitespace checks.
The new tests run real Bash children and assert captured zero/nonzero status even
for an already-finished launcher, a surviving helper and a watchdog-detected exit.
Linux procfs fixtures distinguish game-owned UDP from helper/unrelated sockets and
exclude zombie processes. Cgroup v1/v2 fixtures verify finite ancestor inheritance,
mount-root mapping, tighter scoped limits and unknown/unlimited reporting. Readiness
tests reject healthy-looking Docker with no game socket and clean up after a failed
ExecStartPost. Wine/procfs/cgroup checks add no capability or data/image changes.
No actual Proton executable crash was reproduced; its cause remains unknown until
Wine diagnostics from the repaired deployment are examined. No CT was modified.

## Playit migration revision

Executed locally: 134 offline tests, Python compilation, Bash syntax, CLI help and
whitespace checks. New fixtures cover official IPC v2 framing/version/response IDs,
private-field omission, endpoint/mismatch validation, DNS-to-IPv4, daemon secret-file
flags and installation, interactive/noninteractive setup, reuse of existing identity,
LAN-to-Playit configuration, preservation of owner/password/image/save/extra env,
agent-before-game order, occupied ports, missing tunnels, game vs agent failures,
exact byte rollback, legacy-backup restore retaining network identity, failed rollback
journal retention, cancellation, pending transaction recovery, no deletion of
new game INIs, disable/pause behavior and deferred two-stage boot activation.

The source/runtime verification, account linking, tunnel allocation, UDP transport,
backend registration, 60-second real game stability and external Steam join have not
been exercised on CT 105. ShellCheck/shfmt remain unavailable locally; CI runs them.
A read-only remote Git check failed because shell DNS could not resolve github.com;
see the final work report for the actual commit/push outcome. No Proxmox deployment
or live account/tunnel operation was performed by this development agent.
