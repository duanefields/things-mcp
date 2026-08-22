"""Tests for projecting the next occurrence of a repeating task.

things.py filters every repeating task out of every list query, so Today and
Upcoming used to come back missing most of what the app shows. These pin the
projection that fills the hole -- see src/things_mcp/recurrence.py.
"""
import plistlib

import pytest
from tests._helpers import tool_text
from things_mcp.formatters import format_area, format_project, format_todo
from things_mcp.recurrence import next_occurrences
from things_mcp.server import get_today, get_upcoming


TODAY = "2026-08-22"


def _template(uuid="tmpl-uuid", title="Water the plants"):
    """A repeating template as things.tasks(uuid=...) returns it.

    Things stores one of these per repeating task: start Someday, no
    start_date of its own, and the next occurrence held in a column things.py
    does not read.
    """
    return {
        'uuid': uuid,
        'title': title,
        'type': 'to-do',
        'status': 'incomplete',
        'start': 'Someday',
        'start_date': None,
        'today_index': 0,
        'index': 0,
    }


@pytest.fixture
def repeating(mocker):
    """Install one repeating template with a next occurrence, and return a
    setter for that date so each test can place it relative to TODAY."""
    rows = []
    mocker.patch(
        'things_mcp.recurrence.Database',
        return_value=mocker.Mock(execute_query=lambda sql, parameters=(): rows),
    )
    # things.tasks serves two callers now: hydrating a template by uuid, and
    # the deadline-only sweep in get_upcoming, which passes no uuid.
    tasks = mocker.patch(
        'things.tasks',
        side_effect=lambda uuid=None, **kw: _template(uuid) if uuid else [],
    )

    def schedule(date, uuid="tmpl-uuid", deadline=None, rule=None):
        rows.append({'uuid': uuid, 'start_date': date,
                     'deadline': deadline, 'rule': rule})
        return tasks

    return schedule


def test_next_occurrences_sets_start_date_and_marks_the_projection(repeating):
    repeating("2026-09-01")

    (occurrence,) = next_occurrences()

    # The template's own start_date is NULL; the projection carries the next
    # occurrence, and says that it is one.
    assert occurrence['start_date'] == "2026-09-01"
    assert occurrence['repeating'] is True
    # The template's uuid, not a synthetic one -- it is the row the app shows.
    assert occurrence['uuid'] == "tmpl-uuid"


def test_next_occurrences_survives_an_unreadable_database(mocker):
    """No Things database is the normal case off a Mac. The lists should come
    back without repeating tasks rather than failing outright."""
    mocker.patch(
        'things_mcp.recurrence.Database',
        side_effect=OSError("no database here"),
    )

    assert next_occurrences() == []


def test_next_occurrences_skips_a_template_that_cannot_be_read(mocker):
    mocker.patch(
        'things_mcp.recurrence.Database',
        return_value=mocker.Mock(
            execute_query=lambda sql, parameters=(): [
                {'uuid': 'gone', 'start_date': '2026-09-01',
                 'deadline': None, 'rule': None}
            ]
        ),
    )
    mocker.patch('things.tasks', side_effect=ValueError("no such task"))

    assert next_occurrences() == []


@pytest.mark.asyncio
async def test_get_upcoming_includes_the_next_occurrence(mocker, repeating):
    mocker.patch('things.upcoming', return_value=[])
    mocker.patch('things.projects', return_value=[])
    repeating("2026-09-01")

    result = tool_text(await get_upcoming())

    assert "Water the plants" in result
    assert "Start Date: 2026-09-01" in result


@pytest.mark.asyncio
async def test_get_upcoming_leaves_out_an_occurrence_due_today(mocker, repeating):
    """Upcoming is strictly the future -- things.upcoming() is
    start_date="future" -- so an occurrence due today belongs in Today."""
    mocker.patch('things.upcoming', return_value=[])
    mocker.patch('things.projects', return_value=[])
    mocker.patch('things_mcp.server.datetime', **{'now.return_value.date.return_value.isoformat.return_value': TODAY})
    repeating(TODAY)

    result = tool_text(await get_upcoming())

    assert "Water the plants" not in result


@pytest.mark.asyncio
async def test_get_upcoming_sorts_a_projection_in_by_date(mocker, repeating):
    """The projection has to sort among real rows by date, not land at the end."""
    mocker.patch('things.upcoming', return_value=[
        {'uuid': 'late', 'title': 'Later real todo', 'type': 'to-do',
         'start': 'Someday', 'start_date': '2026-12-01', 'today_index': 0, 'index': 0},
    ])
    mocker.patch('things.projects', return_value=[])
    repeating("2026-09-01")

    result = tool_text(await get_upcoming())

    assert result.index("Water the plants") < result.index("Later real todo")


