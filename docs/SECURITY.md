# Security model

The host wizard creates an unprivileged LXC with nesting/keyctl. It does not alter
host AppArmor, firewall configuration, other guests, kernel parameters or host Docker.
The nested Docker daemon is root inside the guest; users with access to its socket
have guest-root authority. Do not expose that socket or add ordinary users to docker.
The game is explicitly steam, with capabilities dropped and no-new-privileges.
The agent also drops capabilities; its upstream default user has not been independently
audited. Both use the guest network namespace and remain inside LXC's host isolation.

Secrets are read without terminal echo, stored in private JSON/env/secret files and
never passed as shell code or command arguments. The initial host config is temporary
mode 0600 and the staged guest copy is removed on success/failure when the CT remains
reachable. If cleanup cannot reach the guest, remove `/root/astroneer-config.json`
manually after recovery. Root can still read environment variables through Docker's
API and process inspection. Backups contain plaintext secrets and must remain private.
Do not run provisioning with shell tracing or collect raw Docker/game logs externally.

Raw stdout/stderr is retained in Docker's rotating, per-container json-file logs
(10m × three files for the game). Access through Docker is guest-root-equivalent;
never grant untrusted users Docker socket access or publish raw logs. `astroneer logs`
redacts known configured password/agent secret and common credential key formats,
and strips terminal escape/control sequences. Redaction is best-effort: review output
before sharing. Archived previous-attempt logs are additionally redacted, bounded
and stored in mode-0600 files under root-only `/var/log/astroneer`.

The observer never enables xtrace or dumps arbitrary command arguments/environment.
Only known stage identifiers, source basename, line and exit status are reported.
Application stderr stays in Docker logs; Docker CLI/pre-start errors and safe exit
summaries go to the systemd journal. stdout alone is attached to the service to
avoid duplicating raw application stderr into journald. Diagnostic JSON contains
safe state/exit summaries and no raw application logs. Server-owned game logs may
also contain secrets and remain behind the private data parent.

Dependencies come from Debian's authenticated apt repositories. Runtime/agent images
must use reviewed digests. Image digests alone prove identity, not provenance or lack
of vulnerabilities. Upstream SteamCMD/game downloads are mutable and outside this
repository's artifact verification. Keep host/guest security updates current and review
image changes. No remote shell code is downloaded/executed by these wrappers.

Backups use SHA-256 for accidental corruption detection, not authenticity against
an attacker who can rewrite the backup and sidecar. Restore is root-only, rejects
archive traversal, links/devices and incomplete layouts, and extracts to a staging
directory before replacing Saved. Restore consumes temporary disk space. Backups
must come from a trusted administrator; never restore an untrusted archive.

Normal exceptions roll back restore renames; power loss/SIGKILL can leave
`Saved.rollback`. Never delete it blindly. Follow recovery instructions. Whole-guest
backups on separate storage are recommended before runtime changes. No capability
settings compensate for unverified LXC compatibility or unsupported console policy.

Runtime supervision reads process names, non-zombie state, socket inodes and FD links
inside the Docker PID namespace. It requires no ptrace capability or privileged
container. Socket ownership that cannot be established fails readiness; permissions
are never relaxed to force a pass. Machine-id diagnostics show only validity status,
never the ID. ELF dependency checks run ldd only against known Proton ELF paths
inside the already-approved image. No Windows executable is passed to host ldd.
Native Proton file logging is disabled; limited Wine error/SEH output uses the
bounded root-private Docker sink with existing best-effort CLI redaction. No
all-channel trace, environment dump, authentication change or game-data change is made.

Playit setup requires a TTY for hidden agent-secret input; account authentication
happens only in the provider's browser dashboard. The new unit invokes playitd with
`--secret-path` and does not use the upstream shell entrypoint's inline secret argv.
The key and socket directory are root-only. IPC status deliberately discards login
links, account details and error payloads; status/test show whitelisted phase and
parsed endpoint evidence. Agent raw logs are bounded/private and should not be shared.
Network snapshots contain passwords and original INIs; keep them private. Existing
backup archives are not pruned by the migration backup. Explicit --yes means the
operator confirmed the dashboard allocation/UDP type; it grants no provider approval
and does not convert unknown reachability or registration into a verified result.

## Shared networking

Shared agents require no local secret or dependency. Only assigned guest destinations are accepted; route availability remains the operator's responsibility. Local agent registration/key files are retained across mode changes and disable operations. The core configuration no longer duplicates the agent secret during network migration; old private backups may still contain it and must remain protected. Rollback never edits provider tunnels. Game startup does not depend on provider availability.
