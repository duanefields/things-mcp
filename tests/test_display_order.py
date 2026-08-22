"""Ordering of todos within a project, an area, and the Upcoming list.

things.py orders by TASK.index alone. That is a manual position and says nothing
about scheduling state, so it interleaves the three groups Things displays
separately -- and inside a project it is relative to a todo's own heading, so it
does not even reproduce the heading grouping.
"""

import pytest

from tests._helpers import tool_text
from things_mcp.formatters import display_order, schedule_group
from things_mcp.server import (
    _project_display_order, get_anytime, get_todos, get_upcoming,
)


def todo(title, index=0, start='Anytime', start_date=None, heading=None):
    return {'uuid': title, 'title': title, 'type': 'to-do', 'index': index,
            'start': start, 'start_date': start_date, 'heading': heading}


def anytime(title, index=0, **kw):
    return todo(title, index, 'Anytime', **kw)


def scheduled(title, date, index=0, heading=None, today_index=0):
    # Things stores a scheduled item as Someday plus a start_date.
    item = todo(title, index, 'Someday', start_date=date, heading=heading)
    item['today_index'] = today_index
    return item


def someday(title, index=0, heading=None):
    return todo(title, index, 'Someday', heading=heading)


def titles(items):
    return [i['title'] for i in items]


class TestScheduleGroup:
    def test_anytime(self):
        assert schedule_group(anytime('a')) == 0

    def test_scheduled_is_someday_with_a_date(self):
        assert schedule_group(scheduled('a', '2026-08-28')) == 1

    def test_someday_is_someday_without_one(self):
        assert schedule_group(someday('a')) == 2

    def test_a_todo_scheduled_for_today_is_still_anytime(self):
        # That is how Things represents Today: start Anytime, plus a date.
        assert schedule_group(anytime('a', start_date='2026-08-22')) == 0


class TestDisplayOrder:
    def test_groups_come_in_order(self):
        items = [someday('s', 1), scheduled('u', '2026-08-28', 2), anytime('a', 3)]
        assert titles(display_order(items)) == ['a', 'u', 's']

    def test_anytime_keeps_its_manual_order(self):
        items = [anytime('third', 30), anytime('first', 10), anytime('second', 20)]
        assert titles(display_order(items)) == ['first', 'second', 'third']

    def test_scheduled_sorts_by_date_not_index(self):
        items = [scheduled('december', '2026-12-15', -143775),
                 scheduled('august', '2026-08-23', -2103)]
        assert titles(display_order(items)) == ['august', 'december']

    def test_scheduled_within_a_date_uses_today_index_not_index(self):
        """Taken from the app's own display of Dublin's General Travel Prep.

        `index` orders these 0, 0, 4060; Things shows 4060 first. todayIndex is
        the manual position in the date-based views, and it is what these follow.
        """
        items = [
            scheduled('pause bumble', '2026-08-29', index=0, today_index=-2258),
            scheduled('check in', '2026-08-29', index=0, today_index=-2477),
            scheduled('zepbound', '2026-08-29', index=4060, today_index=-2811),
        ]
        assert titles(display_order(items)) == ['zepbound', 'check in', 'pause bumble']

    def test_date_still_outranks_today_index(self):
        # Across dates todayIndex is not ascending: -279 on the 28th precedes
        # -2811 on the 29th.
        items = [scheduled('later', '2026-08-29', today_index=-2811),
                 scheduled('sooner', '2026-08-28', today_index=-279)]
        assert titles(display_order(items)) == ['sooner', 'later']

    def test_anytime_ignores_today_index(self):
        """The other half of the same rule, from the same heading.

        Those Anytime items carry todayIndex values of -174, -437, -286 and 0,
        which do not match the order the app shows them in; their `index` does.
        """
        items = [anytime('first', index=0), anytime('second', index=2127),
                 anytime('third', index=2206)]
        for item, today in zip(items, (-174, -437, -286)):
            item['today_index'] = today
        assert titles(display_order(items)) == ['first', 'second', 'third']

    def test_someday_keeps_its_manual_order(self):
        items = [someday('second', 20), someday('first', 10)]
        assert titles(display_order(items)) == ['first', 'second']

    def test_a_dated_anytime_item_does_not_jump_position(self):
        # Its start_date must stay out of the sort key, or Today's items would
        # be pulled out of the manual order Things shows them in.
        items = [anytime('first', 10), anytime('today', 20, start_date='2026-08-22'),
                 anytime('third', 30)]
        assert titles(display_order(items)) == ['first', 'today', 'third']

    def test_index_is_not_evidence_of_group(self):
        # Taken from a real project: a scheduled item at index -818 sitting
        # above Anytime items at 2127.
        items = [scheduled('haircut', '2026-08-28', -818), anytime('laptop', 2127)]
        assert titles(display_order(items)) == ['laptop', 'haircut']

    def test_missing_index_sorts_last_within_its_group(self):
        items = [{'title': 'no index', 'start': 'Anytime'}, anytime('has index', 5)]
        assert titles(display_order(items)) == ['has index', 'no index']

    def test_empty(self):
        assert display_order([]) == []


