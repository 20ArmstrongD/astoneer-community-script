# Installation

Use a trusted full commit or reviewed release. Inspect the checkout before root execution; there is no curl-pipe-shell bootstrap. See the README for clone commands. The standalone wizard targets amd64 Proxmox 8/9 with Debian 12/13 templates; these targets still require the real-host validation matrix in VALIDATION.md.

List/download a suitable template using Proxmox's normal `pveam` workflow. Then run:

```bash
bash install.sh --template TEMPLATE_STORAGE:vztmpl/DEBIAN_AMD64_TEMPLATE.tar.zst
```

The basic path uses 2 cores, 4096 MiB RAM, 1024 MiB swap, 30 GiB disk, unprivileged LXC and DHCP. `--advanced` asks about ID, hostname, storage, resources, bridge, DHCP/static address, gateway, VLAN and DNS. Both paths ask about server identity/password and networking preference. Image publisher/source/digest/architecture are displayed for explicit approval. No Docker is installed on Proxmox.

Choose new Playit, existing Playit, direct or LAN. All endpoint questions are deferred until the guest exists, so Playit allocations need not exist before installation. The game remains stopped with a clear incomplete-network status. Enter the guest and run `astroneer network configure`. The guest wizard handles account linking or shared-agent instructions and validates the endpoint without manual file editing.

## Unattended host provisioning

Prepare a root-owned 0600 password file without putting its contents in shell history. Replace every placeholder below:

```bash
bash install.sh --template "$TEMPLATE" --yes --ctid "$NEW_CT_ID"   --owner "$STEAM_OWNER" --password-file "$PRIVATE_PASSWORD_FILE"   --approve-image --network-mode playit-existing --storage "$STORAGE" --bridge "$BRIDGE"
```

`--approve-image` explicitly accepts the resolved publisher image; provenance and digest verification remain enforced. Optional flags include `--image-tag`, `--hostname`, `--server-name`, `--cores`, `--ram`, `--swap`, `--disk`, `--ipv4`, `--gateway`, `--vlan`, `--dns`, `--search-domain` and `--no-autostart`. See `bash install.sh --help`. Static IPv4 requires its prefix and gateway. Existing guest IDs are refused. `--network-mode` records a preference (`playit-local`, `playit-existing`, `direct`, `lan`); it does not fabricate an endpoint.

Run the equivalent noninteractive `astroneer network configure` inside the guest with real endpoint values; see NETWORKING.md. Private approval/identity files must not be committed. Partial provisioning retains the guest for inspection rather than destroying it. Do not rerun the host installer against an existing server: use [in-place upgrades](UPGRADE.md).
