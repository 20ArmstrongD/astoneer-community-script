# Installation and real-host acceptance test

Use a disposable new CT on a host you administer. This installer requires root,
Python 3, pct, qm, pvesh, tar, a trusted local Debian 12/13 amd64 template, internet
access and enough free storage for the CT plus temporary restore staging/backups.
It creates only a new CT after showing its settings and receiving CREATE. Never
run against an existing guest ID. No automated deployment occurred during development.

1. Obtain the project from its trusted publisher. Verify a reviewed full Git commit
   using your organization's signature/checksum process, detach checkout at that SHA,
   inspect the installer and run offline tests. Use the reviewed commit for this change; do not assume a floating branch has been reviewed.
2. Review [image selection](IMAGES.md). The wizard contacts Docker Hub over verified
   HTTPS, resolves a publisher tag (default latest), verifies manifest/config hashes
   and linux/amd64 architecture, and displays the source and immutable reference.
   Type APPROVE only if you trust that publisher/image. You do not need to install
   Docker on Proxmox or manually find a digest. Registry rate limits, missing tags,
   DNS/TLS errors and missing amd64 images cause a clear failure before CT creation.
   No fallback image, insecure registry or unpinned source is used.
3. On Proxmox, run `pveversion`, `pveam list local`, `pvesm status` and
   `ip link show vmbr0`. Choose a trusted template already downloaded through pveam.
4. From the reviewed checkout run:

   ```bash
   bash ./install.sh --advanced --template EXACT_EXISTING_TEMPLATE_VOLUME_ID
   ```

   Select a free CT ID, appropriate storage and bridge. DHCP is the default;
   static mode requires IPv4 CIDR and gateway. Choose recommended playit or direct/lan. Playit records setup as incomplete and
   defers public endpoint/account configuration to `astroneer playit setup` and
   `astroneer playit configure` inside the new guest. For direct/LAN, enter the
   endpoint requested for that mode. Direct requires your public IPv4 and
   matching forwarded UDP port. Playit requires the assigned public IPv4 and actual
   external UDP port; set the local game/tunnel origin to that same port. LAN asks
   only for the CT LAN address and local UDP port, with no public endpoint prompt. LAN is experimental.
   Use a new game password, not an account password. Secrets are entered without echo.
5. After completion, substitute the ID you selected in these commands:

   ```bash
   pct config CTID
   pct exec CTID -- docker info
   pct exec CTID -- astroneer status
   pct exec CTID -- astroneer diagnostics --json
   pct exec CTID -- ss -lun
   ```

   Confirm unprivileged=1, expected features, correct disk/RAM/network, no AppArmor
   override, local UDP listener and no restart loop. Inspect private logs locally
   if startup fails. First Steam download/config generation can take several minutes.
6. Configure only the required UDP firewall/router rule as described in NETWORKING.
   Verify the advertised endpoint using an independent network and the community
   server checker, then connect a Steam PC client and create/save a world. Record
   process, socket, external UDP, registration and successful join results separately.
7. Run `pct exec CTID -- astroneer backup`. Preserve its printed filename off-host.
   Reboot only this authorized test CT: `pct reboot CTID`. Verify service startup and
   reconnect with the world intact. Server startup itself does not prove registration.
8. In the disposable world, make a recognizable change and back it up. Test explicit
   image update with a reviewed digest. Restore the selected backup with
   `pct exec CTID -- astroneer restore FULL_BACKUP_PATH --yes` and reconnect to check
   expected world content. Run the guest installer again with the same private config
   to test repeat installation. Never use production saves for first restore testing.
9. Repeat on PVE 8/9 and Debian 12/13, record kernel, AppArmor, storage and Docker
   versions. Test Playit separately with its actual allocation. Report failures as
   failures; do not make the guest privileged to get a green result.

Guest installer for recovery/automation, only inside the intended new guest:

```bash
bash /root/astroneer-project/install/astroneer-install.sh /root/private-config.json
```

Use mode 0600 on that file, never commit it, and remove it after provisioning.
An identical repeated configuration reconciles services without replacing game data.
A different existing configuration is refused; use `astroneer config FILE` for
supported changes. Image changes use `astroneer update --image DIGEST`. Network
mode/agent identity changes use the dedicated `astroneer playit setup/configure/disable`
workflow. Existing LAN-to-Playit migration never requires manual JSON/env/INI editing.

The host wizard does not modify firewall policies or create port-forwarding rules.
No automatic CT cleanup occurs after failure. Keep its ID, inspect the cause and
repair or remove that disposable guest only with an administrator's explicit decision.

## Retrying the reported PVE 9.0.3 setup

The reported digest-validation failure happened in the wizard, before CT creation.
Check `pct list` and `qm list` yourself before selecting an ID; the installer refuses
existing IDs. **It cannot resume or overwrite CT 105.** If 105 already exists,
leave it intact and choose another unused ID for a new test.

On your authorized host, update your reviewed checkout and select the commit from
the change report, then run:

```bash
git fetch origin
git switch --detach REVIEWED_COMMIT_SHA
pveam list local
bash ./install.sh --advanced --template EXACT_DEBIAN_12_TEMPLATE_VOLUME_ID
```

Choose 105 only if unused, hostname astroneer, storage `vmdata-pool`, bridge `vmbr0`,
2 cores, 4096 MiB RAM, 1024 MiB swap and 30 GiB disk. At image selection press Enter
for latest (or enter an approved release tag). Review the resolved Docker Hub source,
platform and digest and type APPROVE. Complete the mode-specific network prompts,
review the CT summary, then type CREATE. These are operator instructions; no live
host was accessed while developing the fix. Repeat the acceptance steps above.

## Repair an existing installation

Do not rerun the host creation wizard for an existing CT. Use the separate
[in-place repair procedure](STARTUP.md#apply-to-the-existing-ct-105) to transfer the
reviewed management files and run `install/repair.sh` inside that guest. It preserves
configuration, image pin, Docker version and data, leaves the service stopped, and
installs `/usr/bin/astroneer` for the default `pct enter` PATH. Repeating repair is safe.
A repeated guest installation with the identical private configuration also reconciles
the unit and makes one start attempt, but may run apt; use repair to avoid that.

The current repair includes explicit game-child supervision and a read-only runtime
watchdog/probe. See [PROTON.md](PROTON.md) for the CT 105 deployment and evidence
commands. `astroneer start` now waits for game-owned UDP readiness and returns failure
on an early exit or readiness deadline, even if Node or Docker remains running.
No native runtime libraries or machine-id settings are changed by repair.

## Recommended Playit two-stage setup and CT 105 migration

New Playit installations do not ask for a public IP before tunnel creation. The
runtime is installed in `playit-pending` mode and game start/boot activation is
deferred. Inside the guest use `astroneer playit setup`, create/link a Custom UDP
tunnel in the official dashboard, and use `astroneer playit configure` to approve
its assigned numerical IPv4 and port. The game and local origin use that same port.
Agent setup installs only the reviewed agent image; it preserves the game digest.

For the existing CT 105, run the updated repair script inside that guest rather
than creating a container. See [PLAYIT.md](PLAYIT.md) for exact transfer commands,
interactive linking, rollback and external Steam acceptance tests. The 60-second
migration test intentionally rejects the reported 11-second game exit if it recurs;
a public endpoint is not assumed to fix Proton/game termination.
