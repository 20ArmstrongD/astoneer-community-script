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
    'wait -n' | 'builtin wait "$@"') stage=runtime-child-exit ;;
  esac
  # No BASH_COMMAND dump or xtrace: arguments could contain credentials.
  printf 'ASTRONEER_STARTUP_FAILURE status=%s source=%s line=%s stage=%s\n' \
    "$result" "$origin" "$line" "$stage" >&2
  return "$result"
}

# Only intercept the known runtime handoff. Leave all earlier upstream setup
# commands and non-runtime wait calls intact. Polling plus explicit wait PID
# also handles children that exited before this function was entered.
astro_children=()
astro_stop_children() {
  local child
  for child in "${astro_children[@]}"; do
    kill -TERM "$child" 2>/dev/null || true
  done
}

wait() {
  local game_pid=${p1:-} backup_pid=${p2:-}
  if [[ $# -eq 1 && $1 == -n && -n $game_pid && -n $backup_pid ]]; then
    local monitor finished= result=0 role=unknown child running
    python3 "$astro_runtime" watch --port "${ASTRO_SERVER_PORT:-8777}" &
    monitor=$!
    astro_children=("$game_pid" "$backup_pid" "$monitor")
    while [[ -z $finished ]]; do
      running=$(jobs -pr)
      for child in "${astro_children[@]}"; do
        # kill -0 alone can still succeed for a completed/zombie child.
        if [[ $'\n'$running$'\n' != *$'\n'"$child"$'\n'* ]] || ! kill -0 "$child" 2>/dev/null; then
          finished=$child
          # Waiting inside a conditional captures zero/nonzero without ERR traps.
          if builtin wait "$child"; then
            result=0
          else
            result=$?
          fi
          break
        fi
      done
      if [[ -z $finished ]]; then
        sleep 1
      fi
    done
    case "$finished" in
      "$game_pid") role=game-launcher ;;
      "$backup_pid") role=backup-helper ;;
      "$monitor") role=game-watchdog ;;
    esac
    printf 'ASTRONEER_CHILD_EXIT role=%s status=%s\n' "$role" "$result" >&2
    astro_stop_children
    # Even a clean launcher/helper exit is an unexpected end of this long-lived
    # service. Do not enter upstream's final wait with a lone backup process.
    if [[ $result -eq 0 ]]; then
      result=1
    fi
    printf 'ASTRONEER_STARTUP_FAILURE status=%s source=observer line=%s stage=%s-exit\n' \
      "$result" "$LINENO" "$role" >&2
    trap - ERR
    return "$result"
  fi
  builtin wait "$@"
}

trap 'astro_failure "$?" "$LINENO" "$BASH_COMMAND" "${BASH_SOURCE[0]}"' ERR
trap 'astro_stop_children; exit 143' TERM
trap 'astro_stop_children; exit 130' INT
trap 'astro_result=$?; astro_stop_children; printf "ASTRONEER_ENTRYPOINT_EXIT status=%s\n" "$astro_result" >&2' EXIT
# Send Wine errors/SEH to the already bounded Docker stdout/stderr log sink.
# Native PROTON_LOG writes unbounded files and can dump more diagnostic metadata.
# Do not change fsync/esync, the Wine prefix, machine-id or runtime libraries.
export PROTON_LOG=0
export WINEDEBUG="${WINEDEBUG:-+timestamp,+pid,+tid,err+all,warn+seh}"
astro_entrypoint=${1:-/usr/bin/entrypoint.sh}
astro_runtime=${2:-/run/astroneer-runtime.py}
if [[ ! -r $astro_entrypoint ]]; then
  printf 'ASTRONEER_OBSERVER_ERROR upstream entrypoint is not readable\n' >&2
  exit 1
fi
if [[ $# -eq 0 ]]; then
  printf 'ASTRONEER_RUNTIME_AUDIT '
  python3 "$astro_runtime" audit
fi
# shellcheck source=/dev/null
source "$astro_entrypoint"
