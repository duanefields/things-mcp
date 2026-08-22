"""Tests for projecting the next occurrence of a repeating task.

things.py filters every repeating task out of every list query, so Today and
Upcoming used to come back missing most of what the app shows. These pin the
projection that fills the hole -- see src/things_mcp/recurrence.py.
"""
import pytest
from tests._helpers import tool_text
from things_mcp.formatters import format_todo
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
        return_value=mocker.Mock(execute_query=lambda sql: rows),
    )
    tasks = mocker.patch('things.tasks', side_effect=lambda uuid: _template(uuid))

    def schedule(date, uuid="tmpl-uuid"):
        rows.append({'uuid': uuid, 'start_date': date})
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
            execute_query=lambda sql: [{'uuid': 'gone', 'start_date': '2026-09-01'}]
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
