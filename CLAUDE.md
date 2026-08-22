# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Development Setup
```bash
# Install dependencies (uses uv package manager)
uv sync

# Run the MCP server (stdio transport, default)
uv run things-mcp

# Run with HTTP transport
THINGS_MCP_TRANSPORT=http uv run things-mcp
```

### Testing

The project includes a comprehensive unit test suite covering URL scheme construction and data formatting functions.

```bash
# Install test dependencies
uv sync --extra test

# Run all tests
uv run pytest

# Run tests with verbose output
uv run pytest -v

# Run specific test file
uv run pytest tests/test_url_scheme.py

# Run tests matching a pattern
uv run pytest -k "test_format"
```

Test coverage includes:
- All URL construction functions (add, update, show, search)
- Authentication token handling
- Data formatting for todos, projects, areas, and tags
- Error handling and edge cases
- Mock external dependencies (Things.py, shell commands)

## Architecture Overview

This is a Model Context Protocol (MCP) server that bridges Claude Desktop with the Things 3 task management app on macOS. The architecture consists of:

1. **src/things_mcp/server.py** - Main MCP server implementation using FastMCP (3.x)
   - Defines all MCP tools for interacting with Things (30 tools)
   - List views (inbox, today, upcoming, etc.)
   - CRUD operations for todos/projects/areas (Areas have create/read/update — no delete by design; see below)
   - Search and tag operations
   - Things URL scheme integration
   - Implements Someday project filtering to match Things UI behavior
   - **Pagination + structured responses**: the 16 list/search read tools accept optional `limit`/`offset` and return a FastMCP `ToolResult` carrying both a human-readable text channel and a `structured_content` envelope `{items, count, total, offset, limit}`. Two helpers drive this: `_paginate_format` (text) and `_paginate_result` (wraps text + JSON-safe structured data); `_error_result` wraps early-return/validation errors. `get_tag_usage` intentionally stays a plain string.

2. **src/things_mcp/url_scheme.py** - Things URL scheme + AppleScript implementation
   - Constructs Things URLs for various operations
   - Uses shell script with `open -g` to execute URLs without bringing Things to foreground
   - Handles authentication tokens for update operations. `auth_token()` wraps `things.token()`,
     which opens the Things database and so raises rather than returning None when Things is
     missing or unreadable; `construct_url` raises `AuthTokenUnavailable` (message:
     `AUTH_TOKEN_HELP`) for update commands with no usable token, which the update tools
     return as a plain string rather than dispatching an update Things would reject
   - `add_area`/`update_area`/`add_tag`/`trash_item` use AppleScript (`osascript`) since the Things URL scheme has no area commands; all user-supplied strings are escaped (`\` then `"`) before being embedded in the AppleScript string literal to prevent injection, and `osascript` is invoked with an argv list (no shell)

3. **src/things_mcp/formatters.py** - Data formatting utilities
   - Converts Things database objects to human-readable text
   - Handles nested data (projects within areas, checklist items, etc.)

4. **src/things_mcp/recurrence.py** - Next occurrence of repeating tasks
   - things.py filters every repeating task out of every list query (`rt1_recurrenceRule IS NULL`
     is hardcoded into its base WHERE), and its `today()` says outright that the prediction
     "does not include repeating tasks at this time" — so Today and Upcoming came back missing
     most of what the app shows (68 of 107 Upcoming rows on a real database)
   - A future occurrence is not a row. One template row per repeating task holds the rule and
     `rt1_nextInstanceStartDate`, the single next occurrence; `next_occurrences()` projects that
     one and nothing further. Anything deeper means evaluating the recurrence rule
   - No double counting: Things clears `rt1_nextInstanceStartDate` while a materialized instance
     row is live and sets it again when that row completes, so the two are mutually exclusive —
     verified across all 76 templates in a real database
   - A projection reports the *template's* uuid, because the template row is itself the row the
     app shows in Upcoming — verified in the app, where Copy Link on that row yields the
     template's uuid. `update_todo` against it therefore edits the repeat schedule rather than
     one occurrence, which is what editing that row in the app does too
   - Templates are fetched with `things.tasks(uuid=...)`, which routes to `get_task_by_uuid` —
     the one query in things.py that matches on uuid alone and so will return a template
   - **A template's `deadline` column is not a date.** Every template carries the same sentinel
     (262213760), which things.py decodes as an ordinary Things date and returns as `1953-01-01`.
     The real deadline is relative to the occurrence and lives in `rt1_recurrenceRule` — a plain
     XML plist, not an opaque blob — whose `ts` key is the negated number of days after the
     occurrence. `_deadline_for` parses it and returns None rather than a guess if it won't parse.
     Verified against the app for offsets of 0, 1, 7, 10 and 14 days

5. **tests/** - Unit test suite (398 tests)
   - **conftest.py** - Pytest fixtures and mock data
   - **_helpers.py** - `tool_text()` reads the text channel from a `ToolResult` (or a plain-string result) so text assertions work across both return shapes
   - **test_url_scheme.py** - Tests for URL construction
   - **test_formatters.py** - Tests for data formatting
   - **test_things_server.py** - Tests for server tools (incl. pagination + structured-content shape)
   - **test_things_server_headings.py** - Tests for heading functionality
   - **test_recurrence.py** - Tests for projecting repeating tasks into Today and Upcoming
   - **test_someday_filtering.py** - Tests for Someday project filtering
   - **test_mcp_server_filtering.py** - Integration tests for MCP server filtering

## Key Implementation Details

- Uses things.py library for reading Things SQLite database
- Write operations use the Things URL scheme API, except Area create/update which use AppleScript (`osascript`)
- FastMCP (3.x) provides the MCP protocol implementation
- Read tools return a `ToolResult` (human-readable text + `structured_content`); write/report tools and error paths return plain strings
- Error handling for invalid UUIDs and missing parameters; pagination args validated by `_validate_pagination`
- Supports filtering and including nested items via parameters
- **Display ordering**: project, area and Upcoming reads are sorted the way the Things UI shows them, not by raw `index`. `formatters.display_order` applies the scheduling groups (Anytime → scheduled by date → Someday) and sorts projects above to-dos within a group; `schedule_group` classifies an item. Each group uses a different manual-order column — `index` for Anytime and Someday, `todayIndex` for scheduled — which was verified against the app, not inferred. `server._project_display_order` composes that with the heading grouping, since a to-do's `index` is relative to its own heading. All of it is pinned by `tests/test_display_order.py` using real values from the app
- **Search ranking**: `search_todos` tokenizes the query, narrows in SQL with the longest token, then matches and ranks the rest in Python (`_search_tokens`, `_token_score`, `_rank_search_results`). Matching is against title and notes only — things.py also matches the parent AREA's title, which flooded results when an area name was searched
- **Repeating tasks**: `get_today` and `get_upcoming` merge in `recurrence.next_occurrences()` —
  occurrences due on or before today go to Today (alongside the other predictions `things.today()`
  makes), later ones to Upcoming. A projected row carries `repeating: True`, which `format_todo`
  renders as a "Repeating" line so a projection is distinguishable from a row that exists
- **Someday Project Filtering**: Tasks from Someday projects are filtered out of Today, Upcoming, and Anytime views to match the Things UI behavior and reduce clutter
- Unit tests mock all external dependencies (Things.py, shell commands)
- Pytest configuration in pyproject.toml with async support
- Supports both stdio (default) and HTTP transport modes

## Things URL Scheme Authentication

For update operations, the server automatically fetches and includes the auth-token from things.py. This allows updating existing items without user intervention.