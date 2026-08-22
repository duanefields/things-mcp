import json as _json
import logging
import urllib.parse
import subprocess
import time
import things
from typing import Optional, Dict, Any, List, Union

logger = logging.getLogger(__name__)

AUTH_TOKEN_HELP = (
    "Things auth token unavailable. Update operations require it. "
    "Enable in Things → Settings → General → Manage."
)


class AuthTokenUnavailable(RuntimeError):
    """The Things auth token could not be read, so an update cannot be built."""


def auth_token() -> Optional[str]:
    """The Things URL scheme auth token, or None if it cannot be read.

    things.token() opens the Things database, so it raises rather than returning
    None when Things is not installed, the database is locked, or the macOS
    privacy approval is missing. Every caller wants the same answer -- no usable
    token -- in all of those cases, and a raw traceback through the MCP boundary
    in none of them.
    """
    try:
        return things.token()
    except Exception as exc:
        logger.warning(
            "Could not read the Things auth token: %s: %s", type(exc).__name__, exc
        )
        return None

# When parameter accepted values:
# - Keywords: "today", "tomorrow", "evening", "anytime", "someday"
# - Date string: "yyyy-mm-dd" (e.g., "2024-01-15") or natural language ("in 3 days", "next tuesday")
# - DateTime string: "yyyy-mm-dd@HH:MM" (e.g., "2024-01-15@14:30") - adds a reminder at that time
# - ISO8601: "2024-01-15T14:30:00Z" or with timezone offset


def format_when_with_reminder(date: str, time: str) -> str:
    """Format a date and time into a Things datetime string for reminders.

    Args:
        date: Date in yyyy-mm-dd format, or "today"/"tomorrow"/natural language
        time: Time in HH:MM (24h) or H:MMPM (12h) format (e.g., "14:30" or "2:30PM")

    Returns:
        Formatted datetime string (e.g., "2024-01-15@14:30")

    Example:
        >>> format_when_with_reminder("2024-01-15", "14:30")
        '2024-01-15@14:30'
        >>> format_when_with_reminder("tomorrow", "9:00AM")
        'tomorrow@9:00AM'
    """
    return f"{date}@{time}"

# Outcome of the most recent dispatch, reported by the health endpoint. Writes go
# out through the URL scheme and are not acknowledged, so a dispatch that starts
# failing is otherwise silent.
_last_dispatch: Dict[str, Any] = {"at": None, "ok": None, "error": None}


def last_dispatch() -> Dict[str, Any]:
    """Return a copy of the most recent dispatch outcome."""
    return dict(_last_dispatch)


