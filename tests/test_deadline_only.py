"""Tests for tasks the Upcoming list shows on their deadline.

things.upcoming() is tasks(start_date="future"), so a task carrying a deadline
but no start date never matches it -- 22 rows missing on a real database,
whole projects among them. The app treats such a task as though it were
scheduled on its deadline. See server._deadline_only_upcoming.
"""
import pytest
from tests._helpers import tool_text
from things_mcp.formatters import upcoming_order
from things_mcp.server import get_upcoming


TODAY = "2026-08-22"


def _dated(uuid, title, start_date, today_index=0, index=0, type='to-do'):
    return {'uuid': uuid, 'title': title, 'type': type, 'start': 'Someday',
            'start_date': start_date, 'deadline': None,
            'today_index': today_index, 'index': index}


def _deadline_only(uuid, title, deadline, today_index=0, index=0, type='to-do'):
    return {'uuid': uuid, 'title': title, 'type': type, 'start': 'Anytime',
            'start_date': None, 'deadline': deadline,
            'today_index': today_index, 'index': index}


@pytest.fixture
def upcoming(mocker):
    """Wire get_upcoming's three sources: things.upcoming, the repeating
    projection, and the deadline-only sweep."""
    mocker.patch('things.projects', return_value=[])
    mocker.patch('things_mcp.server.next_occurrences', return_value=[])
    mocker.patch(
        'things_mcp.server.datetime',
        **{'now.return_value.date.return_value.isoformat.return_value': TODAY},
    )

    def wire(dated=(), deadline_only=()):
        mocker.patch('things.upcoming', return_value=list(dated))
        mocker.patch('things.tasks', return_value=list(deadline_only))

    return wire


@pytest.mark.asyncio
async def test_get_upcoming_includes_a_task_with_only_a_deadline(upcoming):
    upcoming(deadline_only=[_deadline_only('a', 'Renew the passport', '2026-09-03')])

    result = tool_text(await get_upcoming())

    assert "Renew the passport" in result
    assert "Deadline: 2026-09-03" in result


@pytest.mark.asyncio
async def test_get_upcoming_does_not_invent_a_start_date(upcoming):
    """The task genuinely has no start date. Reporting the deadline as one
    would be a date Things does not hold."""
    upcoming(deadline_only=[_deadline_only('a', 'Renew the passport', '2026-09-03')])

    result = await get_upcoming()

    (item,) = result.structured_content['items']
    assert item['start_date'] is None
    assert item['deadline_only'] is True
    assert "Start Date:" not in tool_text(result)


@pytest.mark.asyncio
async def test_get_upcoming_positions_it_by_its_deadline(upcoming):
    upcoming(
        dated=[_dated('early', 'Earlier dated todo', '2026-09-01'),
               _dated('late', 'Later dated todo', '2026-12-01')],
        deadline_only=[_deadline_only('mid', 'Due in between', '2026-10-01')],
    )

    result = tool_text(await get_upcoming())

    assert result.index("Earlier dated todo") < result.index("Due in between")
    assert result.index("Due in between") < result.index("Later dated todo")


@pytest.mark.asyncio
async def test_get_upcoming_keeps_one_from_a_someday_project(upcoming, mocker):
    """Someday defers a start, not a deadline. Checked in the app with a task
    created inside a Someday project: it still shows in Upcoming."""
    mocker.patch('things.projects', return_value=[{'uuid': 'someday-proj'}])
    task = _deadline_only('a', 'Renew the passport', '2026-09-03')
    task['project'] = 'someday-proj'
    upcoming(deadline_only=[task])

    assert "Renew the passport" in tool_text(await get_upcoming())


@pytest.mark.asyncio
async def test_get_upcoming_leaves_out_a_deadline_that_has_arrived(upcoming):
    """Upcoming is strictly the future; things.today() already returns a
    deadline-only task from its due date on."""
    upcoming(deadline_only=[_deadline_only('a', 'Due today', TODAY),
                            _deadline_only('b', 'Overdue', '2026-08-01')])

    result = tool_text(await get_upcoming())

    assert "Due today" not in result
    assert "Overdue" not in result


def test_upcoming_order_does_not_hoist_projects_above_todos():
    """Real values from the app's Upcoming view for 2026-08-30, captured
    2026-08-22. formatters.display_order sorts projects above to-dos within a
    group, which is right for an area or project view and wrong here: the app
    leads the day with the 🔑 Spare Jeep Key project and closes it with the
    ✈️ Dublin one. todayIndex already places both.
    """
    day = [
        _dated('jeep', '🔑 Spare Jeep Key', '2026-08-30', -5713, -456, type='project'),
        _dated('zep', 'Perform Zepbound injection', '2026-08-30', -1704, -255),
        _dated('mail', 'Check the mail regularly', '2026-08-30', -1229, -111209),
        _deadline_only('cams', 'Enable indoor cameras before Dublin trip', '2026-08-30', -174, 0),
        _deadline_only('dublin', '✈️ Dublin', '2026-08-30', 0, 26, type='project'),
    ]
    shuffled = [day[4], day[2], day[0], day[3], day[1]]

    ordered = [t['uuid'] for t in upcoming_order(shuffled)]

    assert ordered == ['jeep', 'zep', 'mail', 'cams', 'dublin']
