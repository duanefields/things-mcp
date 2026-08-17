# Running as a background service on macOS

How to keep the HTTP server running on a Mac that nobody is sitting at, and the two macOS
behaviors that will otherwise cost you an afternoon.

## It must be a LaunchAgent, not a LaunchDaemon

Writes are dispatched by shelling out to `open`, which needs a logged-in GUI session. A root
LaunchDaemon has no session, so every write fails silently. Install into
`~/Library/LaunchAgents/`, and make sure the machine auto-logs in so the session exists after a
reboot.

## Invoke the interpreter directly, not `uv run`

`uv run` spawns the interpreter as a child, so launchd ends up supervising the wrapper. Killing the
job leaves the real server holding the port. Point `ProgramArguments` at the venv's interpreter.

## Template

Save as `~/Library/LaunchAgents/com.example.things-mcp.plist`, replacing the placeholders. It
contains a password, so `chmod 600` it.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.example.things-mcp</string>

  <key>ProgramArguments</key>
  <array>
    <string>/Users/USERNAME/path/to/things-mcp/.venv/bin/python</string>
    <string>-m</string>
    <string>things_mcp</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/USERNAME/path/to/things-mcp</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>THINGS_MCP_TRANSPORT</key><string>http</string>
    <key>THINGS_MCP_HOST</key><string>127.0.0.1</string>
    <key>THINGS_MCP_PORT</key><string>18789</string>
    <key>THINGS_MCP_AUTH</key><string>password</string>
    <key>THINGS_MCP_PASSWORD</key><string>REPLACE-WITH-A-LONG-RANDOM-VALUE</string>
    <key>THINGS_MCP_BASE_URL</key><string>https://things.example.com</string>
    <key>THINGS_MCP_STATE_DIR</key><string>/Users/USERNAME/.things-mcp</string>
    <!-- Without this, Python block-buffers to the log file and it stays empty,
         which makes a startup problem look like total silence. -->
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>

  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/USERNAME/.things-mcp/server.log</string>
  <key>StandardErrorPath</key><string>/Users/USERNAME/.things-mcp/server.log</string>
</dict>
</plist>
```

```bash
chmod 600 ~/Library/LaunchAgents/com.example.things-mcp.plist
launchctl load ~/Library/LaunchAgents/com.example.things-mcp.plist
curl -s localhost:18789/health
```

Bind to `127.0.0.1` and reach it through a tunnel or reverse proxy. Pick an unremarkable port rather
than 8080, which collides with the first thing any other developer tool tries, and stay below 49152
so it cannot clash with an outbound ephemeral socket.

## The privacy prompt that hangs the service

**This is the one that will catch you.** Reading the Things database means reading another
application's group container, which macOS protects. A LaunchAgent has no approval for that, so on
first start the system raises a consent dialog — and if the Mac is headless or unattended, that
dialog sits unanswered and the process **blocks indefinitely inside `open()`**.

It does not crash, does not log, and does not time out. `launchctl list` reports it running with a
healthy PID, the port is never bound, and the log file is empty. With `KeepAlive` set, launchd keeps
restarting it and each attempt stacks another dialog.

Running the same command over SSH works fine, which is thoroughly misleading: your shell inherits an
approval that the LaunchAgent does not have.

**Fix:** grant Full Disk Access to the interpreter, before first launch. System Settings → Privacy &
Security → Full Disk Access → `+`, then `Cmd+Shift+G` in the picker to type the path, since it is
usually hidden. Grant the **resolved** binary, not the venv symlink:

```bash
python3 -c "import os; print(os.path.realpath('.venv/bin/python'))"
```

Clicking Allow on the popup is often not enough. Interpreters installed by `uv` and similar tools
are ad-hoc signed with an empty identifier:

```text
Identifier=-
Signature=adhoc, linker-signed
```

macOS binds approvals to a code-signing identity, and there is nothing here to bind to, so the
prompt returns on every launch and the approval never sticks. An explicitly added Full Disk Access
entry is recorded against the path and does work.

### Why this does not happen when running via uvx

Approval attaches to the responsible process. Launched through `uvx`, the child interpreter inherits
`uv`'s approval, and `uv` lives at a stable path. Exec'ing the interpreter directly makes it
responsible for itself, so it needs its own.

### It will break again when the interpreter is upgraded

Tool-managed interpreters live at version-stamped paths:

```text
~/.local/share/uv/python/cpython-3.12.13-macos-aarch64-none/bin/python3.12
```

Approval is granted against that path. A patch upgrade moves the binary, silently invalidating it,
and the service goes back to hanging on startup with no error anywhere. A `.python-version` holding
only `3.12` permits exactly that upgrade.

`GET /health` reports `python_version` so the change is visible before it bites. Watch it, and
re-grant Full Disk Access after any interpreter upgrade.

## A second prompt, for controlling Things

Most writes go out through the URL scheme, which needs no special permission. Areas are the
exception: Things has no URL commands for them, so `add_area` and `update_area` drive the app with
Apple Events instead. That is a separate protection from file access, and it produces its own
dialog the first time either tool runs:

> "python3.12" wants access to control "Things".

Until it is answered the tool call hangs, the same way startup does. Everything else keeps working,
so this can lie dormant for a long time and then surface the first time someone creates an area.

Trigger it deliberately while you are at the machine — call `add_area` once — rather than letting it
ambush a remote client later.

Unlike Full Disk Access, this one **cannot be granted ahead of time**. System Settings → Privacy &
Security → Automation only lets you toggle pairs macOS has already recorded, so the prompt has to
happen at least once. It does persist across restarts afterwards, ad-hoc signed interpreter and all.

If a grant ever refuses to stick, the workaround is to give the binary a stable identity, since
approvals bind to a code-signing identity and these interpreters ship without one:

```bash
codesign --force --sign - --identifier com.example.things-mcp-python \
  "$(python3 -c "import os;print(os.path.realpath('.venv/bin/python'))")"
