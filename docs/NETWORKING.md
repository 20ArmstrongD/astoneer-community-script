# Networking

Run `astroneer network configure` inside the guest. The menu offers a new local Playit agent, an existing shared agent, direct/LAN choices, or cancellation. No internal JSON, Docker environment or Unreal INI editing is needed.

## Address and port rules

The [official dedicated-server instructions](https://astroneer.space/dedicatedserver/) document one Engine.ini connection port. The inspected [upstream configuration generator](https://github.com/barumel/docker-astroneer-server/blob/develop/files/srv/src/lib/Config.js) maps `ASTRO_SERVER_PORT` to that game port and `ASTRO_SERVER_PUBLIC_IP` to the advertised address. It exposes no separate public-port setting. This project therefore supports matching public/listening ports and rejects translation rather than promising unverified compatibility. This is a supported configuration policy, not proof that every possible translated network is impossible.

For Playit, use the allocation's actual numerical public IPv4 and assigned external UDP port. Set the tunnel local port AND Astroneer port to that same number. With a local agent the destination is loopback; with a shared agent it is an IPv4 assigned to the Astroneer guest. The CLI verifies local address ownership and rejects a foreign address or remote-agent loopback. Multiple guest addresses require an explicit selection. It cannot verify a remote agent's route: provide LAN/VPN connectivity and appropriate firewall access without exposing the port through the router.

Docker uses the guest's network namespace. It does not use the Proxmox host's network namespace. Port conflicts are checked before changing runtime settings.

## Automation

```bash
astroneer network configure --mode playit-existing --public-ip "$PLAYIT_IPV4"   --port "$PUBLIC_UDP_PORT" --local-port "$PUBLIC_UDP_PORT"   --destination-ip "$SERVER_IPV4" --yes --start
astroneer network configure --mode direct --public-ip "$PUBLIC_IPV4"   --port 8777 --destination-ip "$SERVER_IPV4" --yes
astroneer network configure --mode lan --port 8777 --destination-ip "$SERVER_IPV4" --yes
```

`--start` starts a stopped game after saving. Otherwise a stopped game stays stopped; a running game resumes if changed runtime settings require a stop. Role-only changes at an identical endpoint avoid restarting it. Configuration changes do not change the guest's IP, bridge, gateway or firewall and never edit another tunnel.

Direct mode requires router UDP forwarding using the same internal/external port and a reachable public IPv4. LAN mode is diagnostic only: it creates no internet route and does not prove matchmaking works.

## Evidence and troubleshooting

`astroneer network status` or `test --json` reports endpoint, mode, actual game process and game-owned socket separately. Local-agent checks apply only to local mode. Remote-agent transport, external UDP reachability, backend registration and client connectivity remain unknown without independent evidence.

If the process exits, use game logs first. If the game stays running but no owned socket appears, check configuration and runtime diagnostics. If both remain healthy, check the agent dashboard, allocation, destination route and firewall. A UDP scanner alone is not proof of game reachability. Use `astroneer network test --observe 60` to detect premature game exit, then join from a Steam PC on another network. Observation does not certify backend registration or an external join.

See [Playit](PLAYIT.md) and [upgrade/rollback](UPGRADE.md).
