# Optional Playit agent — experimental for Astroneer

The agent runs as a separate systemd-managed Linux Docker container in the LXC,
using [upstream's secret-file interface](https://github.com/playit-cloud/playit-agent).
It is not an authentication or console-provider bypass. No Playit account is created
and no tunnel is purchased or provisioned by this installer.

1. Sign into Playit and use the upstream-linked
   [Docker agent wizard](https://playit.gg/account/setup/wizard/new-account/docker/docker-name)
   to associate an agent with your account. Keep its generated secret private.
2. Obtain and review the supported Linux agent image from the official project.
   Record its immutable `ghcr.io/playit-cloud/playit-agent@sha256:...` digest.
   The browser's latest release resolved to v1.2.0 during research; do not assume
   a floating container tag matches a release without checking provenance.
3. Create a **Custom UDP** tunnel in the dashboard. Record its actual public IPv4
   and external port. If only a hostname is displayed, resolve its IPv4 and confirm
   the assignment in the dashboard. Multiple/changing A records require review;
   do not use an arbitrary outbound-IP lookup as the tunnel address.
4. Set local origin to `127.0.0.1:ASSIGNED_EXTERNAL_PORT`, Proxy Protocol None.
   The game shares the guest network namespace with the agent. Set the game's
   local port to that same number; the default 8777 is not guaranteed by Playit.
5. Select playit in the host wizard. Enter that exact assigned IPv4 and external
   port, the same local port, reviewed agent image and hidden agent secret.
   A mismatch fails validation. The agent secret is stored root-only at
   `/etc/astroneer/playit-secret`, mounted read-only; no secret appears in argv.
6. Verify `systemctl status astroneer-playit` and `ss -lun` inside the guest.
   Confirm the agent is connected in the dashboard and its local UDP origin matches
   the listener shown by `astroneer diagnostics`. A running agent is not a connected
   tunnel. Check the current allocation again after provider changes.
7. Verify backend registration independently, then join using the numerical IPv4
   and assigned port from an external Steam PC client. Record all results separately.

The installer validates syntax, global IPv4 and port equality. It cannot authenticate
an endpoint's ownership or automatically query the assigned tunnel allocation without
an independently verified provider API integration. Dashboard comparison is required.
No real tunnel has been tested here. If registration fails, report the path as
incompatible/unverified and use direct IPv4 forwarding or a supported hosting provider.
Changing port equality does not establish UDP forwarding or backend compatibility.

Agent process output is suppressed; use the dashboard for account linking and tunnel
state. Avoid posting agent secrets, account screenshots or raw local game logs.
The agent connects outbound; no inbound agent-management port needs forwarding.
See [official Linux guidance](https://playit.gg/support/run-on-linux/) for supported
host agent alternatives. This project avoids automatically importing a remote apt key.
