# Installation and real-host acceptance test

Use a disposable new CT on a host you administer. This installer requires root,
Python 3, pct, qm, pvesh, tar, a trusted local Debian 12/13 amd64 template, internet
access and enough free storage for the CT plus temporary restore staging/backups.
It creates only a new CT after showing its settings and receiving CREATE. Never
run against an existing guest ID. No automated deployment occurred during development.

1. Obtain the project from its trusted publisher. Verify a reviewed full Git commit
   using your organization's signature/checksum process, detach checkout at that SHA,
   inspect the installer and run offline tests. No public release exists yet.
2. On a separate trusted Docker machine, review the upstream image source and build
   provenance. Pull the exact approved tag and record its RepoDigest using
   `docker image inspect --format '{{json .RepoDigests}}' APPROVED_IMAGE_TAG`.
   Check encryption support, steam user, entrypoint, Steam install script and runtime
   version in that actual image. A digest identifies content; it does not establish trust.
   Supply the full `barumel/docker-astroneer-server@sha256:...` string to the wizard.
3. On Proxmox, run `pveversion`, `pveam list local`, `pvesm status` and
   `ip link show vmbr0`. Choose a trusted template already downloaded through pveam.
4. From the reviewed checkout run:

   ```bash
   bash ./install.sh --advanced --template EXACT_EXISTING_TEMPLATE_VOLUME_ID
   ```

   Select a free CT ID, appropriate storage and bridge. DHCP is the default;
   static mode requires IPv4 CIDR and gateway. Choose direct, playit or lan and
   enter the actual advertised IPv4 and external port. LAN is experimental.
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
mode/agent identity migration is deliberately refused and needs manual service review.

The host wizard does not modify firewall policies or create port-forwarding rules.
No automatic CT cleanup occurs after failure. Keep its ID, inspect the cause and
repair or remove that disposable guest only with an administrator's explicit decision.
