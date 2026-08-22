"""Tests for get_item, the single-item read by ID."""

import json

import pytest

from tests._helpers import tool_text
from things_mcp.server import get_item


@pytest.mark.asyncio
async def test_formats_a_todo(mocker, mock_todo):
    mocker.patch('things.get', return_value=mock_todo)

    result = await get_item('test-todo-uuid')

    text = tool_text(result)
    assert "Title: Test Todo" in text
    assert "UUID: test-todo-uuid" in text
    assert "First item" in text


@pytest.mark.asyncio
async def test_passes_include_items_through_to_things(mocker, mock_todo):
    mock_get = mocker.patch('things.get', return_value=mock_todo)

    await get_item('test-todo-uuid', include_items=False)

    # format_todo makes its own things.get calls for the parent project and
    # area, so only the first call is ours.
    assert mock_get.call_args_list[0].args == ('test-todo-uuid',)
    assert mock_get.call_args_list[0].kwargs == {'include_items': False}


@pytest.mark.asyncio
async def test_formats_a_project(mocker, mock_project):
    mocker.patch('things.get', return_value=mock_project)
    mocker.patch('things.todos', return_value=[])

    text = tool_text(await get_item('test-project-uuid'))

    assert "Title: Test Project" in text
    assert "Notes: Project description" in text


@pytest.mark.asyncio
async def test_formats_an_area(mocker, mock_area):
    mocker.patch('things.get', return_value=mock_area)
    mocker.patch('things.projects', return_value=[])
    mocker.patch('things.todos', return_value=[])

    text = tool_text(await get_item('test-area-uuid'))

    assert "Title: Test Area" in text


@pytest.mark.asyncio
async def test_formats_a_tag(mocker, mock_tag):
    mocker.patch('things.get', return_value=mock_tag)
    mocker.patch('things.todos', return_value=[])

    text = tool_text(await get_item('test-tag-uuid'))

    assert "Title: work" in text
    assert "Shortcut: cmd+1" in text


@pytest.mark.asyncio
async def test_formats_a_heading(mocker):
    heading = {'uuid': 'h-uuid', 'title': 'Test Heading', 'type': 'heading',
               'project': 'test-project-uuid', 'project_title': 'Test Project'}
    mocker.patch('things.get', return_value=heading)
    mocker.patch('things.todos', return_value=[])

    text = tool_text(await get_item('h-uuid'))

    assert "Title: Test Heading" in text
    assert "Type: heading" in text
    assert "Project: Test Project" in text


@pytest.mark.asyncio
async def test_missing_item_is_an_error(mocker):
    mocker.patch('things.get', return_value=None)

    result = await get_item('no-such-uuid')

    assert "No item found with ID 'no-such-uuid'" in tool_text(result)
    assert result.structured_content["error"]


@pytest.mark.asyncio
async def test_structured_content_matches_the_other_read_tools(mocker, mock_todo):
    mocker.patch('things.get', return_value=mock_todo)

    sc = (await get_item('test-todo-uuid')).structured_content

    assert sc["count"] == 1
    assert sc["total"] == 1
    assert sc["offset"] == 0
    assert sc["limit"] is None
    assert sc["items"][0]["uuid"] == mock_todo["uuid"]
    json.dumps(sc)  # must not raise
