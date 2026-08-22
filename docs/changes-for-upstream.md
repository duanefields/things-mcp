# Notes for the upstream maintainer

This fork contains two independent sets of changes. They are separable, and the second is
considerably more opinionated than the first, so they are described apart rather than offered as a
single lump.

Every change is additive, with one deliberate exception noted where it appears: `search_todos` no
longer matches an item by the name of its area. Otherwise a checkout with no new environment
variables set behaves as it always has: stdio transport, no authentication.

## Group 1 — tool improvements, new tools, and two bug fixes

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

A resolved ID is proof the item exists — the row was read out of the database. A null ID proves
nothing either way: the URL scheme never acknowledges a write, so a create that was merely slow and
one that silently failed are indistinguishable. The text says exactly that and tells the caller to
search by title before retrying, which is the only response that risks neither a phantom success nor
a duplicate. An earlier version claimed the item "was almost certainly created"; that wording talked
a caller out of checking, and when a write really was lost the failure was reported as a success.

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

### `get_item`, the read that closes the loop

The create tools return an ID, but no read tool accepted one — you could make something and then only
act on it through write tools, or go hunting for it by title.

`get_item(id)` takes any UUID. `things.get` already resolves todos, projects, areas, headings and
tags, so the tool dispatches on the type it reports to the matching formatter and passes
`include_items` through for the contained items. It reuses the pagination envelope with a
one-element list, so its `structured_content` is the same shape as every other read tool rather than
a second envelope invented for the single-item case.

### `get_deadlines`

`things.deadlines()` already exists in things.py and was not exposed, so answering "what is due, what
is overdue" meant pulling a whole list and filtering client-side.

Incomplete items that have a deadline, earliest first, which puts overdue items at the front.
Projects are included alongside todos — `things.deadlines` returns both, and `get_recent` already
formats mixed types the same way. `within_days` trims to a horizon without dropping overdue items,
since anything past due is still at or below the cutoff. Deadlines are `YYYY-MM-DD` strings, so the
comparison is lexicographic and needs no date parsing.

### Search that ranks, and stops matching on area names

`things.search()` is one `LIKE '%query%'` over `TASK.title`, `TASK.notes` and `AREA.title`. Three
consequences, of which the third is the surprising one:

- `dentist call` finds nothing when the task is `Call dentist`.
- Results arrive in database order, with no notion of a better match.
- Searching an area's name returns every task in that area. `AREA.title` is in the match list, so a
  query of `Work` matches every task that happens to live in the Work area.

`search_todos` now tokenizes the query. The longest token — usually the most selective — still goes
to SQL, so there is exactly one database scan, the same one as before for a single-term query. The
remaining terms are matched in Python over that candidate set, which is what makes word order
irrelevant. Ranking is a small score rather than a sort key: whole word beats prefix beats mid-word,
a title hit outweighs a notes hit, and a contiguous hit on the whole query outranks scattered terms.
Ties keep the order things.py returned.

Matching against title and notes only is what removes the area flood, and it is **the one behavior
change in this fork that is not purely additive**: an item whose only connection to the query is its
area no longer matches at all. `search_advanced(area=...)` remains the way to search by area.

No new dependency. Semantic search was considered and rejected — titles are too short to embed well,
and it would put a model dependency in a server that is otherwise fully offline.

Still not searched, and worth knowing: tags, and checklist-item titles. things.py marks the latter as
an unimplemented TODO inside `make_search_filter` itself.

### `append_notes` and `prepend_notes`

Every notes edit was a destructive full replacement, so appending a line meant read, concatenate,
write back — with a lost update if anything else touched the item in between.

`append-notes` and `prepend-notes` are documented Things URL scheme parameters, so this needed no
AppleScript. Both are threaded through `update_todo` and `update_project` alongside the existing
`notes` parameter, which keeps its replace-everything behavior.

### `add_tag`, and a scripting dictionary that misleads

Tags that do not already exist are silently dropped from every kind of create — the behavior noted
under `add_project(items=…)` above — so an invented tag name just vanishes with no error, and there
was no way to create the tag first. The URL scheme has no `add-tag` command.

AppleScript does, and the reason that was not obvious is worth recording. Things' scripting
dictionary marks the application's `tag` element `access="r"`, which reads as "you cannot create
one". It marks `area` exactly the same way, and `make new area` has always worked. The dictionary is
not the authority here; the check is. `make new tag` works, and a duplicate name returns the existing
tag's id rather than creating a second tag, so the operation is idempotent. The tool reads
`things.tags()` first anyway, so an existing tag is reported as such rather than claimed as a create.