class TestProjectsSortAboveTodos:
    """Taken from the app's own display of the Hobbies area.

    `index` does not express this and gets it wrong in both directions.
    """

    def project(self, title, index=0, start='Anytime'):
        return {'uuid': title, 'title': title, 'type': 'project',
                'index': index, 'start': start, 'start_date': None}

    def test_anytime_projects_precede_anytime_todos_with_lower_indexes(self):
        # Sell Games sits at index 3604, above to-dos at 481 and 1903.
        items = [anytime('bgg con', 481), anytime('dotfiles', 1903),
                 self.project('sell games', 3604)]
        assert titles(display_order(items)) == ['sell games', 'bgg con', 'dotfiles']

    def test_a_someday_project_heads_the_someday_group(self):
        # Learn Fields of Fire at -4376, above Someday to-dos at -149312.
        items = [someday('install n8n', -149312),
                 self.project('learn fields of fire', -4376, start='Someday')]
        assert titles(display_order(items)) == ['learn fields of fire', 'install n8n']

    def test_a_someday_project_does_not_rise_above_anytime_todos(self):
        """The whole point: grouping is the outer axis, kind the inner one.

        Sorting projects to the top of the container would put this one first.
        """
        items = [self.project('someday project', 0, start='Someday'),
                 anytime('anytime todo', 99999)]
        assert titles(display_order(items)) == ['anytime todo', 'someday project']


class TestProjectDisplayOrder:
    def test_loose_todos_come_before_any_heading(self):
        headings = [{'uuid': 'h1', 'title': 'H', 'index': 0}]
        items = [anytime('under', 0, heading='h1'), anytime('loose', 5)]
        assert titles(_project_display_order(items, headings)) == ['loose', 'under']

    def test_headings_follow_their_own_index(self):
        headings = [{'uuid': 'hb', 'title': 'B', 'index': 0},
                    {'uuid': 'ha', 'title': 'A', 'index': -631}]
        items = [anytime('b1', 0, heading='hb'), anytime('a1', 0, heading='ha')]
        assert titles(_project_display_order(items, headings)) == ['a1', 'b1']

    def test_index_is_relative_to_the_heading(self):
        # Both first-under-their-heading, so both are index 0; only the heading
        # order separates them.
        headings = [{'uuid': 'ha', 'title': 'A', 'index': 0},
                    {'uuid': 'hb', 'title': 'B', 'index': 1}]
        items = [anytime('b1', 0, heading='hb'), anytime('a2', 1, heading='ha'),
                 anytime('a1', 0, heading='ha')]
        assert titles(_project_display_order(items, headings)) == ['a1', 'a2', 'b1']

    def test_grouping_applies_within_each_heading(self):
        headings = [{'uuid': 'ha', 'title': 'A', 'index': 0},
                    {'uuid': 'hb', 'title': 'B', 'index': 1}]
        items = [
            someday('a-someday', 0, heading='ha'),
            anytime('a-anytime', 1, heading='ha'),
            scheduled('a-sched', '2026-08-28', 2, heading='ha'),
            anytime('b-anytime', 0, heading='hb'),
        ]
        assert titles(_project_display_order(items, headings)) == [
            'a-anytime', 'a-sched', 'a-someday', 'b-anytime',
        ]

    def test_a_someday_item_does_not_escape_its_heading(self):
        headings = [{'uuid': 'ha', 'title': 'A', 'index': 0},
                    {'uuid': 'hb', 'title': 'B', 'index': 1}]
        items = [someday('a-someday', 0, heading='ha'), anytime('b1', 0, heading='hb')]
        assert titles(_project_display_order(items, headings)) == ['a-someday', 'b1']

    def test_a_todo_whose_heading_is_missing_is_not_dropped(self):
        items = [anytime('orphan', 0, heading='gone'), anytime('loose', 0)]
        got = titles(_project_display_order(items, []))
        assert sorted(got) == ['loose', 'orphan']
        assert got[0] == 'loose'

    def test_empty_project(self):
        assert _project_display_order([], []) == []


class TestGetTodos:
    @pytest.mark.asyncio
    async def test_project_fetch_is_ordered_and_reads_headings(self, mocker):
        mocker.patch('things.get', return_value={'uuid': 'p1', 'type': 'project'})
        mocker.patch('things.todos', return_value=[
            someday('s', 0), anytime('a', 1)])
        tasks = mocker.patch('things.tasks', return_value=[])

        text = tool_text(await get_todos(project_uuid='p1'))

        assert text.index('Title: a') < text.index('Title: s')
        tasks.assert_called_once_with(type='heading', project='p1')

    @pytest.mark.asyncio
    async def test_unfiltered_fetch_is_grouped_without_reading_headings(self, mocker):
        # Across every project an index is only meaningful within its own
        # heading, so there is no structure to rebuild -- but the groups apply.
        mocker.patch('things.todos', return_value=[someday('s', 0), anytime('a', 1)])
        tasks = mocker.patch('things.tasks', return_value=[])

        text = tool_text(await get_todos())

        assert text.index('Title: a') < text.index('Title: s')
        tasks.assert_not_called()


class TestGetUpcoming:
    @pytest.mark.asyncio
    async def test_sorts_by_date_rather_than_index(self, mocker):
        # Every item in Upcoming is scheduled, so date order is the whole point
        # of the view; things.py returned December ahead of August.
        mocker.patch('things.upcoming', return_value=[
            scheduled('december', '2026-12-15', -143775),
            scheduled('august', '2026-08-23', -2103)])
        mocker.patch('things.projects', return_value=[])
        mocker.patch('things.tasks', return_value=[])

        text = tool_text(await get_upcoming())

        assert text.index('Title: august') < text.index('Title: december')


class TestAnytimeExcludesHeadings:
    @pytest.mark.asyncio
    async def test_headings_are_not_returned_as_tasks(self, mocker):
        """things.anytime() is tasks(start="Anytime") with no type filter, so it
        also returns every heading in an Anytime project. The app shows none."""
        mocker.patch('things.anytime', return_value=[
            anytime('a real task', 0),
            {'uuid': 'h1', 'title': 'Onboarding', 'type': 'heading',
             'index': 1, 'start': 'Anytime'},
        ])
        mocker.patch('things.projects', return_value=[])
        mocker.patch('things.tasks', return_value=[])

        result = await get_anytime()

        assert [i['title'] for i in result.structured_content['items']] == ['a real task']
