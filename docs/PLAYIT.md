# Playit.gg setup

Playit is optional networking infrastructure. Astroneer can start independently of a local agent. Existing shared agents require no local install, credentials or IPC access. Account linking and tunnel creation use the official dashboard; this project does not invent a cloud API or alter unrelated tunnels.

## New local agent

Inside the guest, run `astroneer network configure` and choose **Playit — New agent**. The image resolver displays the official publisher reference, resolved immutable digest and Linux/amd64 architecture for approval. The managed agent uses systemd and starts after reboot with reduced container capabilities.

Follow the displayed account-link URL in your browser. Treat the URL as sensitive. If the dashboard reports an agent/account limit, select an existing agent or resolve the account limit; never delete an existing registration automatically. Invalid credentials require checking the linked agent with the provider, not silently registering replacements. If no tunnel exists yet, complete setup in the dashboard or return later to the wizard.

Create a **Custom UDP** tunnel on that agent. Record its actual assigned public IPv4 and external UDP port. Set the local destination to `127.0.0.1` and the SAME port. Confirm these values in the wizard. Plans/account limits and allocation options can vary; follow the dashboard's current instructions.

For unattended new local agents, use `--mode playit-local --agent-secret-file PRIVATE_ROOT_OWNED_FILE --approve-image --yes` with the endpoint flags. The file must be a regular root-owned private file. Do not put tokens in command arguments. Existing managed keys/registration are reused; a fresh secret is not needed on repeated setup. Interactive setup remains recommended for first-time linking.

## Existing shared agent

Choose **Playit — Existing agent**. No Playit dependency is installed. In that agent's dashboard, create or select a Custom UDP tunnel dedicated to this server. Set its local destination to the Astroneer guest's reachable IPv4, not loopback on the agent machine. Use the assigned external UDP port as the local destination port too.

The agent may be another LXC, VM or physical server. It must reach the guest over the LAN or a VPN. An arbitrary internet-hosted agent cannot reach a private guest address without such a route. Keep router forwarding disabled for this mode.

Enter the allocation's numerical public IPv4 and UDP port, then confirm the guest destination. An allocation hostname must be resolved and checked against the dashboard; the CLI does not silently choose an address or use the ISP's outbound IP. A mismatched external/local port is rejected with instructions to adjust the tunnel.

```bash
astroneer network configure --mode playit-existing --public-ip "$PLAYIT_IPV4"   --port "$PUBLIC_UDP_PORT" --local-port "$PUBLIC_UDP_PORT"   --destination-ip "$SERVER_IPV4" --yes --start
```

External agent availability is **unknown**, including when local configuration succeeds. Confirm its dashboard status, exact allocation, tunnel destination and route. No shared secret needs to be copied into the game guest.

## Inspect, migrate and disable

`astroneer playit setup`, `configure`, `status` and `test` remain compatibility commands. Prefer the unified `network` commands for switching modes. Legacy `playit configure --agent existing --destination-ip ...` also supports shared agents.

To migrate from local to shared, prepare the shared tunnel, run the network wizard and choose existing agent. The old local registration/key is preserved. Once other tunnel users have been checked, optionally run `astroneer playit disable` to stop/disable only this project's managed local agent. It does not stop the game, erase credentials or delete a provider registration. Switching to direct/LAN likewise does not silently stop shared infrastructure.

The local IPC client uses verified upstream v2 status/tunnel fields, not a cloud management API. A daemon lifecycle state or configured tunnel is not proof of connected transport, UDP reachability or client success. See [networking](NETWORKING.md) for separate checks and [upgrade](UPGRADE.md) for snapshots/rollback.

Sources: [official Linux agent instructions](https://playit.gg/support/run-on-linux/), [official agent source](https://github.com/playit-cloud/playit-agent), [official Docker package](https://github.com/playit-cloud/playit-agent/pkgs/container/playit-agent).
