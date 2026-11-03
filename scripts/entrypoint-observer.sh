#!/usr/bin/env bash
# Observe the pinned upstream entrypoint; never ignore/retry a failed command.
set -E

astro_failure() {
  local result=$1 line=$2 invocation=$3 origin=${4##*/} stage=unclassified
  case "$invocation" in
    'bash /steamcmd/steamcmd.sh +quit') stage=steamcmd-self-update ;;
    'bash /steamcmd/steamcmd.sh +runscript /tmp/install.txt') stage=steamcmd-game-update ;;
    '/geproton/proton run /astroneer/Astro/Binaries/Win64/AstroServer-Win64-Shipping.exe') stage=proton-initialization ;;
    'node /srv/src/restore.js') stage=upstream-restore ;;
    'node /srv/src/initConfig.js') stage=configuration ;;
    'wait -n') stage=runtime-child-exit ;;
  esac
  # No BASH_COMMAND dump or xtrace: arguments could contain credentials.
  printf 'ASTRONEER_STARTUP_FAILURE status=%s source=%s line=%s stage=%s\n' \
    "$result" "$origin" "$line" "$stage" >&2
  return "$result"
}

trap 'astro_failure "$?" "$LINENO" "$BASH_COMMAND" "${BASH_SOURCE[0]}"' ERR
trap 'astro_result=$?; printf "ASTRONEER_ENTRYPOINT_EXIT status=%s\n" "$astro_result" >&2' EXIT
# An optional path supports offline fixture testing. The installed service passes
# no override and always sources the entrypoint from its pinned Docker image.
astro_entrypoint=${1:-/usr/bin/entrypoint.sh}
if [[ ! -r $astro_entrypoint ]]; then
  printf 'ASTRONEER_OBSERVER_ERROR upstream entrypoint is not readable\n' >&2
  exit 1
fi
# shellcheck source=/dev/null
source "$astro_entrypoint"
