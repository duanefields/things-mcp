# Notes for the upstream maintainer

This fork contains two independent sets of changes. They are separable, and the second is
considerably more opinionated than the first, so they are described apart rather than offered as a
single lump.

Every change is additive. A checkout with no new environment variables set behaves as it always
has: stdio transport, no authentication.

## Group 1 — improvements to the existing tools

These have nothing to do with remote hosting and apply equally to a plain stdio setup. If only one
group is of interest, it is probably this one.

### Create tools return the new item's ID

`add_todo`, `add_todos` and `add_project` return `{id, id_resolved, title}` as structured content.
Previously they returned a fixed sentence, so the only way to act on something you had just created
was to search for it by title. The ID can now be passed to `update_todo`, `show_item`,
`bulk_update_todos`, or used as the `list_id` of a follow-up create.

The Things URL scheme accepts no caller-supplied ID and reports nothing back, so the ID is recovered
by watching the database. Two details matter:

- **Matching is by identity, not time.** The UUIDs of existing items with the same titles are
  recorded before dispatch, and only UUIDs absent from that set count as new. The creation timestamp
  has one-second resolution, far too coarse for a write that lands in roughly half that.
- **The scan is bounded by recency and status.** Querying every status pulls the whole logbook,
  which on a mature database was 37k rows and 1.8s — longer on its own than the entire confirmation
  budget. Restricting to incomplete items created within a day makes the scan bounded by how many
  items were created today rather than by the size of the backlog.

Measured dispatch-to-visible latency was 495–568ms on a Mac mini and 413–1029ms on a laptop, every
attempt resolving. `wait_ms` controls the budget per call; `THINGS_MCP_CREATE_WAIT_MS` sets the
default, and `0` restores the previous fire-and-forget timing for anyone who does not want to pay
roughly half a second per create.

A null ID always means the lookup timed out, never that the write failed, and both the text and the
`id_resolved` flag say so, so a caller is not tempted to create the item twice.

### `add_todos`, a batch create that preserves order

This fixes a real ordering defect rather than merely being faster. Creating todos one at a time
inserts each at the top of the Inbox, so a sequence of single creates ends up **reversed**. Measured:

| Destination | Method | Sent | Reads back |
| :---------- | :--------- | :------ | :------------- |
| Inbox | sequential | 1, 2, 3 | **3, 2, 1** |
| Inbox | batch | 1, 2, 3 | 1, 2, 3 |
| Project | sequential | 1, 2, 3 | 1, 2, 3 |
| Project | batch | 1, 2, 3 | 1, 2, 3 |

A single `things:///json` payload preserves its order in both destinations. One confirmation wait
covers the whole batch, so twenty todos resolve in about the time one create takes rather than
twenty times that.

There is no way to reorder an existing list. Things declares a hidden
`_private_experimental_ reorder to dos in` AppleScript command, but every invocation of it returned
success and changed nothing, so ordering has to be established at creation time.

### `add_project(items=...)`, the only way to create a heading

Things has no way to add a heading to an existing project — not through the URL scheme, and
not through AppleScript, whose dictionary has no heading class at all. A heading can only be
created as part of a project create, nested in `attributes.items`. So a project's structure
is fixed at the moment it is created, and before this there was no way to express it:
`todos` took a flat list of titles.

`items` accepts headings and todos interleaved, in order, and returns an id for each. Item
ids are read out of the created project rather than matched by title, which is exact.

Two behaviors found by testing, both of which the tool now enforces or documents:

- A nested todo carrying `checklist-items` makes Things reject the **whole** project, and
  it reports this with a modal dialog rather than a silent failure. Since a tool sits
  between a model and that payload, this is validated before dispatch — the difference
  between an error in the transcript and a dialog on an unattended machine's screen.
- Tags that do not already exist are silently dropped. General Things behavior, but worth
  knowing when a model invents tag names.

### Relevant commits

```text
8a6c884  return IDs from create tools and add a batch create that keeps its order
7cc1935  make the create confirmation wait configurable
02dd475  bound the ID lookup by creation date, not by backlog size
2a6b02f  note the one case ID matching cannot disambiguate
dcfe464  let add_project build a project's structure with headings
```

## Group 2 — HTTP transport, authentication, and hosting

Only relevant to running the server as a network service. Reasonable to decline wholesale.

### Authentication for the HTTP transport

`THINGS_MCP_AUTH` defaults to `none`, leaving existing behavior untouched. Setting it to `password`
runs a self-contained OAuth 2.1 authorization server whose single credential is a shared password,
with no third-party identity provider.

The motivation is narrow: a remote MCP client is handed a URL and nothing else — no field for an API
key or a custom header — so discovering OAuth is the only way it can authenticate at all.

Very little OAuth is implemented here. FastMCP and the MCP SDK already provide dynamic client
registration, the discovery endpoints, PKCE verification, redirect validation and the `401`
challenge. What this adds is a `/login` interstitial and a constant-time password check, plus
persistence of registered clients and tokens so a restart does not force every client to
re-authorize. The interstitial mirrors the shape FastMCP's own `OAuthProxy` uses for consent.

Verified end to end against a Claude custom connector.

### Stateless HTTP by default

The HTTP transport now runs stateless -- a fresh transport per request -- with
`THINGS_MCP_STATELESS=false` to restore session handling.

Sessions are the wrong model for a remote client. Requests arrive from a pool of addresses, and one
landing from a different address than the one that opened the session is rejected with a `400`.
Observed against a Claude connector: it recovered by reconnecting, repeatedly, until it stopped
recovering and every tool call failed. Nothing in this server needs session state -- no
subscriptions, no server-initiated messages.

### Health endpoint

`GET /health`, unauthenticated, reporting whether Things 3 is running, how long ago the database was
written, the outcome of the last write dispatch, and the interpreter version. This one is arguably
Group 1: the failures it surfaces are silent in any deployment. If Things is not running, writes are
dispatched into nothing and appear to succeed.

### Deployment material

`docs/deployment-macos.md` and `scripts/`. Entirely optional, and specific to running on macOS as a
LaunchAgent. Worth a look only for the privacy-permission problem it documents, which hangs the
process rather than failing it and is genuinely hard to diagnose from scratch.

### Relevant commits

```text
54aea80  add optional password-protected OAuth for the HTTP transport
793e132  add a health endpoint and record write-dispatch outcomes
12d67e0  document HTTP auth and the health endpoint, and record sync measurements
8a805df  document running as a macOS service, and report the interpreter version
3cb3619  document the Apple Events prompt that area tools trigger
58e0bc4  add a health check script for monitoring a deployed server
36b40ce  turn the staleness check off by default
5badf8c  drop the health check's dependency on /usr/bin/python3
f24354b  add a self-update script for a deployed host
349eb0f  do not let untracked files block a deploy
```

## Testing

273 tests pass, 99 of them new. The existing suite is untouched and still green.

New tests cover the OAuth provider including its failure paths, the health endpoint, ID resolution
including timeout and duplicate-title behavior, batch payload construction, and the
heading-relative index rule that project ordering depends on. Note that the suite
imports tool functions directly and awaits them rather than going through the MCP protocol, so
nothing exercises transport selection; the HTTP and auth paths were verified against a live
deployment instead.

## One thing worth knowing regardless

Things shipped a schema change with the "complete a repeating task early" feature. It is
**index-only** — two indexes added, one dropped, no columns altered — so no read code is affected.
Completing early reuses the ordinary completed status rather than introducing a new value; it
materializes the next occurrence and completes it in the same instant. Verified against a live beta
database.
