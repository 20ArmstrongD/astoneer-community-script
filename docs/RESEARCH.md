# Research and provenance

Inspected through the browser on 2026-11-02. Browser responses can be cached;
exact branch HEADs and artifact correspondence could not be independently checked.
Shell DNS/network access failed for GitHub API requests. No source was executed.
Do not infer an audited supply chain from this research.

| Source | Observation | Version/activity confidence |
| --- | --- | --- |
| [ProxmoxVE](https://github.com/community-scripts/ProxmoxVE) | MIT, ct/install application tree; Docker script uses separate core | Current browser-visible source; full HEAD not verified |
| [DevScripts](https://github.com/community-scripts/DevScripts) | MIT; new scripts enter here before production | Browser README describes current development process |
| [core](https://github.com/community-scripts/core) | MIT shared engine, UI, backend and LXC functions | Visible commit history includes August 2026; current HEAD unavailable |
| [Docker Astroneer](https://github.com/barumel/docker-astroneer-server) | develop branch; no GitHub releases visible | README describes 3.x encryption change; exact published tag/digest unknown |
| [Dockerfile](https://github.com/barumel/docker-astroneer-server/blob/develop/Dockerfile) | amd64 Debian trixie, GE-Proton 10-27, steam user | Source observation; not proof of Docker Hub image contents |
| [AstroTuxLauncher release](https://github.com/JoeJoeTV/AstroTuxLauncher/releases/tag/1.1.13) | Browser latest release resolves to 1.1.13; GPL-3.0 | Browser reports May 14, year not established from that page |
| [Playit release](https://github.com/playit-cloud/playit-agent/releases/tag/v1.2.0) | Browser latest resolves to v1.2.0, Oct 29 | Verify platform image provenance independently |
| [Official server guide](https://astroneer.space/dedicatedserver/) | IPv4 server setup and platform restrictions | Policy evidence; does not certify Linux implementations |

Docker project's LICENSE could not be retrieved through the browser. Its exact
license and image redistribution obligations remain a release gate; the repository
only references the image and contains original code. Playit component license
inventory is also pending. No third-party source has been copied into this project.

[Steam install script](https://github.com/barumel/docker-astroneer-server/blob/develop/files/tmp/install.txt)
requests Windows 64-bit app 728470 using anonymous login and validation. This wrapper
uses that implementation, rather than assuming Linux game depots exist.

[Configuration implementation](https://github.com/barumel/docker-astroneer-server/blob/develop/files/srv/src/lib/Config.js)
reads `ASTRO_SERVER_NAME`, `ASTRO_SERVER_OWNER_NAME`, `ASTRO_SERVER_PASSWORD`,
`ASTRO_SERVER_PORT`, `ASTRO_SERVER_PUBLIC_IP` and
`ASTRO_SERVER_DISABLE_ENCRYPTION`. It writes `Engine.ini` and
`AstroServerSettings.ini` under `Astro/Saved/Config/WindowsServer`.
Explicit IPv4 overrides domain lookup and ipify. Only one port is configured;
no separate advertised-port variable was found. Differing public/local ports are
therefore rejected conservatively, not declared impossible for every game version.

[Playit upstream](https://github.com/playit-cloud/playit-agent) supports a Linux
Docker agent with a read-only secret file and host networking. The optional service
uses that interface with a caller-reviewed digest, rather than installing an
unverified repository key or a downloaded executable without checksums.

Proxmox's online LXC manual returned HTTP 403 here. PVE 8/9 and Debian 12/13 remain
targets pending the deployment matrix; neither version combination is certified.
A public release must record full source SHAs, verified image digests, licenses,
Debian package/runtime versions and real-host results. Examples intentionally reject
missing digests. This is safer than inventing a functioning release pin.

## Image-selection follow-up

The manual-digest blocker and updated Docker Hub/GHCR lookup protocol are documented
in [IMAGES.md](IMAGES.md). That follow-up supersedes the earlier manual-selection
instructions; exact current registry digests are still discovered at runtime rather
than fabricated in this repository.
