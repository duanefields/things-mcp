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
async def test_requests_checklist_items(mocker):
    mock_deadlines = mocker.patch('things.deadlines', return_value=[])

    await get_deadlines()

    mock_deadlines.assert_called_once_with(include_items=True)


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
