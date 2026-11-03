# Playit networking: account linking and in-place migration

Playit is the recommended installer option. The integration uses the official Linux
agent in a digest-pinned Docker image **inside the existing LXC**, with a systemd
service, guest host networking, no capabilities and no-new-privileges. It adds no
router forwarding, changes no LXC address, and makes no global firewall changes.
Astroneer through this stack still needs real-host/backend/Steam acceptance tests;
this document does not certify end-to-end compatibility.

## Research and interfaces

[Official Linux guidance](https://playit.gg/support/run-on-linux/) describes native
Debian packages. [Official Docker guidance](https://github.com/playit-cloud/playit-agent)
also supports its GHCR agent image and a secret file with guest host networking.
This project keeps the Docker deployment and existing digest/architecture checks.
It does not import an unverified apt key or install Docker on Proxmox.

The current [daemon CLI](https://github.com/playit-cloud/playit-agent/blob/master/packages/playitd/src/bin/playitd.rs)
supports `--secret-path` and `--socket-path`. The unit invokes that binary directly,
avoiding the image shell entrypoint that converts a file secret into a secret argv.
The actual pinned image's help must expose both options before installation proceeds.
Account linking and Custom UDP creation remain explicit dashboard steps. No cloud
creation API or account credentials are invented by the wrapper.

Read-only [IPC v2](https://github.com/playit-cloud/playit-agent/blob/master/packages/playit-ipc/src/ipc.rs)
provides daemon status and lifecycle/tunnels. A root-only Unix socket is shared with
the guest. The client validates version, response IDs, message types and size/time
bounds. It requests only status/state, never secret/login URLs. It exposes sanitized
phase/endpoint evidence rather than raw payloads. Older/different protocols fail
closed with an actionable status; they are not parsed as if they were v2.

IPC's running lifecycle and enabled tunnel listing are useful control/configuration
evidence. They do **not** expose a definitive UDP transport-connected boolean,
external reachability or a game registration result. The tunnel view also does not
expose its protocol type; verify Custom UDP/Proxy Protocol None in the dashboard.
`status`/`test` keep transport/backend/client states WARN/Unknown. They never infer
those states from an agent or Docker process alone.

## Upgrade the existing CT 105

Use the reviewed commit for this change, after it has been pushed. On the Proxmox
host, from that checked-out repository:

```bash
pct config 105
# Verify this is the existing unprivileged Debian guest; do not run install.sh.
git archive --format=tar HEAD scripts install > /root/astroneer-playit-upgrade.tar
pct exec 105 -- mkdir -p /root/astroneer-playit-upgrade
pct push 105 /root/astroneer-playit-upgrade.tar /root/astroneer-playit-upgrade.tar --perms 0600
pct exec 105 -- tar -xf /root/astroneer-playit-upgrade.tar -C /root/astroneer-playit-upgrade
pct exec 105 -- bash /root/astroneer-playit-upgrade/install/repair.sh
pct enter 105
```

`install/repair.sh` is the in-place management upgrade script. It leaves the game
stopped. It does not create a CT, reinstall Debian, upgrade Docker, pull a game image,
change the verified game digest, rewrite config.json/server.env, delete game files,
or touch networking/storage. CT 105 retains 192.168.1.150/24, its gateway/bridge and
vmdata-pool disk. Existing owner/password/saves/backup data and reduced capabilities
are retained. The updated helper and CLI files are installed on the default PATH.

## Link the account and create the tunnel

Inside CT 105 in an interactive terminal:

```bash
astroneer version
astroneer playit setup
```

1. Visit the displayed official Docker agent wizard in your browser:
   <https://playit.gg/account/setup/wizard/new-account/docker/docker-name>.
   Sign into your account there and create/link the agent. The browser handles
   account passwords and account authentication; this CLI never requests them.
2. In the terminal, choose a publisher tag (latest by default), review its resolved
   immutable linux/amd64 digest/provenance and type APPROVE. Paste only the generated
   **agent secret** at the hidden prompt. Do not pass it on a command line or share it.
   The secret is root-only in `/etc/astroneer/playit-secret`, mounted read-only.
3. The pinned Linux daemon starts as `astroneer-playit.service` and is enabled at
   boot. `setup` does not change the game's existing LAN/public settings. Repeating
   setup reuses the agent identity. To correct/relink a secret explicitly, use
   `astroneer playit setup --relink`; identity snapshots are retained privately.
4. In Playit's dashboard, create/link a **Custom UDP** tunnel to this agent, with
   **Proxy Protocol None**. Tunnel availability/billing depends on your account;
   this software does not buy anything or bypass provider limits.
5. Record the actual public IPv4 and **assigned external UDP port**. Do not assume
   8777. Edit the local origin to **127.0.0.1:THAT_EXTERNAL_PORT**. The game will move
   to the same port. Do not assign the public IP to the LXC network interface.
   Keep 192.168.1.150 and existing Proxmox settings unchanged.
6. Wait for the dashboard/agent state to update, then run:

   ```bash
   astroneer playit status
   astroneer playit configure
   ```

   Select the current endpoint, compare its exact IPv4/port to the dashboard, confirm
   Custom UDP/None and type CONFIGURE. Hostnames are resolved to numerical IPv4;
   multiple allocations require selection. The advertised field is never a hostname
   and never comes from an ISP/outbound-IP lookup. No enabled tunnel yields a clear
   incomplete-setup error without changing the game's configuration.

If the display cannot be resolved/parses differently, supply the actual numerical
allocation you checked in the dashboard (the local IPC origin must still match):

```bash
astroneer playit configure --public-ip ACTUAL_ASSIGNED_IPV4 --port ACTUAL_EXTERNAL_PORT
```

`--local-port` is optional and must equal the external port; mismatch produces
instructions to correct the dashboard origin. Ports below 1024 are refused under
the non-root game policy; choose a suitable allocation. For explicit automation,
`--yes` asserts dashboard confirmation. It does not invent endpoint verification or
allow a known conflicting allocation. For an unresolvable/nonstandard public display,
the private operator confirmation and live origin permit configuration, but the
independent endpoint-match check remains WARN/Unknown until IPC/DNS corroborates it. Without an explicit endpoint, --yes requires
exactly one compatible candidate.

## Transaction and health behavior

Configure stops the game, snapshots private JSON/env/unit and existing WindowsServer
INIs, checks the requested UDP port is free, then takes a stopped save/config backup
**without pruning existing backups**. It changes the advertised IPv4, game/local port
and network mode; preserves identity/password and unrelated env settings; orders the
agent before the game; and starts once. Existing supervision, Wine diagnostics and
game-owned socket readiness remain intact.

After readiness, configure observes the game for 60 seconds. This catches the reported
roughly 11-second clean exit. A game failure is reported separately from agent/control
failure. Neither failure is declared a Playit transport failure without evidence.
A successful 60-second test proves only observed local stability, not indefinite
uptime or external playability.

On failure the prior configuration/INIs are restored byte-for-byte, the image stays
pinned, and game/agent are left stopped for inspection. Save files are never rolled
back/deleted. Newly generated game INIs absent before setup are retained. Logs and
private `network-*` recovery snapshots remain. A power loss/failed rollback leaves a
journal and blocks another migration; recover explicitly:

```bash
astroneer playit configure --rollback
```

Old LAN backups remain intact and can restore saves without undoing the migration:
`astroneer restore BACKUP_PATH --keep-network --yes` retains the current public
endpoint and agent identity. Normal cross-mode restore remains refused by default.

Inspect `astroneer logs --tail 500`, `--previous`, and the journal before retrying.
Pending transactions do not silently commit at reboot. A matching already-completed
configuration is idempotent and does not restart the game; run test to assess health.

```bash
astroneer playit status --json
astroneer playit test --json
astroneer diagnostics --json
astroneer logs --tail 500
```

Keep these states separate: actual game PID; game-owned UDP socket; agent process;
IPC enabled tunnel/lifecycle; live endpoint match; UDP transport connectivity;
external UDP reachability; backend registration; actual Steam join. The last four
need independent evidence. Raw agent logs can contain secrets: do not publish them.

To pause Playit safely:

```bash
astroneer playit disable
```

For a Playit deployment this stops game/agent and disables boot starts, retains all
credentials/configuration/data, and creates an operator pause marker. It does not
silently switch back to LAN/direct. Use setup then configure to re-enable explicitly.
If still in LAN mode, disable does not stop an unrelated LAN game. No direct router
rule is ever created by these commands.

## New-install two-stage wizard

The default recommended network choice is Playit. Stage one creates the new guest,
installs/pins the runtime and records `playit-pending`, with no fabricated endpoint
or account secret. Game start/boot activation is deferred. After entering the guest,
setup/configure completes stage two. Direct and LAN remain selectable and request
their own endpoints. This new-install path is separate from CT 105's in-place upgrade.

## External Steam acceptance test and outstanding limits

1. Verify the dashboard shows the intended agent/tunnel, Custom UDP, None, the exact
   numerical public allocation and matching loopback origin. Check live CLI endpoint
   match and game-owned socket. Keep router forwarding off.
2. Keep the repaired game running beyond the migration's 60-second observation; watch
   for CHILD_EXIT/GAME_EVENT/Wine exceptions for at least several minutes. The earlier
   LAN/private advertised IP may affect backend behavior, but causality is unproven.
   Changing to Playit is an experiment, not a confirmed fix for the clean exit.
3. Independently check the numerical endpoint with the community server checker if
   available, recording its result/time. This CLI does not guess a backend API.
4. From a Steam PC **outside your LAN**, using matching game version and encryption,
   add the numerical Playit IPv4:external-port in Astroneer's dedicated-server list.
   Join, authenticate with the existing server password, load/save a world, disconnect
   and reconnect. Record actual success/failure, not just an online icon.
5. During a maintenance window reboot only the authorized test CT and repeat endpoint,
   game-owned socket and external join checks. Existing account identity/boot policy
   should persist. A tunnel hostname can move/change allocation; reconfigure after
   verifying any provider change.

This development environment has not performed a live agent link, tunnel connection,
Proton/Playit run, registration check or external Steam join. IPC support is tied to
verified v2 schema and reviewed agent flags; actual pinned artifacts still need testing.
Separate local health from relay policy/transport failures. Public/local port mismatch
is unsupported; no unverified translation is attempted. Xbox self-hosting remains
unsupported by official policy; PlayStation with this stack is unverified. No console
compatibility, authentication bypass or provider approval is implied.