### `trash_item`, and why there is no delete

The server could create items it could not take back — only `completed` and `canceled` existed. This
was built last, because unverified AppleScript plus a destructive operation is the worst combination
to build on speculatively, so it was verified against Things 3 on disposable items first.

What the check found changed the shape of the feature. AppleScript's `delete` and
`move … to list "Trash"` are the same operation: both leave the item in the Trash, recoverable, and a
trashed item is not addressable by AppleScript afterwards at all. Things has no permanent per-item
delete to expose, and `empty trash` empties the whole thing, so it is left out. One tool, not two,
and nothing here destroys data outright.

The tool reads the item first, which gives a precise error for a missing id, recognizes an
already-trashed item that AppleScript could not have addressed anyway, refuses areas — same reasoning
as the absent `delete_area`, since an area takes every project in it — and picks the right
AppleScript class for the type.

### The auth-token lookup could raise

Upstream behavior, not something this fork introduced. `construct_url` called `things.token()` with
no `try`/`except`. That call opens the Things database, so it raises rather than returning `None`
when Things is not installed, the database is locked, or the macOS privacy grant is missing — and
every update operation surfaced a raw traceback through the MCP boundary.

`url_scheme.auth_token()` returns `None` in all of those cases and logs the reason. For `update` and
`update-project`, `construct_url` raises `AuthTokenUnavailable` when there is no usable token, and
the update tools return that message as a plain string. Raising rather than building the URL anyway
matters: Things rejects an update that arrives without an `auth-token`, so the previous code would
have dispatched it and reported success.

### Relevant commits

```text
8a6c884  return IDs from create tools and add a batch create that keeps its order
7cc1935  make the create confirmation wait configurable
02dd475  bound the ID lookup by creation date, not by backlog size
2a6b02f  note the one case ID matching cannot disambiguate
dcfe464  let add_project build a project's structure with headings
abd1b0b  add a get_item tool for reading an item by ID
ce90af9  add a get_deadlines tool
188fe0c  guard the auth-token lookup behind url_scheme.auth_token()
93e41b7  add append_notes and prepend_notes to the update tools
2bcb4de  rank search results and match terms in any order
1a07f91  add a tag creation tool
2e8f97d  add a trash tool for to-dos and projects
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

Because the endpoint is unauthenticated, the dispatch `error` is a summary — exception type and exit
status — and never the exception text. `str(CalledProcessError)` embeds the whole command line, and
a Things URL carries the auth-token along with the title and notes of the item being written.

One bug worth calling out, because it inverted the endpoint's purpose. Reading the WAL age used to
construct a `things.database.Database` just to get at `.filepath`, and that constructor opens SQLite
and asserts on the schema version. A missing, locked or privacy-revoked database raises
`sqlite3.OperationalError`, an old schema raises `AssertionError`, and neither was caught — so
`/health` returned a 500 in exactly the case it exists to detect. It now resolves the path from
`THINGSDB` or `things.database.DEFAULT_FILEPATH`, which removes the SQLite open entirely rather than
catching what it throws.

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
104686e  stop the health endpoint from crashing on an unreachable database
40c9b7f  stop the unauthenticated health endpoint from publishing the auth-token
```

## Testing

355 tests pass, 181 of them new. The existing suite is untouched and still green.

New tests cover the OAuth provider including its failure paths, the health endpoint, ID resolution
including timeout and duplicate-title behavior, batch payload construction, and the
heading-relative index rule that project ordering depends on. Each tool above brings its own:
search ranking and the dropped area match, the single-item read across all five item types, the
deadline horizon, notes append and prepend, tag creation and its idempotence, and every branch of
the trash tool. Transport selection is covered too, including the stateless default. The suite
imports tool functions directly and awaits them rather than going through the MCP protocol, so it
does not exercise a real client session; the HTTP and auth paths were verified against a live
deployment instead.

The suite is hermetic — it mocks things.py wholesale and touches no real Things database — and runs
on every push and pull request via `.github/workflows/tests.yml`, on Ubuntu, against Python 3.12
and 3.13. Ubuntu is the point as much as the convenience: five tests used to pass only on a machine
with a real Things database, and a machine without one is the only place that shows up as a failure.

```text
5414cec  make the test suite hermetic and run it in CI
```

## One thing worth knowing regardless

Things shipped a schema change with the "complete a repeating task early" feature. It is
**index-only** — two indexes added, one dropped, no columns altered — so no read code is affected.
Completing early reuses the ordinary completed status rather than introducing a new value; it
materializes the next occurrence and completes it in the same instant. Verified against a live beta
database.
