# Safe in-place upgrade, migration and rollback

This procedure updates managed application code in an existing guest. It does not recreate the guest, install an OS, change guest networking/security, pull a different game image or erase saves/configuration/backups/agent keys. The repair stops the game and leaves it stopped unless `--start` is explicitly passed. Plan a maintenance window and retain your normal Proxmox backup as well.

On the Proxmox host, from a reviewed checkout at the trusted new commit, set your existing guest ID and inspect it first. Replace the placeholder before running:

```bash
CTID=YOUR_EXISTING_CT_ID
pct config "$CTID"
git status --short
git rev-parse HEAD
git archive HEAD scripts install > /root/astroneer-upgrade.tar
pct exec "$CTID" -- mkdir -p /root/astroneer-upgrade
pct push "$CTID" /root/astroneer-upgrade.tar /root/astroneer-upgrade.tar --perms 0600
pct exec "$CTID" -- tar -xf /root/astroneer-upgrade.tar -C /root/astroneer-upgrade
pct exec "$CTID" -- bash /root/astroneer-upgrade/install/repair.sh
pct enter "$CTID"
```

The guest repair requires an existing installation. It refreshes CLI/runtime observers and the game unit, removing the old local-agent dependency. It preserves current image pinning, credentials, persistent paths and runtime restrictions. Repeating it is supported; it does not reinitialize game data.

Inside the guest:

```bash
astroneer version
astroneer network status
astroneer network configure
astroneer logs --tail 200
astroneer network test --observe 60
```

Choose the desired networking mode. To migrate local Playit to shared, configure a dedicated Custom UDP tunnel on the existing agent first, targeting this guest's IPv4 and the assigned external port. Select existing agent in the wizard. No shared credentials are requested. Once verified, optionally disable the old managed agent with `astroneer playit disable`; registration and key are retained. No unrelated tunnel is modified.

## Rollback

A failed configuration write restores exact prior files and leaves the game stopped. An interrupted transaction blocks startup with recovery instructions:

```bash
astroneer network rollback
```

Every committed change prints its private `network-*` snapshot directory. To restore a selected prior network configuration:

```bash
astroneer network rollback --snapshot /var/backups/astroneer/SELECTED_NETWORK_SNAPSHOT --yes
astroneer start
```

Rollback leaves the game stopped until explicitly started. It does not alter provider tunnels or agent registration. Correct the dashboard destination/allocation yourself if necessary. Network-only snapshots are not substitutes for save backups. Normal archive restore also supports `--keep-network` when restoring saves while retaining the current endpoint; inspect `astroneer restore --help` first.

Once configuration is committed, a game-start failure DOES NOT undo the confirmed endpoint. Diagnose it separately rather than repeatedly migrating networking. Read the logs for child exit/Wine errors and keep the bounded-restart supervision intact. To revert code, archive a reviewed prior checkout and repeat repair; avoid versions that lack support for the currently selected network mode.

## External Steam validation

Keep the game running throughout a meaningful observation period, confirm its owned UDP socket, then verify the Playit dashboard allocation/destination (or direct router forwarding). Ask a Steam PC on a different network to add/join the exact public IPv4:port through Astroneer's dedicated-server interface. Record successful authentication, join and sustained gameplay separately from process/tunnel status. If the game exits first, investigate startup; if it stays healthy but joining fails, investigate endpoint, route, provider and backend. Console compatibility remains separate and unverified.
