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
