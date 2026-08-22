"""Tests for get_deadlines."""

from datetime import datetime, timedelta

import pytest

from tests._helpers import tool_text
from things_mcp.server import get_deadlines


def _dated(title, days_out, uuid=None):
    deadline = (datetime.now().date() + timedelta(days=days_out)).isoformat()
    return {
        'uuid': uuid or f'uuid-{title}',
        'title': title,
        'type': 'to-do',
        'status': 'incomplete',
        'deadline': deadline,
    }


@pytest.mark.asyncio
async def test_lists_deadlines(mocker):
    mocker.patch('things.deadlines', return_value=[_dated('Overdue', -3), _dated('Soon', 2)])

    text = tool_text(await get_deadlines())

    assert "Title: Overdue" in text
    assert "Title: Soon" in text


@pytest.mark.asyncio
async def test_does_not_ask_things_py_to_walk_every_project(mocker):
    """include_items also hydrates each returned project's to-dos and headings,
    which nothing here renders. Checklists are filled in per page instead."""
    mock_deadlines = mocker.patch('things.deadlines', return_value=[])

    await get_deadlines()

    mock_deadlines.assert_called_once_with()


@pytest.mark.asyncio
async def test_within_days_filters_out_later_deadlines(mocker):
    mocker.patch('things.deadlines', return_value=[_dated('Soon', 2), _dated('Later', 30)])

    text = tool_text(await get_deadlines(within_days=7))

    assert "Title: Soon" in text
    assert "Title: Later" not in text


@pytest.mark.asyncio
async def test_within_days_keeps_overdue_items(mocker):
    mocker.patch('things.deadlines', return_value=[_dated('Overdue', -30)])

    text = tool_text(await get_deadlines(within_days=1))

    assert "Title: Overdue" in text


@pytest.mark.asyncio
async def test_within_days_zero_is_today_only(mocker):
    mocker.patch('things.deadlines', return_value=[_dated('Today', 0), _dated('Tomorrow', 1)])

    text = tool_text(await get_deadlines(within_days=0))

    assert "Title: Today" in text
    assert "Title: Tomorrow" not in text


@pytest.mark.asyncio
async def test_negative_within_days_is_an_error(mocker):
    mocker.patch('things.deadlines', return_value=[])

    result = await get_deadlines(within_days=-1)

    assert "within_days must be zero or a positive integer" in tool_text(result)


@pytest.mark.asyncio
async def test_empty_message(mocker):
    mocker.patch('things.deadlines', return_value=[])

    assert tool_text(await get_deadlines()) == "No deadlines found"


@pytest.mark.asyncio
async def test_pagination_and_structured_content(mocker):
    mocker.patch(
        'things.deadlines',
        return_value=[_dated('A', 1), _dated('B', 2), _dated('C', 3)],
    )

    result = await get_deadlines(limit=2)

    assert "Showing 1-2 of 3 items" in tool_text(result)
    sc = result.structured_content
    assert sc["count"] == 2
    assert sc["total"] == 3
    assert sc["limit"] == 2


@pytest.mark.asyncio
async def test_invalid_limit_is_an_error(mocker):
    mocker.patch('things.deadlines', return_value=[])

    assert "limit must be a positive integer" in tool_text(await get_deadlines(limit=0))


@pytest.mark.asyncio
async def test_a_project_does_not_drag_its_children_into_the_payload(mocker):
    """include_items is for a to-do's checklist. On a project it hangs every
    child off an item that is due, and those children are not themselves due --
    format_todo does not render them, and a project's own to-dos with deadlines
    already appear in this list in their own right."""
    project = _dated('Gage College', 2)
    project['type'] = 'project'
    project['items'] = [{'uuid': 'child', 'title': 'Pay balance', 'type': 'to-do'}]
    mocker.patch('things.deadlines', return_value=[project])
    mocker.patch('things.todos', return_value=[])
    mocker.patch('things.tasks', return_value=[])

    result = await get_deadlines()

    assert 'items' not in result.structured_content['items'][0]
    assert 'Pay balance' not in tool_text(result)


@pytest.mark.asyncio
async def test_a_todo_keeps_its_checklist(mocker):
    todo = _dated('Packing list', 2)
    todo['checklist'] = [{'title': 'Passport', 'status': 'incomplete'}]
    mocker.patch('things.deadlines', return_value=[todo])

    result = await get_deadlines()

    assert result.structured_content['items'][0]['checklist']
    assert 'Passport' in tool_text(result)