@pytest.mark.asyncio
async def test_get_today_includes_an_occurrence_that_has_come_due(mocker, repeating):
    """Things only materializes an occurrence into a row when the app next
    opens, so a repeater due today may still be nothing but a template."""
    mocker.patch('things.today', return_value=[])
    mocker.patch('things.projects', return_value=[])
    mocker.patch('things_mcp.server.datetime', **{'now.return_value.date.return_value.isoformat.return_value': TODAY})
    repeating(TODAY)

    result = tool_text(await get_today())

    assert "Water the plants" in result


@pytest.mark.asyncio
async def test_get_today_leaves_out_a_future_occurrence(mocker, repeating):
    mocker.patch('things.today', return_value=[])
    mocker.patch('things.projects', return_value=[])
    mocker.patch('things_mcp.server.datetime', **{'now.return_value.date.return_value.isoformat.return_value': TODAY})
    repeating("2026-09-01")

    result = tool_text(await get_today())

    assert "Water the plants" not in result


def test_format_todo_marks_a_projected_occurrence():
    todo = dict(_template(), start_date="2026-09-01", repeating=True)

    text = format_todo(todo)

    assert "Repeating: yes (Start Date is the next occurrence)" in text


def test_format_todo_says_nothing_about_repeating_for_an_ordinary_todo():
    text = format_todo(dict(_template(), start_date="2026-09-01"))

    assert "Repeating" not in text


# The sentinel every repeating template carries in its deadline column, and the
# rule plist Things stores alongside it. `ts` is the negated number of days
# after the occurrence that the deadline falls -- here, 10.
DEADLINE_SENTINEL = 262213760
RULE_TS_MINUS_10 = plistlib.dumps({'ts': -10, 'rrv': 4})


def test_next_occurrences_computes_the_deadline_from_the_recurrence_rule(repeating):
    """The deadline column is a sentinel, not a date: things.py decodes it as
    1953-01-01 and would report that as a real deadline."""
    repeating("2026-09-16", deadline=DEADLINE_SENTINEL, rule=RULE_TS_MINUS_10)

    (occurrence,) = next_occurrences()

    assert occurrence['deadline'] == "2026-09-26"


def test_next_occurrences_leaves_a_deadline_off_when_the_template_has_none(repeating):
    repeating("2026-09-16", deadline=None, rule=None)

    (occurrence,) = next_occurrences()

    assert occurrence['deadline'] is None


def test_next_occurrences_drops_a_deadline_it_cannot_decode(repeating):
    """No deadline is a smaller lie than a fabricated one."""
    repeating("2026-09-16", deadline=DEADLINE_SENTINEL, rule=b"not a plist")

    (occurrence,) = next_occurrences()

    assert occurrence['deadline'] is None


def test_format_area_gives_a_repeater_its_own_upcoming_section(mocker):
    """Real values from 🧑🏻‍💻 Fast Wombat, checked against the app 2026-08-22.

    Both of the area's scheduled items are repeating templates. Before they
    were projected the area rendered with no Upcoming section at all, because
    a section is emitted only when non-empty -- and a missing section reads as
    "nothing is scheduled here", a stronger and more misleading claim than a
    list that is merely short.
    """
    mocker.patch('things.projects', return_value=[])
    mocker.patch('things.todos', return_value=[])
    mocker.patch('things_mcp.formatters.next_occurrences', return_value=[
        dict(_template('goog', 'Use Google Voice number to keep it'),
             start_date='2026-08-31', repeating=True),
        dict(_template('tax', 'File annual Franchise Tax report by May 15 each year'),
             start_date='2027-03-01', repeating=True),
    ])

    text = format_area({'uuid': 'area-uuid', 'title': '🧑🏻‍💻 Fast Wombat'},
                       include_items=True)

    assert "Upcoming:" in text
    assert "[2026-08-31] Use Google Voice number to keep it" in text
    assert "[2027-03-01] File annual Franchise Tax report by May 15 each year" in text


def test_format_area_asks_only_for_its_own_repeaters(mocker):
    """Narrowed in SQL rather than by filtering all 68 -- get_areas formats
    every area, so a full hydration per area would be paid nine times over."""
    mocker.patch('things.projects', return_value=[])
    mocker.patch('things.todos', return_value=[])
    occurrences = mocker.patch('things_mcp.formatters.next_occurrences', return_value=[])

    format_area({'uuid': 'area-uuid', 'title': 'An area'}, include_items=True)

    occurrences.assert_called_once_with(area='area-uuid')


def test_format_project_includes_its_own_repeaters(mocker):
    mocker.patch('things.tasks', return_value=[])
    mocker.patch('things.todos', return_value=[])
    mocker.patch('things_mcp.formatters.next_occurrences', return_value=[
        dict(_template('build', 'Push a new build every Sunday'), start_date='2026-08-23'),
    ])

    text = format_project({'uuid': 'proj-uuid', 'title': '📱 Roll Play iOS'},
                          include_items=True)

    assert "Push a new build every Sunday" in text
