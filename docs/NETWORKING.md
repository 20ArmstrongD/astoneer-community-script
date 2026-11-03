# Networking

Game traffic uses IPv4 UDP, default 8777. Forward the actual external UDP port to
the guest IPv4 at the same port. Allow that UDP port in every applicable firewall
scope: datacenter, node, CT and guest. Keep existing protections. Do not forward a
Docker socket, SSH, RCON or arbitrary TCP ports. The upstream compose example also
publishes TCP; this implementation does not add an inbound TCP rule without evidence
it is needed for game clients.

Direct mode requires a globally routable IPv4 on your router or server, matching
the configured advertised address. CGNAT requires a provider allocation or tunnel.
Hairpin NAT may be needed for clients inside the same LAN to use the public address.
A dynamic public IP requires updating private configuration and restarting with
`astroneer config FILE`; no external-IP discovery silently overrides your endpoint.

LAN mode accepts a private advertised IPv4 and opens no router ports. It is an
experimental deployment mode, not a promise of offline/LAN multiplayer: upstream
reports public-address registration requirements. Do not infer LAN join support
from an active UDP listener. External services may still be contacted by the game.

The inspected configuration source has one port setting used for binding and the
advertised URI. No supported independent external-port control was found.
This installer rejects NAT/tunnel port mismatch. For Playit allocate first, then
set the game's local port and tunnel origin to that actual assigned external port.
This is conservative policy, not proof that all forms of port translation are impossible.
See [upstream configuration](https://github.com/barumel/docker-astroneer-server/blob/develop/files/srv/src/lib/Config.js).

Diagnostics reports independent evidence:

| Observation | What it establishes |
| --- | --- |
| systemd active / Docker running | Process/container exists |
| ss shows local UDP port | A local socket is bound, not necessarily owned by the game |
| Playit service active | Agent process exists, not a connected tunnel |
| Playit dashboard connected | Provider reports a connection; verify assigned endpoint |
| External UDP/client test | A tested path worked from that network at that time |
| Backend checker online | Registration evidence at the time of the query |
| PC successfully joins and loads world | End-to-end gameplay evidence |

There is no reliable generic UDP "port open" test. TCP port checkers do not test
UDP. The CLI leaves public reachability and registration at WARN/Unknown. Optionally
consult [JoeJoeTV's checker](https://astroservercheck.joejoetv.de/) manually; no
undocumented API, stale log string or guessed backend response is treated as proof.

## Managed Playit migration

`astroneer playit configure` discovers current enabled tunnel endpoints through
local IPC, resolves hostnames to IPv4 candidates, and requires dashboard confirmation
of Custom UDP/Proxy Protocol None. It never runs ipify or changes the CT address.
It rejects mismatched ports; set the local origin to 127.0.0.1:EXTERNAL_PORT before
retrying. An explicitly supplied dashboard IPv4/port must still agree with a current
local origin and cannot override a known conflicting allocation.

The Playit agent shares CT 105's network namespace with the game via Docker host
networking. 127.0.0.1 is the guest's loopback; it is not the Proxmox host. Game-owned
UDP readiness and endpoint/configuration match are local evidence. IPC lifecycle
and tunnel listing do not prove relay transport connectivity; keep those checks
separate from a real external Steam join. No inbound router rule is requested.
