#!/usr/bin/env bash
#
# Pull the latest code and restart the server if anything changed.
#
# Intended for a machine that hosts the server and is not sat in front of, so
# that pushing to the tracked branch is enough to deploy.
#
# Configuration comes from ~/.things-mcp/update.env, if it exists:
#
#   REPO_DIR=/Users/you/Code/things-mcp
#   BRANCH=main
#   LAUNCH_LABEL=com.example.things-mcp   # omit to skip restarting anything
#   PING_URL=https://hc-ping.com/your-uuid-here
#
# Nothing happens when the branch has not moved, so this is cheap to run often.

set -uo pipefail

CONFIG="${THINGS_MCP_UPDATE_ENV:-$HOME/.things-mcp/update.env}"
# shellcheck source=/dev/null
[[ -f "$CONFIG" ]] && source "$CONFIG"

REPO_DIR="${REPO_DIR:-$HOME/Code/things-mcp}"
BRANCH="${BRANCH:-main}"
LAUNCH_LABEL="${LAUNCH_LABEL:-}"
PING_URL="${PING_URL:-}"
UV="${UV:-/opt/homebrew/bin/uv}"

# mkdir is atomic and works without flock, which macOS lacks.
LOCKDIR="/tmp/things-mcp-self-update.lock"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
ping_hc() { [[ -z "$PING_URL" ]] && return 0; curl -fsS -m 10 --retry 3 --data-raw "${2:-}" "${PING_URL}${1}" >/dev/null 2>&1 || true; }

if ! mkdir "$LOCKDIR" 2>/dev/null; then
  log "another run is in progress, skipping"
  exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT

ping_hc "/start"

cd "$REPO_DIR" || { log "no such directory: $REPO_DIR"; ping_hc "/fail" "missing $REPO_DIR"; exit 1; }

before=$(git rev-parse HEAD 2>/dev/null)
if ! out=$(git fetch --quiet origin "$BRANCH" 2>&1); then
  # A failed fetch is usually the network or the forge having a bad day. Leave
  # the checkout alone and let the next run pick it up.
  log "fetch failed, leaving the checkout untouched: $out"
  ping_hc "/fail" "fetch failed: $out"
  exit 1
fi

remote=$(git rev-parse "origin/$BRANCH" 2>/dev/null)
if [[ "$before" == "$remote" ]]; then
  log "already up to date at ${before:0:8}"
  ping_hc "" "no change ${before:0:8}"
  exit 0
fi

# Refuse to clobber local edits: a dirty checkout on a deploy host means someone
# was debugging in place, and throwing that away silently would be rude.
if [[ -n "$(git status --porcelain)" ]]; then
  log "local changes present, refusing to update"
  ping_hc "/fail" "local changes in $REPO_DIR; not updating"
  exit 1
fi

log "updating ${before:0:8} -> ${remote:0:8}"
if ! out=$(git merge --ff-only "origin/$BRANCH" 2>&1); then
  log "fast-forward failed, leaving the checkout alone: $out"
  ping_hc "/fail" "ff-only merge failed: $out"
  exit 1
fi

# Dependencies may have moved with the code.
if ! out=$("$UV" sync 2>&1); then
  log "uv sync failed: $out"
  ping_hc "/fail" "uv sync failed"
  exit 1
fi

if [[ -n "$LAUNCH_LABEL" ]]; then
  plist="$HOME/Library/LaunchAgents/${LAUNCH_LABEL}.plist"
  log "restarting $LAUNCH_LABEL"
  launchctl unload "$plist" 2>/dev/null
  sleep 2
  launchctl load "$plist" 2>/dev/null
fi

log "updated to ${remote:0:8}: $(git log -1 --pretty=%s)"
ping_hc "" "updated to ${remote:0:8}"
