# Publisher image selection and verification

## Registry research

Upstream's [Compose file](https://github.com/barumel/docker-astroneer-server/blob/develop/docker-compose.yml)
references `barumel/docker-astroneer-server:latest`, which is the Docker Hub repository
[barumel/docker-astroneer-server](https://hub.docker.com/r/barumel/docker-astroneer-server).
This is a community publisher image, not a Docker Official Image or a System Era image.
The upstream [Dockerfile](https://github.com/barumel/docker-astroneer-server/blob/develop/Dockerfile)
uses amd64 Debian trixie and GE-Proton 10-27 in the browser-visible source.
Source contents do not prove the contents of any published tag.

The workflow directory lists docker-publish.yml, but fetching its content and Docker
Hub's live tag metadata failed in this development environment. Consequently no current
published digest, image signature, source-to-image correspondence or successful pull
is claimed here. The wizard checks live availability at installation time and fails
closed if the documented repository does not serve a suitable image.

The optional agent uses the official Playit project's documented GHCR repository:
[upstream Linux Docker instructions](https://github.com/playit-cloud/playit-agent).
Its tag is also resolved and approved; agent credentials are never used for registry lookup.

## Discovery without host Docker

`scripts/images.py` uses only Python's standard library. It requests anonymous,
repository-scoped pull tokens from fixed Docker Hub/GHCR endpoints over verified HTTPS.
Only the two approved repositories are supported. The operator enters a tag or
sha256 reference, never an arbitrary registry URL. Metadata responses are bounded and
time-limited. Redirects must remain HTTPS; cross-host redirects lose Authorization.
Tokens are transient and are neither printed nor written into provenance.

For a manifest list/image index the resolver requires exactly one linux/amd64 entry,
checks its descriptor hash and size and pins the platform manifest. For a single
manifest it hashes the returned bytes directly. Docker-Content-Digest, when present,
must match. The image configuration blob's SHA-256 and size must match its descriptor,
and its OS/architecture must independently confirm linux/amd64. Unsupported schemas,
missing platforms, malformed metadata or any mismatch abort selection.

These checks follow the [Distribution manifest specification](https://distribution.github.io/distribution/spec/manifest-v2-2/).
They verify content identity under TLS trust, not publisher authenticity, signed
attestations, vulnerability status or an exact upstream commit. The wizard states
that distinction and displays source URL, registry, requested tag, immutable reference,
platform, config hash, lookup timestamp, Proton version when supplied, and publisher
revision label when supplied. Labels are untrusted metadata, not attestations.
It requires typing APPROVE for each image before CT creation. CREATE remains a
separate confirmation of the new guest's resource/network settings.

The new guest pulls only the approved platform digest. Before executing the image,
it independently checks Docker's image OS/architecture, RepoDigests and configuration
ID against the approved record. Docker validates image/layer content during pull.
Initial discovery and installed-game inspection are saved privately in
`/etc/astroneer/image-provenance.json`. Keep this record alongside backups when
reporting installation issues. It describes initial selection; later CLI image
updates still require an explicit immutable reference and do not rewrite that history.

Required host egress: HTTPS to auth.docker.io, registry-1.docker.io and registry blob
CDNs; Playit also needs ghcr.io and its CDNs. Guest pulls need registry/layer endpoints,
and upstream startup needs Steam's download/backend endpoints. DNS, certificate
verification, rate limits and availability can stop lookup/pull; there is no HTTP,
privileged-container or floating-tag fallback. The Proxmox host never installs or
runs Docker as part of this workflow.

## Build and LXC scope

A source-build fallback is not selected because upstream documents a published image
and the revised installer can discover its digest directly. If that registry is not
available, installation stops; it does not silently execute a source checkout.
A future source-build mode would need a reviewed full commit plus locked base image,
apt/npm dependencies, Proton and Steam downloads. Upstream's current Dockerfile has
mutable downloads, so pinning its Git commit alone would not make a reproducible build.

This fix verifies platform metadata in offline tests, and enforces amd64 on the host,
Debian 12/13 amd64 in the guest, unprivileged LXC and existing nesting/keyctl policy.
It does **not** establish that this image builds or runs in an unprivileged PVE LXC.
The available Docker socket denied access, shell DNS to the registry failed, and no
live host was authorized. Real pull, first-run, encrypted PC join and reboot checks
remain the acceptance procedure in INSTALLATION.md. Never change the CT to privileged
or disable AppArmor to work around a runtime failure.