def execute_url(url: str) -> None:
    """Execute a Things URL without bringing Things to the foreground."""
    _last_dispatch["at"] = time.time()
    try:
        try:
            # Use 'do shell script' with 'open -g' to open in background
            subprocess.run([
                'osascript', '-e',
                f'do shell script "open -g \\"{url}\\""'
            ], check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError:
            # Fallback - still try with open -g directly
            subprocess.run(['open', '-g', url], check=True)
    except Exception as exc:
        _last_dispatch["ok"] = False
        _last_dispatch["error"] = str(exc)
        raise
    else:
        _last_dispatch["ok"] = True
        _last_dispatch["error"] = None


def add_area(title: str) -> str:
    """Create a new Area in Things 3 via AppleScript.

    The Things URL scheme has no add-area command, so we use AppleScript instead.
    Returns the new Area's UUID.
    """
    escaped_title = title.replace('\\', '\\\\').replace('"', '\\"')
    applescript = (
        'tell application "Things3"\n'
        f'  set newArea to make new area with properties {{name:"{escaped_title}"}}\n'
        '  return id of newArea\n'
        'end tell'
    )
    result = subprocess.run(
        ['osascript', '-e', applescript],
        check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def update_area(area_id: str, title: Optional[str] = None,
                tags: Optional[list[str]] = None) -> None:
    """Update an existing Area in Things 3 via AppleScript.

    The Things URL scheme has no area operations, so we use AppleScript.
    Only the parameters that are provided are changed.

    Note: there is deliberately no delete_area — deleting an Area in Things
    also deletes every project it contains, which is destructive and not
    recoverable.

    Args:
        area_id: UUID of the area to update
        title: New name for the area
        tags: Tags to set on the area (replaces existing; Things only applies
            tags that already exist)
    """
    def esc(s: str) -> str:
        return s.replace('\\', '\\\\').replace('"', '\\"')

    statements = []
    if title is not None:
        statements.append(f'set name of theArea to "{esc(title)}"')
    if tags is not None:
        statements.append(f'set tag names of theArea to "{esc(",".join(tags))}"')
    if not statements:
        return

    body = '\n  '.join(statements)
    applescript = (
        'tell application "Things3"\n'
        f'  set theArea to area id "{esc(area_id)}"\n'
        f'  {body}\n'
        'end tell'
    )
    subprocess.run(
        ['osascript', '-e', applescript],
        check=True, capture_output=True, text=True
    )


def construct_url(command: str, params: Dict[str, Any]) -> str:
    """Construct a Things URL from command and parameters."""
    # Start with base URL
    url = f"things:///{command}"

    # Get authentication token if needed. Things rejects an update that arrives
    # without one, so a missing token is an error rather than a URL to build and
    # dispatch anyway -- the caller would be told the update succeeded.
    if command in ['update', 'update-project']:
        token = auth_token()
        if not token:
            raise AuthTokenUnavailable(AUTH_TOKEN_HELP)
        params['auth-token'] = token

    # URL encode parameters
    if params:
        encoded_params = []
        for key, value in params.items():
            if value is None:
                continue
            # Handle boolean values
            if isinstance(value, bool):
                value = str(value).lower()
            # Handle lists (for tags, checklist items etc)
            elif isinstance(value, list):
                value = ','.join(str(v) for v in value)
            # safe='' so '/' inside values (e.g. "2/13" in a title) is percent-encoded
            # as %2F. urllib.parse.quote's default safe='/' would leave it as a literal
            # slash, which Things parses as a path delimiter and silently truncates.
            encoded_params.append(f"{key}={urllib.parse.quote(str(value), safe='')}")

        url += "?" + "&".join(encoded_params)

    return url

def add_todo(title: str, notes: Optional[str] = None, when: Optional[str] = None,
             deadline: Optional[str] = None, tags: Optional[list[str]] = None,
             checklist_items: Optional[list[str]] = None, list_id: Optional[str] = None,
             list_title: Optional[str] = None, heading: Optional[str] = None,
             heading_id: Optional[str] = None,
             completed: Optional[bool] = None) -> str:
    """Construct URL to add a new todo.

    Args:
        title: Title of the todo
        notes: Notes for the todo
        when: Schedule the todo. Accepts:
            - Keywords: "today", "tomorrow", "evening", "anytime", "someday"
            - Date: "yyyy-mm-dd" or natural language ("in 3 days", "next tuesday")
            - DateTime (adds reminder): "yyyy-mm-dd@HH:MM" (e.g., "2024-01-15@14:30")
        deadline: Deadline date (yyyy-mm-dd)
        tags: List of tag names
        checklist_items: List of checklist item titles
        list_id: UUID of project/area to add to
        list_title: Title of project/area to add to
        heading: Heading title within project
        heading_id: UUID of heading within project
        completed: Mark as completed on creation
    """
    params = {
        'title': title,
        'notes': notes,
        'when': when,
        'deadline': deadline,
        'checklist-items': '\n'.join(checklist_items) if checklist_items else None,
        'list-id': list_id,
        'list': list_title,
        'heading': heading,
        'heading-id': heading_id,
        'completed': completed
    }

    # Handle tags separately since they need to be comma-separated
    if tags:
        params['tags'] = ','.join(tags)
    return construct_url('add', {k: v for k, v in params.items() if v is not None})

def add_project(title: str, notes: Optional[str] = None, when: Optional[str] = None,
                deadline: Optional[str] = None, tags: Optional[list[str]] = None,
                area_id: Optional[str] = None, area_title: Optional[str] = None,
                todos: Optional[list[str]] = None) -> str:
    """Construct URL to add a new project.

    Args:
        title: Title of the project
        notes: Notes for the project
        when: Schedule the project. Accepts:
            - Keywords: "today", "tomorrow", "evening", "anytime", "someday"
            - Date: "yyyy-mm-dd" or natural language ("in 3 days", "next tuesday")
            - DateTime (adds reminder): "yyyy-mm-dd@HH:MM" (e.g., "2024-01-15@14:30")
        deadline: Deadline date (yyyy-mm-dd)
        tags: List of tag names
        area_id: UUID of area to add to
        area_title: Title of area to add to
        todos: List of todo titles to create in the project
    """
    params = {
        'title': title,
        'notes': notes,
        'when': when,
        'deadline': deadline,
        'area-id': area_id,
        'area': area_title,
        # Change todos to be newline separated
        'to-dos': '\n'.join(todos) if todos else None
    }

    # Handle tags separately since they need to be comma-separated
    if tags:
        params['tags'] = ','.join(tags)

    return construct_url('add-project', {k: v for k, v in params.items() if v is not None})

def update_todo(id: str, title: Optional[str] = None, notes: Optional[str] = None,
                prepend_notes: Optional[str] = None,
                append_notes: Optional[str] = None,
                when: Optional[str] = None, deadline: Optional[str] = None,
                tags: Optional[list[str]] = None,
                add_tags: Optional[list[str]] = None,
                completed: Optional[bool] = None,
                canceled: Optional[bool] = None, list: Optional[str] = None,
                list_id: Optional[str] = None, heading: Optional[str] = None,
                heading_id: Optional[str] = None,
                checklist_items: Optional[list[str]] = None,
                prepend_checklist_items: Optional[list[str]] = None,
                append_checklist_items: Optional[list[str]] = None) -> str:
    """Construct URL to update an existing todo.

    Args:
        id: UUID of the todo to update
        title: New title
        notes: New notes (replaces existing)
        prepend_notes: Text to add above the existing notes
        append_notes: Text to add below the existing notes
        when: Reschedule the todo. Accepts:
            - Keywords: "today", "tomorrow", "evening", "anytime", "someday"
            - Date: "yyyy-mm-dd" or natural language ("in 3 days", "next tuesday")
            - DateTime (adds reminder): "yyyy-mm-dd@HH:MM" (e.g., "2024-01-15@14:30")
        deadline: New deadline (yyyy-mm-dd)
        tags: New tags (replaces existing)
        add_tags: Tags to append to the existing list (does not remove existing)
        completed: Mark as completed
        canceled: Mark as canceled
        list: Title of project/area to move to
        list_id: UUID of project/area to move to (takes precedence over list)
        heading: Heading title to move under
        heading_id: UUID of heading to move under (takes precedence over heading)
        checklist_items: Replace the entire checklist with these items
        prepend_checklist_items: Add these items to the start of the checklist
        append_checklist_items: Add these items to the end of the checklist
    """
    params = {
        'id': id,
        'title': title,
        'notes': notes,
        'prepend-notes': prepend_notes,
        'append-notes': append_notes,
        'when': when,
        'deadline': deadline,
        'tags': tags,
        'add-tags': add_tags,
        'completed': completed,
        'canceled': canceled,
        'list': list,
        'list-id': list_id,
        'heading': heading,
        'heading-id': heading_id,
        'checklist-items': '\n'.join(checklist_items) if checklist_items else None,
        'prepend-checklist-items': '\n'.join(prepend_checklist_items) if prepend_checklist_items else None,
        'append-checklist-items': '\n'.join(append_checklist_items) if append_checklist_items else None,
    }
    return construct_url('update', {k: v for k, v in params.items() if v is not None})

def update_project(id: str, title: Optional[str] = None, notes: Optional[str] = None,
                   prepend_notes: Optional[str] = None,
                   append_notes: Optional[str] = None,
                   when: Optional[str] = None, deadline: Optional[str] = None,
                   tags: Optional[list[str]] = None, completed: Optional[bool] = None,
                   canceled: Optional[bool] = None) -> str:
    """Construct URL to update an existing project.

    Args:
        id: UUID of the project to update
        title: New title
        notes: New notes (replaces existing)
        prepend_notes: Text to add above the existing notes
        append_notes: Text to add below the existing notes
        when: Reschedule the project. Accepts:
            - Keywords: "today", "tomorrow", "evening", "anytime", "someday"
            - Date: "yyyy-mm-dd" or natural language ("in 3 days", "next tuesday")
            - DateTime (adds reminder): "yyyy-mm-dd@HH:MM" (e.g., "2024-01-15@14:30")
        deadline: New deadline (yyyy-mm-dd)
        tags: New tags (replaces existing)
        completed: Mark as completed
        canceled: Mark as canceled
    """
    params = {
        'id': id,
        'title': title,
        'notes': notes,
        'prepend-notes': prepend_notes,
        'append-notes': append_notes,
        'when': when,
        'deadline': deadline,
        'tags': tags,
        'completed': completed,
        'canceled': canceled
    }
    return construct_url('update-project', {k: v for k, v in params.items() if v is not None})

def json_command(payload: List[Dict[str, Any]], auth_token: Optional[str] = None) -> str:
    """Construct a URL for Things' multi-operation 'json' endpoint.

    Each entry in payload follows the shape:
        {"type": "to-do", "operation": "create" | "update", "id"?: "<uuid>",
         "attributes": {... using hyphenated attribute names ...}}

    auth-token is required by Things whenever payload contains an 'update'
    operation; we include it whenever supplied so callers don't have to
    pre-classify the batch.
    """
    parts = [f"data={urllib.parse.quote(_json.dumps(payload), safe='')}"]
    if auth_token:
        parts.append(f"auth-token={urllib.parse.quote(auth_token, safe='')}")
    return "things:///json?" + "&".join(parts)


def show(id: str, query: Optional[str] = None, filter_tags: Optional[list[str]] = None) -> str:
    """Construct URL to show a specific item or list."""
    params = {
        'id': id,
        'query': query,
        'filter': filter_tags
    }
    return construct_url('show', {k: v for k, v in params.items() if v is not None})

def search(query: str) -> str:
    """Construct URL to perform a search."""
    return construct_url('search', {'query': query})
