# Client compatibility

Labels: **Verified** = supported by the cited evidence, with its scope stated;
**Community reported** = upstream/community claim without this project's testing;
**Experimental** = implemented or plausible but awaiting end-to-end validation;
**Unsupported** = outside documented policy/support; **Unknown** = insufficient evidence.
No client connection has been tested by this project.

| Client/path | Label | Evidence and scope |
| --- | --- | --- |
| Steam PC to official Windows self-hosted server | Verified | Official guide documents home hosting through Steam; documentation evidence only |
| Steam PC to this Docker/Proton LXC | Experimental | Primary target; no live CT or game join test |
| Encrypted traffic on modern Proton image | Community reported | Docker upstream claims support; must verify the pinned artifact |
| Xbox to this self-hosted server | Unsupported | Official policy restricts Xbox to authenticated/preferred servers |
| Xbox/PC via approved provider | Verified | Official guide states preferred servers enable this; provider/platform test not performed here |
| PlayStation to general dedicated servers | Verified | Official guide says PS4 can connect to any dedicated server; documentation scope |
| PlayStation to this Proton/LXC/Playit stack | Unknown | Policy permission is not runtime evidence; no console test |
| Other PlayStation generations/store builds | Unknown | No specific verified evidence for this project |
| Playit UDP to this server | Experimental | Assigned endpoint and registration need independent testing |
| LAN-only clients without public registration | Experimental | Runtime installation possible; functional joins not established |
| Old Wine with encryption disabled | Community reported | Launcher documents this limitation; not implemented by this installer |

[Official dedicated-server policy](https://astroneer.space/dedicatedserver/) separates
home hosting from trusted-provider crossplay. It forbids attempts to circumvent
backend restrictions. Encryption support does not grant provider authentication.
[Docker upstream](https://github.com/barumel/docker-astroneer-server) describes its
modern Proton encryption behavior, but that claim does not certify console support.
No client encryption changes, authentication workarounds or provider impersonation
are offered. Test Steam PC first; console testing is optional separate research.
