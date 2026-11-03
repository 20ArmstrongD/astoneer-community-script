#!/usr/bin/env bash
set -euo pipefail
[[ ${EUID} -eq 0 ]] || {
  echo 'Run as root' >&2
  exit 1
}
[[ $# -ge 1 && $# -le 2 && -f $1 ]] || {
  echo 'Usage: astroneer-install.sh CONFIG.json [PROVENANCE.json]' >&2
  exit 2
}
# shellcheck source=/dev/null
source /etc/os-release
[[ ${ID:-} == debian && ${VERSION_ID:-} =~ ^(12|13)$ && $(uname -m) == x86_64 ]] || {
  echo 'Requires amd64 Debian 12/13' >&2
  exit 1
}
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends docker.io python3 iproute2 ca-certificates
systemctl enable --now docker
# Refuse to change container security or Docker storage settings on failure.
docker info >/dev/null
install -d -m 700 /etc/astroneer /var/lib/astroneer /var/backups/astroneer
install -d -m 755 /usr/local/lib/astroneer
install -m 755 "$ROOT/scripts/astroneer" /usr/local/bin/astroneer
install -m 644 "$ROOT/scripts/manage.py" /usr/local/lib/astroneer/manage.py
if [[ $# -eq 2 ]]; then
  python3 /usr/local/lib/astroneer/manage.py provision "$1" --provenance "$2"
else
  python3 /usr/local/lib/astroneer/manage.py provision "$1"
fi
