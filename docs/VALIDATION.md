# Validation and release gates

Development environment: macOS, Python 3.14. No Proxmox host/target CT was supplied,
no LXC was created and no game, Docker image or Playit tunnel was executed.

Executed locally:

- `python3 -m unittest discover -s tests -v`: 56 offline tests passed after the image-selection fix.
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

## Image selection regression validation

The reported PVE 9.0.3 / Debian 12 deployment stopped before CT creation because
manual image-digest input failed validation. This is evidence of wizard execution,
not of a successful guest/game deployment. The revised wizard resolves and verifies
registry metadata itself and requires explicit approval. Tests use synthetic
manifest/config bytes and compute fixture hashes; they contain no fabricated release
pins. Coverage includes multiarch selection, digest/header/config/size tampering,
wrong/missing architecture, malformed metadata, network errors, response bounds,
HTTPS redirects/token handling, approval refusal, the default wizard, all network
modes and guest Docker inspect matching.

Local real-registry lookup: blocked by DNS. Local Docker info: socket permission
denied. No build, pull, new CT, live host access or real game test was performed.
See IMAGES.md for the distinction between documented publication and current live
registry/artifact availability. The newly selected image still needs real-host
acceptance testing; no reproducible source-build claim is made.
