"""Next occurrences of repeating tasks.

things.py hides every repeating task from every list query -- the base WHERE
clause in `Database.get_tasks` is `rt1_recurrenceRule IS NULL` -- and its
`today()` says outright that the prediction "does not include repeating tasks
at this time". Today and Upcoming therefore come back missing most of what the
app shows: 68 of the 107 rows in Upcoming on the database this was written
against.

A future occurrence is not a row. What exists is one template row per repeating
task, holding the recurrence rule and `rt1_nextInstanceStartDate`, the date of
the single next occurrence. Past occurrences are separate, completed rows. We
project that one occurrence and nothing further; going deeper would mean
evaluating the recurrence rule ourselves.

Two things make the projection safe to merge into the existing lists:

* No double counting. Once Things materializes an occurrence as a real row it
  clears `rt1_nextInstanceStartDate`, and sets it again only when that row is
  completed. A non-null next date and a live instance row are mutually
  exclusive -- checked across all 76 templates in a real database, where the 8
  templates carrying a live instance were exactly the 8 with a null next date.

* A real uuid. The template row is itself the row the app shows in Upcoming, so
  a projected occurrence reports the template's uuid rather than a synthetic
  one. Verified in the app: Copy Link on "Use Google Voice number to keep it"
  in Fast Wombat's Upcoming section gives
  things:///show?id=BqruYNhGQTPj59fYPfsHKh, the template's uuid. The
  consequence worth knowing: `update_todo` against it edits the repeat schedule
  rather than a single occurrence, which is also what editing that row does in
  the app.
"""

import logging

import things
from things.database import (
    Database,
    convert_thingsdate_sql_expression_to_isodate,
)

logger = logging.getLogger(__name__)

# Live repeating templates and the date of their next occurrence. A paused
# repeater generates nothing, so it has nothing upcoming to show.
_NEXT_OCCURRENCE_SQL = f"""
    SELECT uuid,
           {convert_thingsdate_sql_expression_to_isodate('rt1_nextInstanceStartDate')}
               AS start_date
    FROM TMTask
    WHERE rt1_recurrenceRule IS NOT NULL
      AND rt1_nextInstanceStartDate IS NOT NULL
      AND status = 0
      AND trashed = 0
      AND (rt1_instanceCreationPaused IS NULL OR rt1_instanceCreationPaused = 0)
"""


def next_occurrences():
    """The next occurrence of every live repeating task, as task dicts.

    Each dict is what things.py would return for the template row, with
    `start_date` set to the next occurrence and `repeating` set to True so
    callers and formatters can tell a projection from a row that exists.

    Returns an empty list if the database can't be read, which is what the
    lists showed before this existed.
    """
    try:
        rows = Database().execute_query(_NEXT_OCCURRENCE_SQL)
    except Exception:
        logger.warning("Could not read repeating tasks from the Things database",
                       exc_info=True)
        return []

    occurrences = []
    for row in rows:
        # things.tasks(uuid=...) routes to get_task_by_uuid, which matches on
        # uuid alone and so is the one query in things.py that will hand back a
        # repeating template. One call per template, but format_todo already
        # spends more than this looking up each item's project and area titles.
        try:
            task = things.tasks(uuid=row['uuid'])
        except Exception:
            continue
        if not task:
            continue
        task['start_date'] = row['start_date']
        task['repeating'] = True
        occurrences.append(task)
    return occurrences
