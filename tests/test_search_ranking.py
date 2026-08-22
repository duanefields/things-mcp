"""Tests for multi-term matching and ranking in search_todos."""

import pytest

from tests._helpers import tool_text
from things_mcp.server import _rank_search_results, search_todos


def todo(title, notes='', uuid=None):
    return {'uuid': uuid or title, 'title': title, 'notes': notes, 'type': 'to-do'}


def titles(items):
    return [i['title'] for i in items]


class TestRanking:
    def test_terms_match_in_any_order(self):
        items = [todo('Call dentist')]
        assert titles(_rank_search_results('dentist call', items)) == ['Call dentist']

    def test_every_term_must_appear(self):
        items = [todo('Call dentist'), todo('Call plumber')]
        assert titles(_rank_search_results('call dentist', items)) == ['Call dentist']

    def test_a_term_may_land_in_the_notes(self):
        items = [todo('Call the office', notes='ask about the dentist referral')]
        assert len(_rank_search_results('call dentist', items)) == 1

    def test_whole_word_outranks_mid_word(self):
        items = [todo('Recalled items'), todo('Call dentist')]
        assert titles(_rank_search_results('call', items)) == ['Call dentist', 'Recalled items']

    def test_prefix_outranks_mid_word(self):
        items = [todo('Recalled items'), todo('Calling card')]
        assert titles(_rank_search_results('call', items)) == ['Calling card', 'Recalled items']

    def test_title_outranks_notes(self):
        items = [todo('Buy milk', notes='at the dentist'), todo('Dentist')]
        assert titles(_rank_search_results('dentist', items))[0] == 'Dentist'

    def test_contiguous_phrase_outranks_scattered_terms(self):
        items = [
            todo('Errands', notes='call the vet, and the dentist too'),
            todo('Call dentist'),
        ]
        assert titles(_rank_search_results('call dentist', items))[0] == 'Call dentist'

    def test_case_insensitive(self):
        assert len(_rank_search_results('DENTIST', [todo('Call dentist')])) == 1

    def test_ties_keep_the_original_order(self):
        items = [todo('Dentist A'), todo('Dentist B'), todo('Dentist C')]
        assert titles(_rank_search_results('dentist', items)) == ['Dentist A', 'Dentist B', 'Dentist C']

    def test_missing_notes_field_is_not_an_error(self):
        assert len(_rank_search_results('dentist', [{'title': 'Dentist', 'uuid': 'x'}])) == 1

    def test_punctuation_only_query_falls_back_to_a_substring(self):
        assert titles(_rank_search_results('?!', [todo('Why?!')])) == ['Why?!']

    def test_empty_query_matches_nothing(self):
        assert _rank_search_results('   ', [todo('Anything')]) == []


class TestSearchTodos:
    @pytest.mark.asyncio
    async def test_narrows_in_sql_with_the_longest_token(self, mocker):
        mock_search = mocker.patch('things.search', return_value=[])

        await search_todos('the dentist')

        mock_search.assert_called_once_with('dentist', include_items=True)

    @pytest.mark.asyncio
    async def test_preserves_the_case_the_user_typed(self, mocker):
        # SQLite's LIKE is case-insensitive for ASCII only.
        mock_search = mocker.patch('things.search', return_value=[])

        await search_todos('Ärzte')

        mock_search.assert_called_once_with('Ärzte', include_items=True)

    @pytest.mark.asyncio
    async def test_area_only_matches_are_dropped(self, mocker):
        # things.py matches AREA.title too, so searching an area's name used to
        # return every task in it.
        mocker.patch('things.search', return_value=[todo('Buy milk'), todo('Work handover')])

        text = tool_text(await search_todos('Work'))

        assert 'Work handover' in text
        assert 'Buy milk' not in text

    @pytest.mark.asyncio
    async def test_multi_term_query_reorders_results(self, mocker):
        mocker.patch(
            'things.search',
            return_value=[todo('Plumber'), todo('Call dentist'), todo('Dentist forms')],
        )

        text = tool_text(await search_todos('dentist call'))

        assert 'Call dentist' in text
        assert 'Dentist forms' not in text
        assert 'Plumber' not in text

    @pytest.mark.asyncio
    async def test_no_matches(self, mocker):
        mocker.patch('things.search', return_value=[])

        assert tool_text(await search_todos('nope')) == "No todos found matching 'nope'"

    @pytest.mark.asyncio
    async def test_blank_query_is_an_error(self, mocker):
        search = mocker.patch('things.search')

        result = await search_todos('   ')

        assert 'at least one character' in tool_text(result)
        search.assert_not_called()

    @pytest.mark.asyncio
    async def test_pagination_still_applies(self, mocker):
        mocker.patch(
            'things.search',
            return_value=[todo(f'Dentist {n}') for n in range(5)],
        )

        result = await search_todos('dentist', limit=2)

        assert 'Showing 1-2 of 5 items' in tool_text(result)
        assert result.structured_content['total'] == 5
