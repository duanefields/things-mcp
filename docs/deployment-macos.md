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

## Checking on it

```bash
launchctl list | grep things-mcp        # pid, last exit code
curl -s localhost:18789/health          # liveness, staleness, last write, python version
tail -f ~/.things-mcp/server.log
```

A hang looks like: a live PID, nothing on the port, and an empty log. That is the privacy prompt
above, not a crash.
