# Changelog

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
