#!/usr/bin/env bash
# In-guest repair only: no apt upgrade, image pull, game install or CT operations.
set -euo pipefail
[[ ${EUID} -eq 0 ]] || {
  echo 'Run inside the existing Debian LXC as root' >&2
  exit 1
}
[[ $# -eq 0 || ($# -eq 1 && $1 == --start) ]] || {
  echo 'Usage: repair.sh [--start]' >&2
  exit 2
}
[[ -f /etc/astroneer/config.json && -f /etc/astroneer/server.env ]] || {
  echo 'Existing Astroneer config.json/server.env required; repair will not generate replacements' >&2
  exit 1
}
# shellcheck source=/dev/null
source /etc/os-release
[[ ${ID:-} == debian && ${VERSION_ID:-} =~ ^(12|13)$ && $(uname -m) == x86_64 ]] || {
  echo 'Requires the existing amd64 Debian 12/13 guest' >&2
  exit 1
}
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# End the old restart loop before changing managed code. Leave forensic container
# and logs intact until the operator explicitly starts the repaired unit.
systemctl stop astroneer
install -d -m 755 /usr/local/lib/astroneer
install -m 755 "$ROOT/scripts/entrypoint-observer.sh" /usr/local/lib/astroneer/entrypoint-observer.sh
install -m 644 "$ROOT/scripts/runtime.py" /usr/local/lib/astroneer/runtime.py
install -m 644 "$ROOT/scripts/images.py" /usr/local/lib/astroneer/images.py
install -m 644 "$ROOT/scripts/network.py" /usr/local/lib/astroneer/network.py
install -m 644 "$ROOT/scripts/playit.py" /usr/local/lib/astroneer/playit.py
install -m 644 "$ROOT/scripts/manage.py" /usr/local/lib/astroneer/manage.py
install -m 755 "$ROOT/scripts/astroneer" /usr/local/bin/astroneer
ln -sfn /usr/local/bin/astroneer /usr/bin/astroneer
/usr/bin/python3 /usr/local/lib/astroneer/manage.py repair "$@"
