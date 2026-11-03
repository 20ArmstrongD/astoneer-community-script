# Validation and release gates

Development environment: macOS, Python 3.14. No Proxmox host/target CT was supplied,
no LXC was created and no game, Docker image or Playit tunnel was executed.

Executed locally:

- `python3 -m unittest discover -s tests -v`: 28 offline tests passed.
- `python3 -m py_compile scripts/*.py`: passed.
- `bash -n install.sh install/astroneer-install.sh scripts/astroneer`: passed.

ShellCheck and shfmt are not installed in this environment. Restricted shell DNS
prevented fetching tooling. Their execution is configured in GitHub Actions, but
that workflow has not run here. No claim of lint or CI success is made.

Tests cover validation, argument rejection, environment generation, Proxmox create
arguments, systemd command routing, update backup ordering, stopped-service recovery,
repeated provision behavior, partial failure, missing dependency errors, archive
roundtrip/checksum/traversal/link safety, retention and diagnostic classification.
Mocks do not establish correctness of Docker, systemd, pct or SteamCMD integration.

Public release blockers:

- Record reviewed full source SHAs and image digests; reconcile source/image contents.
- Resolve Docker image and Playit license inventories before redistribution decisions.
- Run ShellCheck/shfmt and CI on Debian Python 3.11 and 3.13 as well as current Python.
- Complete INSTALLATION's PVE 8/9 × Debian 12/13 real-host matrix with representative
  backing filesystems, kernel and AppArmor versions.
- Verify SteamCMD seeding, initial game setup, non-root UID permissions, dropped
  capabilities, encrypted PC join, autosave, graceful stop, reboot, update and restore.
- Verify actual Playit allocation, agent secret-file UID access, connectivity,
  backend registration and external PC join. Console testing is separate and optional.
- Review log-redaction tradeoffs and game-file schema evolution; add tested diagnostics
  for registration only if a stable supported source becomes available.
- For an upstream contribution, adapt to the current core wizard and submit to
  DevScripts with its required metadata and real-host results. This standalone
  repository is not a ready-to-merge Community Scripts PR.
