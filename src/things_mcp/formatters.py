import logging
import things
from datetime import datetime

logger = logging.getLogger(__name__)


def _calculate_age(date_str: str) -> str:
    """Helper function to calculate human-readable age from a date string.

    Args:
        date_str: ISO format date string

    Returns:
        Human-readable age string (e.g., "3 days ago", "2 weeks ago")

    Raises:
        ValueError: If date string cannot be parsed
        TypeError: If date_str is not a string
    """
    date_obj = datetime.fromisoformat(str(date_str))
    age = datetime.now() - date_obj
    days = age.days

    if days == 0:
        return "today"
    elif days == 1:
        return "1 day ago"
    elif days < 7:
        return f"{days} days ago"
    elif days < 30:
        weeks = days // 7
        return f"{weeks} week{'s' if weeks > 1 else ''} ago"
    elif days < 365:
        months = days // 30
        return f"{months} month{'s' if months > 1 else ''} ago"
    else:
        years = days // 365
        return f"{years} year{'s' if years > 1 else ''} ago"


def schedule_group(todo):
    """Which of the three groups Things shows a todo in: 0 Anytime, 1 scheduled, 2 Someday.

    Things stores a scheduled ("Upcoming") item as start='Someday' with a
    start_date, and a true Someday item as start='Someday' with none. Anytime
    comes first, and that includes anything scheduled for today or overdue --
    Things keeps those in the main list rather than moving them down.
    """
    if todo.get("start") != "Someday":
        return 0
    return 1 if todo.get("start_date") else 2


def _position(todo, field):
    """A sortable pair for one of Things' manual-order columns, missing last."""
    value = todo.get(field)
    return (value is None, value or 0)


def display_order(todos):
    """Todos in the order Things lists them within one container.

    Anytime items in their manual order, then scheduled items by date, then
    Someday items in their manual order. `index` is a manual position and says
    nothing about which group an item is in, so sorting on it alone interleaves
    all three.

    The two groups use different manual-order columns, which is not guessable
    and was checked against the app. Anytime and Someday go by `index`.
    Scheduled items go by date and then `todayIndex`, their position in the
    date-based views -- `index` gets this visibly wrong, ordering one real
    project's 8/29 items 4060, 0, 0 where the app shows them 4060 first.
    Verified against Things' display of a project heading holding both groups.

    start_date enters the key only for the scheduled group. An Anytime item can
    carry one too -- that is what a to-do scheduled for today looks like -- and
    letting it sort them would break the manual order Things shows them in.

    Projects sort above to-dos inside a group, which index does not express: an
    area's Someday project heads its Someday section above to-dos whose indexes
    are far lower, and its Anytime projects all precede Anytime to-dos with
    indexes in between. Also verified against the app.
    """
    def key(todo):
        group = schedule_group(todo)
        if group == 1:
            date = todo.get("start_date") or ""
            manual = _position(todo, "today_index")
        else:
            date = ""
            manual = _position(todo, "index")
        kind = 0 if todo.get("type") == "project" else 1
        # `index` again as a stable tiebreak: 10 of 38 scheduled todos in the
        # sample carried todayIndex 0, and their relative order is unverified.
        return (group, date, kind, manual, _position(todo, "index"))

    return sorted(todos, key=key)


def upcoming_order(todos):
    """Todos in the order the Upcoming list shows them.

    Upcoming is a flat sort by date, and unlike an area or a project view it
    does NOT put projects above to-dos. `todayIndex` already encodes where a
    project sits within its day -- in the app, 🔑 Spare Jeep Key leads Aug 30
    and ✈️ Dublin closes it -- so applying display_order's projects-first rule
    here moves rows to the wrong end of their day. Verified against the app
    across five days covering 38 rows.

    A deadline-only task has no start date and takes its position from its
    deadline, which is how the app places it.
    """
    return sorted(todos, key=lambda todo: (
        todo.get("start_date") or todo.get("deadline") or "",
        _position(todo, "today_index"),
        _position(todo, "index"),
    ))


def _lookup_title(uuid):
    """Fetch an item by uuid and return its title, or None if missing/broken.

    Wraps the things.get + try/except + None check pattern used to display
    parent project, area, and heading titles next to a task.
    """
    if not uuid:
        return None
    try:
        obj = things.get(uuid)
    except Exception:
        return None
    return obj['title'] if obj and obj.get('title') else None


def _append_timestamps(text: str, item: dict) -> str:
    """Append 'Created/Age' and 'Modified/Last modified' lines if present.

    Both blocks were duplicated across format_todo / format_project /
    format_area / format_heading with the same try/except shape. Centralise
    here so the four formatters stay in lock-step.
    """
    if item.get('created'):
        text += f"\nCreated: {item['created']}"
        try:
            text += f"\nAge: {_calculate_age(item['created'])}"
        except (ValueError, TypeError):
            pass
    if item.get('modified'):
        text += f"\nModified: {item['modified']}"
        try:
            text += f"\nLast modified: {_calculate_age(item['modified'])}"
        except (ValueError, TypeError):
            pass
    return text


