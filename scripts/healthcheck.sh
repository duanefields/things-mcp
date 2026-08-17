#!/usr/bin/env bash
#
# Check a running things-mcp server and optionally report to a dead-man's-switch
# service such as healthchecks.io.
#
# Run it from cron or a LaunchAgent on the machine hosting the server. Reporting
# outward matters: a monitor running on the same machine cannot tell you that the
# machine is gone, whereas a service expecting a regular ping can.
#
# Configuration comes from ~/.things-mcp/check.env, if it exists:
#
#   HEALTH_URL=http://127.0.0.1:18789/health
#   PING_URL=https://hc-ping.com/your-uuid-here
#   EXPECTED_PYTHON=/Users/you/.local/share/uv/python/cpython-3.12.13-.../bin/python3.12
#   VENV_PYTHON=/Users/you/Code/things-mcp/.venv/bin/python
#   MAX_WAL_AGE=43200
#
# Everything is optional except HEALTH_URL. Without PING_URL it just prints its
# findings and exits non-zero on a problem, which is useful for running by hand.

set -uo pipefail

CONFIG="${THINGS_MCP_CHECK_ENV:-$HOME/.things-mcp/check.env}"
# shellcheck source=/dev/null
[[ -f "$CONFIG" ]] && source "$CONFIG"

HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/health}"
PING_URL="${PING_URL:-}"
EXPECTED_PYTHON="${EXPECTED_PYTHON:-}"
VENV_PYTHON="${VENV_PYTHON:-}"
# Generous by default. The write-ahead log is only touched when something
# changes, so a quiet night is not a fault -- this is meant to catch sync being
# genuinely dead, not inactivity.
MAX_WAL_AGE="${MAX_WAL_AGE:-43200}"

problems=()
report=""

ping_hc() {
  [[ -z "$PING_URL" ]] && return 0
  curl -fsS -m 10 --retry 3 --data-raw "$2" "${PING_URL}${1}" >/dev/null 2>&1 || true
}

ping_hc "/start" ""

# --- is the server answering, and does it think it is healthy? ---------------
# A timeout here is meaningful: the documented failure mode on macOS is a
# privacy prompt that hangs the process rather than crashing it.
body=$(curl -fsS -m 15 "$HEALTH_URL" 2>/dev/null)
if [[ -z "$body" ]]; then
  problems+=("no response from $HEALTH_URL (down, or hung on a permission prompt)")
else
  read -r status running wal python <<<"$(printf '%s' "$body" | /usr/bin/python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    print("unparseable ? ? ?"); raise SystemExit
print(d.get("status"), d.get("things_running"), d.get("database_wal_age_seconds"), d.get("python_version"))
')"
  report+="status=$status things_running=$running wal_age=${wal}s python=$python"

  [[ "$status" != "ok" ]] && problems+=("health status is '$status'")
  [[ "$running" != "True" && "$running" != "true" ]] && problems+=("Things 3 is not running; writes will vanish")

  if [[ "$wal" != "None" && "$wal" != "?" ]]; then
    if (( $(printf '%.0f' "$wal") > MAX_WAL_AGE )); then
      problems+=("database untouched for ${wal}s (limit ${MAX_WAL_AGE}s); sync may be dead")
    fi
  fi
fi

# --- has the interpreter moved out from under its privacy approval? ----------
# macOS grants Full Disk Access against a path, and tool-managed interpreters
# live at version-stamped paths. An upgrade silently revokes the grant, and the
# service then hangs on its next restart with no error anywhere. Catching the
# move is the only warning available before that happens.
if [[ -n "$EXPECTED_PYTHON" && -n "$VENV_PYTHON" ]]; then
  actual=$(/usr/bin/python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$VENV_PYTHON" 2>/dev/null)
  if [[ -z "$actual" ]]; then
    problems+=("cannot resolve $VENV_PYTHON")
  elif [[ "$actual" != "$EXPECTED_PYTHON" ]]; then
    problems+=("interpreter moved to $actual (approval was granted to $EXPECTED_PYTHON); re-grant Full Disk Access and update EXPECTED_PYTHON")
  fi
fi

# --- report ------------------------------------------------------------------
now=$(date '+%Y-%m-%d %H:%M:%S')

if (( ${#problems[@]} )); then
  msg="things-mcp check FAILED"$'\n'"$report"$'\n'
  for p in "${problems[@]}"; do msg+="- $p"$'\n'; done
  printf '[%s] %s' "$now" "$msg"
  ping_hc "/fail" "$msg"
  exit 1
fi

# Logged on every run, not just failures, so the log doubles as a record of how
# fresh the database has been staying.
printf '[%s] things-mcp OK %s\n' "$now" "$report"
ping_hc "" "OK $report"
