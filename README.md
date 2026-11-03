# Astoneer Community Script

A standalone host wizard and management CLI for an encrypted Astroneer dedicated
server using the community Docker/Proton runtime inside an unprivileged Debian LXC.
**Unreleased deployment candidate. No live installation or client connection has
been tested. Do not treat generated files or a running process as a working server.**

Targets: Proxmox VE 8/9, Debian 12/13 amd64, IPv4, systemd. Defaults: 2 cores,
4096 MiB RAM, 1024 MiB swap, 30 GiB disk, UDP 8777. Actual compatibility is pending
[deployment testing](docs/INSTALLATION.md). ARM is not supported.

Clone [20ArmstrongD/astoneer-community-script](https://github.com/20ArmstrongD/astoneer-community-script)
and check out a reviewed full commit SHA in detached mode (`git switch --detach FULL_COMMIT_SHA`).
Verify the publisher's signature/checksum through a separate trusted channel and
inspect the files before running them as root.

On a Proxmox host, after reviewing the [installation procedure](docs/INSTALLATION.md):

```bash
# List existing templates; choose a trusted Debian 12/13 amd64 template.
pveam list local
# Substitute the exact volume ID shown by pveam.
bash ./install.sh --template local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst
# Advanced host options:
bash ./install.sh --advanced --template YOUR_ACTUAL_TEMPLATE_VOLUME_ID
```

The template above illustrates the format; it is not a claim that this version is
available. Supply an existing template. Default setup uses local-lvm and vmbr0;
advanced setup configures CT ID, storage, hostname, CPU, RAM, swap, disk, bridge,
DHCP/static IPv4, VLAN and DNS. Both modes ask for game identity, hidden password,
boot policy and network endpoint. The wizard resolves the publisher image tag
(default `latest`) from Docker Hub, verifies SHA-256 metadata and linux/amd64,
displays source/registry/digest/runtime metadata, and requires typing `APPROVE`.
No manual digest lookup or Docker installation on the Proxmox host is needed.
The approved immutable platform digest, rather than the moving tag, is installed.
Registry lookup failure or refusal stops before CT creation. Playit uses the same
approval flow against its official GHCR repository. No existing
CT/VM is modified; failed new guests are retained for inspection. Host installation
is interactive. Guest provisioning accepts a private JSON file for automation.

Inside the new guest, as root, from any working directory:

```bash
astroneer start
astroneer stop
astroneer restart
astroneer status
astroneer logs
astroneer diagnostics
astroneer diagnostics --json
astroneer config
astroneer config /root/reviewed-config.json
astroneer backup
astroneer update --image barumel/docker-astroneer-server@sha256:REVIEWED_DIGEST
astroneer restore /var/backups/astroneer/SELECTED_BACKUP.tar.gz --yes
astroneer version
astroneer help
```

`config` without a file redacts secrets. Configuration files must match
[example.json](config/example.json); example placeholders intentionally fail
validation. Updates require a reviewed immutable digest and make a stopped backup.
Every service start also backs up saves/config before upstream updates game files.
Restores require confirmation or `--yes`, validate archive checksum and paths, stage
on the same filesystem, retain pre-restore data in a backup and roll back directory
renames on ordinary errors. Do not interrupt a restore. Retention defaults to the
last seven backups **including pre-start backups**, not seven days. Backups contain
secrets; copy them securely off the guest. See [recovery details](docs/TROUBLESHOOTING.md).

Runtime data lives in `/var/lib/astroneer/{game,steamcmd,upstream-backup}`. Managed
backups contain `game/Astro/Saved` and private JSON configuration, not game binaries
or the Proton prefix. Normal updates never delete these directories. Reverting an
image does not revert the game's Steam depot. Restored saves must be compatible
with the game version that Steam supplies.

[Networking](docs/NETWORKING.md) explains public IPv4 forwarding, LAN limitations
and the separation of local socket health, external reachability, registration
and successful player connection. [Playit](docs/PLAYIT.md) is optional and
experimental: use the assigned public IPv4 and actual external UDP port. Different
external/local ports are rejected. LAN-only deployment is available for investigation
but may not permit client joins because public registration is still required.

[Architecture](docs/ARCHITECTURE.md), [crossplay](docs/CROSSPLAY.md),
[security](docs/SECURITY.md), and [troubleshooting](docs/TROUBLESHOOTING.md) document
tradeoffs. Xbox self-hosting is unsupported by official policy. PlayStation on this
Linux runtime remains untested. The primary target is Steam PC.

Application stdout/stderr and Docker log capture are disabled to prevent upstream
credential leakage. `logs` reports recent error counts; game log files remain
private and can contain secrets. This sacrifices raw live logs for predictable
redaction. Diagnostics never reports registration or tunnel success from process
health alone. Review private logs locally when necessary.

Run offline checks with `python3 -m unittest discover -s tests -v` and
`bash -n install.sh install/astroneer-install.sh scripts/astroneer`. CI also runs
ShellCheck and shfmt. See [validation record](docs/VALIDATION.md) for what actually ran.

Original wrapper code is MIT licensed. Astroneer belongs to System Era; Steam,
Proton/Wine, Playit and container components retain their own licenses and terms.
No game binaries or third-party implementation source are redistributed here.
Initial selection provenance is retained root-only in
`/etc/astroneer/image-provenance.json`; it records the original approved selection,
not a guarantee that later manual updates use the same image. Registry hashes
verify identity, not signatures or source correspondence. See
[image selection](docs/IMAGES.md) for verification and network requirements.

Thanks to barumel, JoeJoeTV, tteck and Community Scripts. This repository is not
an official Community Scripts installer. A framework port must begin in DevScripts.
