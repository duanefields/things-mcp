"""Tests for trash_item.

Verified against Things 3 before this was built: AppleScript's `delete` and
`move ... to list "Trash"` are the same recoverable operation, and a trashed
item is no longer addressable by AppleScript at all.
"""

import subprocess
from unittest.mock import Mock, patch

import pytest

from things_mcp import url_scheme
from things_mcp.server import trash_item


class TestUrlScheme:
    @patch('subprocess.run')
    def test_moves_a_todo_to_the_trash_list(self, mock_run):
        mock_run.return_value = Mock(stdout="", returncode=0)

        url_scheme.trash_item("ITEM-UUID", "to do")

        args = mock_run.call_args[0][0]
        assert args[0] == "osascript"
        assert 'move to do id "ITEM-UUID" to list "Trash"' in args[2]

    @patch('subprocess.run')
    def test_addresses_a_project_by_its_own_class(self, mock_run):
        mock_run.return_value = Mock(stdout="", returncode=0)

        url_scheme.trash_item("P-UUID", "project")

        assert 'move project id "P-UUID" to list "Trash"' in mock_run.call_args[0][0][2]

    @patch('subprocess.run')
    def test_escapes_quotes_in_the_id(self, mock_run):
        mock_run.return_value = Mock(stdout="", returncode=0)

        url_scheme.trash_item('a"b', "to do")

        assert r'move to do id "a\"b"' in mock_run.call_args[0][0][2]


class TestTool:
    @pytest.mark.asyncio
    async def test_trashes_a_todo(self, mocker):
        mocker.patch('things.get', return_value={
            'uuid': 'u1', 'title': 'Old task', 'type': 'to-do'})
        trash = mocker.patch('things_mcp.server.url_scheme.trash_item')

        result = await trash_item('u1')

        assert result == "Moved to Trash: Old task (id: u1)"
        trash.assert_called_once_with('u1', 'to do')

    @pytest.mark.asyncio
    async def test_trashes_a_project(self, mocker):
        mocker.patch('things.get', return_value={
            'uuid': 'p1', 'title': 'Old project', 'type': 'project'})
        trash = mocker.patch('things_mcp.server.url_scheme.trash_item')

        result = await trash_item('p1')

        assert result == "Moved to Trash: Old project (id: p1)"
        trash.assert_called_once_with('p1', 'project')

    @pytest.mark.asyncio
    async def test_missing_item(self, mocker):
        mocker.patch('things.get', return_value=None)
        trash = mocker.patch('things_mcp.server.url_scheme.trash_item')

        assert "No item found with ID 'nope'" in await trash_item('nope')
        trash.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_trashed_is_not_retried(self, mocker):
        # Things cannot address a trashed item, so the AppleScript would fail.
        mocker.patch('things.get', return_value={
            'uuid': 'u1', 'title': 'Old task', 'type': 'to-do', 'trashed': True})
        trash = mocker.patch('things_mcp.server.url_scheme.trash_item')

        assert await trash_item('u1') == "Already in the Trash: Old task (id: u1)"
        trash.assert_not_called()

    @pytest.mark.asyncio
    async def test_areas_are_refused(self, mocker):
        mocker.patch('things.get', return_value={
            'uuid': 'a1', 'title': 'Work', 'type': 'area'})
        trash = mocker.patch('things_mcp.server.url_scheme.trash_item')

        assert "Cannot trash a area" in await trash_item('a1')
        trash.assert_not_called()
