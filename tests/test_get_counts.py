"""Tests for the tool that sizes the database before anything is fetched.

The list tools return at most 50 rows by default, so a count is what makes the
difference between narrowing on purpose and taking an arbitrary slice.
"""
import pytest
from tests._helpers import tool_text
from things_mcp.server import get_counts


@pytest.fixture
def things_db(mocker):
    lookup = ({'proj-a': 'area-1', 'proj-b': None}, {}, {'area-1': '🌍 Travel'})
    mocker.patch('things_mcp.server.parent_lookup', return_value=lookup)
    mocker.patch('things.projects', return_value=[
        {'uuid': 'proj-a', 'title': '✈️ Dublin', 'area': 'area-1', 'area_title': '🌍 Travel'},
        {'uuid': 'proj-b', 'title': '🐾 Stalled', 'area': None, 'area_title': None},
    ])
    mocker.patch('things.tasks', return_value=[
        {'uuid': 't1', 'title': 'In Dublin', 'type': 'to-do', 'project': 'proj-a'},
        {'uuid': 't2', 'title': 'Also Dublin', 'type': 'to-do', 'project': 'proj-a'},
        {'uuid': 't3', 'title': 'Loose', 'type': 'to-do'},
    ])
    for name in ('inbox', 'today', 'anytime', 'upcoming', 'someday', 'deadlines',
                 'trash', 'todos'):
        mocker.patch(f'things.{name}', return_value=[])


@pytest.mark.asyncio
async def test_counts_areas_by_the_area_todos_fall_under(things_db):
    """Both Dublin todos count toward 🌍 Travel, though neither is filed in it --
    the same resolution get_todos(area_uuid=...) filters on."""
    result = await get_counts()

    (area,) = result.structured_content['areas']
    assert area['title'] == '🌍 Travel'
    assert area['todos'] == 2
    assert area['projects'] == 1


@pytest.mark.asyncio
async def test_counts_report_a_project_with_nothing_open(things_db):
    """A zero is worth reporting: that is a stalled project."""
    result = await get_counts()

    stalled = [p for p in result.structured_content['projects'] if p['title'] == '🐾 Stalled']
    assert stalled and stalled[0]['todos'] == 0


@pytest.mark.asyncio
async def test_counts_report_todos_under_no_area(things_db):
    result = await get_counts()

    assert result.structured_content['unassigned_todos'] == 1


@pytest.mark.asyncio
async def test_counts_include_uuids_so_they_can_be_acted_on(things_db):
    """Also the way to find an area or project id by name."""
    text = tool_text(await get_counts())

    assert 'area-1' in text
    assert 'proj-a' in text


@pytest.mark.asyncio
async def test_a_list_that_cannot_be_counted_reports_none(things_db, mocker):
    """One broken list should not take the whole overview down with it."""
    mocker.patch('things.inbox', side_effect=RuntimeError("db locked"))

    result = await get_counts()

    assert result.structured_content['lists']['inbox'] is None
    assert result.structured_content['lists']['today'] == 0
