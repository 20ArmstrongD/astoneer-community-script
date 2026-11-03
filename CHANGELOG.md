# Changelog

## 0.1.2 — unreleased

- Enable rotating game Docker json-file logs (10m × 3), redacted CLI log viewing,
  and bounded previous-attempt archives retained across container replacement.
- Observe the pinned upstream entrypoint's failing stage, line and exit status;
  preserve nonzero SteamCMD/Proton/Node/wait results without retries or workarounds.
- Disable automatic game restarts and add explicit failure/state diagnostics.
- Add an idempotent in-guest repair script and CLI command that preserve private
  configuration, image digest, Docker installation and game data.
- Install the CLI in the minimal guest PATH and extend startup regression tests/CI.
- No live CT access or proof of the underlying deployment failure's cause.

## 0.1.1 — unreleased

- Resolve publisher image tags through authenticated HTTPS registry APIs without host Docker.
- Verify manifest/config SHA-256, select linux/amd64, require explicit image approval,
  and retain provenance with guest pull/inspect verification.
- Explain direct, Playit and LAN endpoints separately; validate assigned port equality.
- Add offline registry, approval, network-mode and guest-verification regression tests.
- Live registry pulls and PVE/LXC integration remain untested in this development environment.

## 0.1.0 — unreleased

- Standalone Proxmox host wizard and Debian guest installer.
- Pinned Docker/Proton runtime; private configuration; optional pinned Playit agent.
- Systemd lifecycle, persistent storage, pre-start save/config backups, transactional restore.
- Management CLI, structured diagnostics and mocked offline tests.
- Deployment candidate only: no live Proxmox, game-client, console or tunnel validation.
