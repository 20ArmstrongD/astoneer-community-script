# Astoneer Community Script

Install and manage an Astroneer dedicated server in an unprivileged Proxmox LXC. This standalone community project uses the barumel Docker/Proton runtime, immutable approved images, persistent game storage, bounded logs, backups and game-process supervision. It is not an official Astroneer product or an upstream Community Scripts application.

Targets: amd64 Proxmox VE 8/9 and Debian 12/13 templates. Defaults: 2 cores, 4096 MiB RAM, 1024 MiB swap, 30 GiB disk and UDP 8777. Nested Docker compatibility depends on the host kernel, storage and AppArmor. No privileged container or host Docker installation is required. See [architecture](docs/ARCHITECTURE.md) and [validation limits](docs/VALIDATION.md).

## Install

Clone this repository, check out a trusted full commit or reviewed release, inspect its contents, then run on the Proxmox host as root. Replace placeholders before running:

```bash
git clone https://github.com/20ArmstrongD/astoneer-community-script.git
cd astoneer-community-script
git checkout --detach TRUSTED_FULL_COMMIT
bash install.sh --template TEMPLATE_STORAGE:vztmpl/DEBIAN_AMD64_TEMPLATE.tar.zst
```

Use `--advanced` for container resources, storage, addressing and other advanced choices. The installer displays the publisher, registry, architecture and resolved image digest for approval; users need not find a digest themselves. It refuses existing guest IDs.

Installation prepares the runtime and leaves the game stopped until networking is configured. Enter the new guest with `pct enter YOUR_CT_ID`, then run:

```bash
astroneer network configure
```

Choose **Playit — New agent**, **Playit — Existing agent**, or **Direct / LAN**. Only questions relevant to that choice are asked. New installs remember the initial networking preference. See [installation](docs/INSTALLATION.md) for automation and [safe in-place upgrades](docs/UPGRADE.md) for an existing server.

## How players connect

- **New Playit agent:** approve the agent image, follow the account-link URL, create a Custom UDP tunnel in the dashboard and confirm its actual public IPv4 and UDP port. No router forwarding is required.
- **Existing shared Playit agent:** install nothing extra. Point that agent's Custom UDP tunnel at this guest's reachable IPv4 address. Enter the allocation's public IPv4 and port in the wizard. No shared agent secret is needed. The agent needs a LAN/VPN route to the guest.
- **Direct:** forward the selected UDP port from your router to the guest, using the same port. Enter your public IPv4. CGNAT or upstream filtering may prevent this.
- **LAN:** use the guest's private address for local tests. This does not establish public reachability or guarantee backend registration.

The public and game UDP ports must match in supported configurations. Change the tunnel's local destination port to match its assigned public port. Do not use a hostname, guess the allocation or substitute your ISP address for a Playit address. See [networking](docs/NETWORKING.md) and [Playit instructions](docs/PLAYIT.md).

Automation example (replace the uppercase shell variables with actual values):

```bash
astroneer network configure --mode playit-existing   --public-ip "$PLAYIT_IPV4" --port "$PUBLIC_UDP_PORT"   --local-port "$PUBLIC_UDP_PORT" --destination-ip "$SERVER_IPV4" --yes --start
```

Other modes are `playit-local`, `direct` and `lan`. Run `astroneer network configure --help` for all flags. Network changes preserve saves, passwords, approved image pinning and container security. Shared agents are first-class configurations; game startup never depends on a local Playit agent.

## Management and recovery

```bash
astroneer help
astroneer start
astroneer stop
astroneer restart
astroneer status
astroneer logs --tail 200
astroneer diagnostics --json
astroneer network status
astroneer network test --observe 60
astroneer backup
astroneer update
astroneer restore BACKUP_ARCHIVE
astroneer version
```

Use `astroneer config` for configuration help. Backups are timestamped compressed archives with checksums; restore validates archives and requires explicit overwrite approval. Updates back up first and preserve persistent game directories. The pinned runtime image does not freeze SteamCMD/game downloads. Backups include private configuration: protect them. See [security](docs/SECURITY.md).

Network changes retain a private configuration snapshot and take a stopped, non-pruning game backup when runtime settings change. Configuration-write failures restore prior files. A subsequent game failure leaves the confirmed endpoint saved and reports a separate startup error. `astroneer network rollback` recovers interrupted changes; `--snapshot SNAPSHOT_DIRECTORY --yes` restores a selected committed configuration and leaves the game stopped. See [migration and rollback](docs/UPGRADE.md).

## Known limits

A reported game failure briefly opens UDP before the Proton launcher exits with status zero. Networking changes are not a proven fix. The supervisor treats premature exit as failure even if the backup helper remains alive. Inspect `astroneer logs` and [startup diagnostics](docs/STARTUP.md); do not infer health from Docker, Proton or a brief socket alone.

Process, game-owned socket, agent status, transport, public UDP reachability, backend registration and an actual client join are separate checks. Shared-agent availability remains unknown unless independently verified. Test with a Steam PC outside the hosting network before declaring success. Xbox/PlayStation support is not established; see [crossplay](docs/CROSSPLAY.md).

The local offline test suite does not certify live Proxmox, Playit or client compatibility. Run `python3 -m unittest discover -s tests`; CI also checks shell lint/formatting. [Validation](docs/VALIDATION.md) lists release gates.

## Attribution

MIT-licensed installer; see [LICENSE](LICENSE). Astroneer belongs to System Era Softworks. Runtime projects and services retain their own licenses: [barumel](https://github.com/barumel/docker-astroneer-server), [AstroTuxLauncher](https://github.com/JoeJoeTV/AstroTuxLauncher), [Playit](https://playit.gg), and [Community Scripts](https://github.com/community-scripts/ProxmoxVE). No third-party binary redistribution or provider endorsement is implied.
