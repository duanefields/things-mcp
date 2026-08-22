"""Tests for the area a task falls under, including the one it inherits.

A to-do almost never carries an area of its own -- 88 of 676 on a real
database. Answering an area question from the `area` column alone returned 2
of the 183 to-dos under 🧑🏻‍💻 Fast Wombat. See src/things_mcp/areas.py.
"""
import pytest
from tests._helpers import tool_text
from things_mcp.areas import annotate, effective_area, effective_project
from things_mcp.server import get_todos, search_advanced


PROJECTS = {'proj-in-area': 'the-area', 'proj-no-area': None}
HEADINGS = {'head-1': 'proj-in-area'}
TITLES = {'the-area': '🧑🏻‍💻 Fast Wombat'}
LOOKUP = (PROJECTS, HEADINGS, TITLES)


class TestEffectiveArea:
    def test_a_task_filed_straight_in_an_area_keeps_it(self):
        task = {'area': 'the-area', 'project': 'proj-no-area'}
        assert effective_area(task, PROJECTS, HEADINGS) == 'the-area'

    def test_a_task_in_a_project_inherits_the_projects_area(self):
        """The common case: 98% of to-dos resolve this way or via a heading."""
        assert effective_area({'project': 'proj-in-area'}, PROJECTS, HEADINGS) == 'the-area'

    def test_a_task_under_a_heading_resolves_through_the_heading(self):
        """A task under a heading has no project of its own; the heading holds it."""
        assert effective_area({'heading': 'head-1'}, PROJECTS, HEADINGS) == 'the-area'

    def test_a_project_with_no_area_resolves_to_none(self):
        """A project need not be in an area, so the answer can still be nothing."""
        assert effective_area({'project': 'proj-no-area'}, PROJECTS, HEADINGS) is None

    def test_a_loose_task_resolves_to_none(self):
        assert effective_area({'title': 'in the inbox'}, PROJECTS, HEADINGS) is None


def test_effective_project_resolves_through_a_heading():
    assert effective_project({'heading': 'head-1'}, HEADINGS) == 'proj-in-area'
    assert effective_project({'project': 'proj-no-area'}, HEADINGS) == 'proj-no-area'
    assert effective_project({}, HEADINGS) is None


def test_annotate_adds_the_resolved_fields_without_touching_area():
    """`area` reports what Things stores. A to-do in a project is not filed in
    that project's area, and overwriting it would misreport the database."""
    tasks = [{'uuid': 't1', 'project': 'proj-in-area', 'area': None}]

    (task,) = annotate(tasks, lookup=LOOKUP)

    assert task['area'] is None
    assert task['effective_area'] == 'the-area'
    assert task['effective_area_title'] == '🧑🏻‍💻 Fast Wombat'
    assert task['effective_project'] == 'proj-in-area'


def test_annotate_leaves_already_resolved_tasks_alone():
    """Runs again on a page that was annotated before it was filtered."""
    tasks = [{'uuid': 't1', 'effective_area': 'kept', 'effective_area_title': 'Kept'}]

    (task,) = annotate(tasks, lookup=LOOKUP)

    assert task['effective_area'] == 'kept'


@pytest.mark.asyncio
async def test_get_todos_by_area_reaches_into_that_areas_projects(mocker):
    mocker.patch('things_mcp.server.parent_lookup', return_value=LOOKUP)
    mocker.patch('things_mcp.areas.parent_lookup', return_value=LOOKUP)
    mocker.patch('things.todos', return_value=[
        {'uuid': 'a', 'title': 'Filed in the area', 'type': 'to-do', 'area': 'the-area'},
        {'uuid': 'b', 'title': 'Inside a project', 'type': 'to-do', 'project': 'proj-in-area'},
        {'uuid': 'c', 'title': 'Under a heading', 'type': 'to-do', 'heading': 'head-1'},
        {'uuid': 'd', 'title': 'Somewhere else', 'type': 'to-do', 'project': 'proj-no-area'},
    ])

    result = tool_text(await get_todos(area_uuid='the-area'))

    assert "Filed in the area" in result
    assert "Inside a project" in result
    assert "Under a heading" in result
    assert "Somewhere else" not in result


@pytest.mark.asyncio
async def test_search_advanced_by_area_does_the_same(mocker):
    mocker.patch('things_mcp.server.parent_lookup', return_value=LOOKUP)
    mocker.patch('things_mcp.areas.parent_lookup', return_value=LOOKUP)
    mocker.patch('things.todos', return_value=[
        {'uuid': 'b', 'title': 'Inside a project', 'type': 'to-do', 'project': 'proj-in-area'},
        {'uuid': 'd', 'title': 'Somewhere else', 'type': 'to-do', 'project': 'proj-no-area'},
    ])

    result = tool_text(await search_advanced(area='the-area', status='incomplete'))

    assert "Inside a project" in result
    assert "Somewhere else" not in result
