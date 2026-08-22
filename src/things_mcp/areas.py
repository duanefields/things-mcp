"""Which area a task falls under, including the one it inherits.

A to-do almost never carries an area of its own. On a real database only 88 of
676 incomplete to-dos did -- 13% -- while 664 of them, 98%, fall under an area
once you follow the parent. So any question phrased by area ("what work is due
this week") answered from `area` alone was answering from about 2% of the truth,
and doing it silently: "show me work tasks" returned 4 rows where 185 qualify.

There are four cases, and all of them happen:

* Filed straight in an area. `area` is set; that is the answer.
* Filed in a project. The most common case by far. The project's area is the
  answer -- and a project need not be in one, so the answer can still be None.
* Filed under a heading in a project. The heading holds the project, the
  project holds the area.
* Filed in none of the above: Inbox, or a project with no area. No answer, and
  `effective_area` is None rather than a guess.

`area` is left exactly as Things stores it. A to-do in Gravehoard is not filed
in Fast Wombat, and saying so would misreport what the database holds; the
inherited answer travels beside it in `effective_area` instead.

`project` gets the same treatment as `effective_project`, since a task under a
heading has no project of its own -- the heading holds it.
"""

import logging

from things.database import Database, TABLE_AREA

logger = logging.getLogger(__name__)

# All three maps in one round trip. type 1 is a project and type 2 a heading,
# per things.py's own TYPE_TO_FILTER; `kind` says which map a row belongs in.
_PARENTS_SQL = f"""
    SELECT 'project' AS kind, uuid AS key, area    AS value FROM TMTask WHERE type = 1
    UNION ALL
    SELECT 'heading' AS kind, uuid AS key, project AS value FROM TMTask WHERE type = 2
    UNION ALL
    SELECT 'area'    AS kind, uuid AS key, title   AS value FROM {TABLE_AREA}
"""


def parent_lookup():
    """Maps for resolving a parent area: project->area, heading->project, area->title.

    About 9ms on a database with 1,848 projects and 1,679 headings. One query
    rather than three for the round trip, though the cost is the table scans
    and splitting it back out measured the same.

    Resolving from maps beats asking per task: every to-do in a list shares the
    same few hundred parents, and this replaces a things.get per row.

    Returns empty maps if the database cannot be read, which resolves every
    task to no area rather than failing the request.
    """
    projects, headings, titles = {}, {}, {}
    buckets = {'project': projects, 'heading': headings, 'area': titles}
    try:
        rows = Database().execute_query(_PARENTS_SQL)
    except Exception:
        logger.warning("Could not read the area hierarchy from the Things database",
                       exc_info=True)
        return projects, headings, titles
    for row in rows:
        buckets[row['kind']][row['key']] = row['value']
    return projects, headings, titles


def effective_project(task, headings):
    """The project uuid a task really belongs to, or None.

    A task filed under a heading has `project` NULL -- the heading holds the
    project. 63 of Gravehoard's 79 to-dos are in that shape.
    """
    return task.get('project') or headings.get(task.get('heading'))


def effective_area(task, projects, headings):
    """The area uuid a task falls under, or None. See the module docstring."""
    if task.get('area'):
        return task['area']
    project = effective_project(task, headings)
    return projects.get(project) if project else None


def annotate(tasks, lookup=None):
    """Add effective_project, effective_area and effective_area_title, in place.

    Idempotent: a task that already carries the fields is left alone, so this
    can run again on a page that was annotated before it was filtered.
    """
    tasks = list(tasks or [])
    pending = [t for t in tasks
               if isinstance(t, dict) and 'effective_area' not in t]
    if not pending:
        return tasks
    projects, headings, titles = lookup if lookup is not None else parent_lookup()
    for task in pending:
        task['effective_project'] = effective_project(task, headings)
        uuid = effective_area(task, projects, headings)
        task['effective_area'] = uuid
        task['effective_area_title'] = titles.get(uuid) if uuid else None
    return tasks