```

Re-grant afterwards. Note this is undone by an interpreter upgrade, like the approval itself.

## Monitoring

`scripts/healthcheck.sh` checks a running server and reports to a dead-man's-switch service such as
healthchecks.io. Configure it in `~/.things-mcp/check.env`:

```bash
HEALTH_URL=http://127.0.0.1:18789/health
PING_URL=https://hc-ping.com/your-uuid-here
EXPECTED_PYTHON=/Users/you/.local/share/uv/python/cpython-3.12.13-.../bin/python3.12
VENV_PYTHON=/Users/you/Code/things-mcp/.venv/bin/python
MAX_WAL_AGE=0   # staleness check off; see below
```

```cron
*/10 * * * * /Users/you/Code/things-mcp/scripts/healthcheck.sh >> /Users/you/.things-mcp/check.log 2>&1
```

`chmod 600` the config: the ping URL is a capability, not just an address.

It reports failure on three things:

- **No response.** Either down, or hung on a permission prompt. A timeout is meaningful here, since
  the documented failure mode is a hang rather than a crash.
- **Unhealthy.** Things 3 is not running, so writes are going nowhere.
- **The interpreter moved.** Compares the resolved interpreter against `EXPECTED_PYTHON`. This is
  the early warning for the privacy-approval problem above: an upgrade invalidates the grant, and
  without this the first symptom is the service hanging on its next restart. Re-grant Full Disk
  Access and update `EXPECTED_PYTHON` together.

It can also flag a database that has not been written to recently, but this is **off by default**
(`MAX_WAL_AGE=0`) and deserves care before you enable it. The write-ahead log is only touched when
something changes, so its age cannot tell "sync is dead" apart from "nobody has changed anything" --
a quiet weekend looks exactly like a broken sync. The age is reported on every run regardless, so
let the log show what an ordinary idle stretch looks like for you, then pick a threshold no genuine
absence would reach.

An outward ping is what makes the whole machine being gone detectable. A monitor running on the same
host cannot report its own host's death. Set the expected period to match the cron interval, with a
grace period of two or three intervals.

Every run is logged, not just failures, so the log doubles as a record of how fresh the database has
been staying.

## Deploying by pushing

`scripts/self-update.sh` pulls the tracked branch, syncs dependencies if they moved, and restarts
the service — so a push is a deploy and the host needs no attention. Configure it in
`~/.things-mcp/update.env`:

```bash
REPO_DIR=/Users/you/Code/things-mcp
BRANCH=main
LAUNCH_LABEL=com.example.things-mcp   # omit to skip the restart
PING_URL=https://hc-ping.com/a-different-uuid
```

```cron
*/15 * * * * /Users/you/Code/things-mcp/scripts/self-update.sh >> /Users/you/.things-mcp/update.log 2>&1
```

It exits immediately when the branch has not moved, so a short interval is cheap, and it takes a
lock so a slow run cannot overlap the next. It leaves the checkout alone if the fetch fails or if
tracked files have local modifications — better to skip a deploy than to half-apply one or discard
someone's debugging.

One trap when setting this up: **build the virtualenv where it will finally live.** `uv` records
absolute paths, so a venv created in one directory and then moved leaves the editable install
pointing at the old location, and the service fails with `No module named things_mcp`. Re-run
`uv sync` after any move.

## Checking on it

```bash
launchctl list | grep things-mcp        # pid, last exit code
curl -s localhost:18789/health          # liveness, staleness, last write, python version
tail -f ~/.things-mcp/server.log
```

A hang looks like: a live PID, nothing on the port, and an empty log. That is the privacy prompt
above, not a crash.
