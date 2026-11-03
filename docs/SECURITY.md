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

Raw application output is suppressed because upstream components may emit secrets.
The `logs` command provides a bounded error count instead; game-owned log files live
behind a private data parent. Diagnostic JSON contains addresses and host resource
information, but no passwords or tokens. Review before publishing even redacted output.

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