def format_todo(todo: dict) -> str:
    """Helper function to format a single todo into a readable string."""
    todo_text = f"Title: {todo['title']}"
    todo_text += f"\nUUID: {todo['uuid']}"
    todo_text += f"\nType: {todo['type']}"

    if todo.get('status'):
        todo_text += f"\nStatus: {todo['status']}"

    # Look up parent project once (used for both List status and Project display).
    # For heading-level tasks without a project field, resolve heading -> project.
    parent_project = None
    if todo.get('project'):
        try:
            parent_project = things.get(todo['project'])
        except Exception:
            pass
    elif todo.get('heading'):
        try:
            heading_obj = things.get(todo['heading'])
            if heading_obj and heading_obj.get('project'):
                parent_project = things.get(heading_obj['project'])
        except Exception:
            pass

    # Start/list location with Someday inheritance from the parent project.
    if todo.get('start'):
        effective_start = todo['start']
        if effective_start != 'Someday' and parent_project and parent_project.get('start') == 'Someday':
            effective_start = 'Someday'
            todo_text += f"\nList: {effective_start} (inherited from project)"
        else:
            todo_text += f"\nList: {effective_start}"

    if todo.get('start_date'):
        todo_text += f"\nStart Date: {todo['start_date']}"
    if todo.get('repeating'):
        todo_text += "\nRepeating: yes (Start Date is the next occurrence)"
    if todo.get('deadline'):
        todo_text += f"\nDeadline: {todo['deadline']}"
    if todo.get('stop_date'):
        todo_text += f"\nCompleted: {todo['stop_date']}"

    todo_text = _append_timestamps(todo_text, todo)

    if todo.get('notes'):
        todo_text += f"\nNotes: {todo['notes']}"

    if parent_project:
        todo_text += f"\nProject: {parent_project['title']}"

    heading_title = _lookup_title(todo.get('heading'))
    if heading_title:
        todo_text += f"\nHeading: {heading_title}"

    area_title = _lookup_title(todo.get('area'))
    if area_title:
        todo_text += f"\nArea: {area_title}"

    if todo.get('tags'):
        todo_text += f"\nTags: {', '.join(todo['tags'])}"

    if isinstance(todo.get('checklist'), list):
        todo_text += "\nChecklist:"
        for item in todo['checklist']:
            checkbox = "✓" if item.get('status') == 'completed' else "☐"
            todo_text += f"\n  {checkbox} {item['title']}"

    return todo_text


def format_project(project: dict, include_items: bool = False) -> str:
    """Helper function to format a single project."""
    project_text = f"Title: {project['title']}\nUUID: {project['uuid']}"

    area_title = _lookup_title(project.get('area'))
    if area_title:
        project_text += f"\nArea: {area_title}"

    if project.get('notes'):
        project_text += f"\nNotes: {project['notes']}"

    project_text = _append_timestamps(project_text, project)

    # Always show headings for projects.
    headings = things.tasks(type='heading', project=project['uuid'])
    if headings:
        project_text += "\n\nHeadings:"
        for heading in headings:
            project_text += f"\n- {heading['title']}"

    if include_items:
        todos = things.todos(project=project['uuid'])
        if todos:
            project_text += "\n\nTasks:"
            for todo in todos:
                project_text += f"\n- {todo['title']}"

    return project_text


def format_area(area: dict, include_items: bool = False) -> str:
    """Helper function to format a single area."""
    area_text = f"Title: {area['title']}\nUUID: {area['uuid']}"

    if area.get('notes'):
        area_text += f"\nNotes: {area['notes']}"

    area_text = _append_timestamps(area_text, area)

    if include_items:
        # Projects and to-dos are one list in the app, not two sections. An area
        # groups everything it holds by list -- Anytime, then Upcoming, then
        # Someday -- so a Someday project sits below Anytime to-dos rather than
        # at the top with the other projects.
        items = display_order(
            (things.projects(area=area['uuid']) or [])
            + (things.todos(area=area['uuid']) or [])
        )
        for label, group in (("Items", 0), ("Upcoming", 1), ("Someday", 2)):
            rows = [i for i in items if schedule_group(i) == group]
            if not rows:
                continue
            area_text += f"\n\n{label}:"
            for item in rows:
                date = f"[{item['start_date']}] " if group == 1 else ""
                kind = "[project] " if item.get('type') == 'project' else ""
                area_text += f"\n- {date}{kind}{item['title']}"

    return area_text


def format_tag(tag: dict, include_items: bool = False) -> str:
    """Helper function to format a single tag."""
    tag_text = f"Title: {tag['title']}\nUUID: {tag['uuid']}"

    if tag.get('shortcut'):
        tag_text += f"\nShortcut: {tag['shortcut']}"

    if include_items:
        todos = things.todos(tag=tag['title'])
        if todos:
            tag_text += "\n\nTagged Items:"
            for todo in todos:
                tag_text += f"\n- {todo['title']}"

    return tag_text


def format_heading(heading: dict, include_items: bool = False) -> str:
    """Helper function to format a single heading."""
    heading_text = f"Title: {heading['title']}\nUUID: {heading['uuid']}"
    heading_text += f"\nType: heading"

    if heading.get('project'):
        # Prefer the inlined project_title if things-py already provided it,
        # otherwise fall back to a fresh lookup.
        project_title = heading.get('project_title') or _lookup_title(heading.get('project'))
        if project_title:
            heading_text += f"\nProject: {project_title}"

    heading_text = _append_timestamps(heading_text, heading)

    if heading.get('notes'):
        heading_text += f"\nNotes: {heading['notes']}"

    if include_items:
        todos = things.todos(heading=heading['uuid'])
        if todos:
            heading_text += "\n\nTasks under heading:"
            for todo in todos:
                heading_text += f"\n- {todo['title']}"

    return heading_text
