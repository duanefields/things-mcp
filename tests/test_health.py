import json
import subprocess
from unittest.mock import patch

import pytest

from things_mcp import url_scheme
from things_mcp.server import _things_is_running, _wal_age_seconds, health


@pytest.fixture(autouse=True)
def reset_dispatch():
    """Dispatch state is module-level; keep tests from leaking into each other."""
    url_scheme._last_dispatch.update({"at": None, "ok": None, "error": None})
    yield
    url_scheme._last_dispatch.update({"at": None, "ok": None, "error": None})


async def health_json():
    response = await health(None)
    return json.loads(response.body)


@pytest.fixture
def ambient_health():
    """Pin the machine-dependent halves of the report.

    Tests that care only about the dispatch record still render the whole
    response, and without this they read the real process list and the real
    database file.
    """
    with patch("things_mcp.server._things_is_running", return_value=True), patch(
        "things_mcp.server._wal_age_seconds", return_value=12.0
    ):
        yield


class TestDispatchRecording:
    def test_records_success(self):
        with patch("subprocess.run"):
            url_scheme.execute_url("things:///show?id=inbox")
        record = url_scheme.last_dispatch()
        assert record["ok"] is True
        assert record["error"] is None
        assert record["at"] is not None

    def test_records_failure_and_still_raises(self):
        # Both the osascript path and the direct fallback fail.
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "open"),
        ):
            with pytest.raises(subprocess.CalledProcessError):
                url_scheme.execute_url("things:///add?title=x")
        record = url_scheme.last_dispatch()
        assert record["ok"] is False
        assert record["error"]

    def test_fallback_success_is_recorded_as_success(self):
        # osascript fails, the plain `open -g` fallback succeeds.
        with patch(
            "subprocess.run",
            side_effect=[subprocess.CalledProcessError(1, "osascript"), None],
        ):
            url_scheme.execute_url("things:///add?title=x")
        assert url_scheme.last_dispatch()["ok"] is True

    def test_last_dispatch_returns_a_copy(self):
        url_scheme.last_dispatch()["ok"] = "tampered"
        assert url_scheme.last_dispatch()["ok"] is None


class TestHelpers:
    def test_things_running_true(self):
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)):
            assert _things_is_running() is True

    def test_things_running_false(self):
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1)):
            assert _things_is_running() is False

    def test_wal_age_is_a_number(self):
        age = _wal_age_seconds()
        assert age is None or age >= 0

    def test_wal_age_none_when_missing(self):
        with patch("things_mcp.server.Path") as fake_path:
            fake_path.return_value.stat.side_effect = OSError
            assert _wal_age_seconds() is None

    def test_wal_age_none_when_database_path_does_not_exist(self, monkeypatch):
        monkeypatch.setenv("THINGSDB", "/nonexistent/main.sqlite")
        assert _wal_age_seconds() is None

    def test_wal_age_does_not_open_the_database(self):
        # The endpoint reports on an unreachable database, so reading the path
        # must not go through Database(), whose constructor opens SQLite.
        with patch("things.database.Database", side_effect=AssertionError("opened")):
            _wal_age_seconds()


class TestHealthEndpoint:
    async def test_reports_ok_when_healthy(self):
        with patch("things_mcp.server._things_is_running", return_value=True), patch(
            "things_mcp.server._wal_age_seconds", return_value=12.0
        ):
            body = await health_json()
        assert body["status"] == "ok"
        assert body["things_running"] is True
        assert body["database_wal_age_seconds"] == 12.0

    async def test_degraded_when_things_is_not_running(self):
        # The failure that matters: writes are dispatched into nothing.
        with patch("things_mcp.server._things_is_running", return_value=False), patch(
            "things_mcp.server._wal_age_seconds", return_value=12.0
        ):
            body = await health_json()
        assert body["status"] == "degraded"
        assert body["things_running"] is False

    async def test_degraded_when_database_is_unreadable(self):
        with patch("things_mcp.server._things_is_running", return_value=True), patch(
            "things_mcp.server._wal_age_seconds", return_value=None
        ):
            body = await health_json()
        assert body["status"] == "degraded"

    async def test_surfaces_a_failed_write(self, ambient_health):
        with patch(
            "subprocess.run", side_effect=subprocess.CalledProcessError(1, "open")
        ):
            with pytest.raises(subprocess.CalledProcessError):
                url_scheme.execute_url("things:///add?title=x")
        body = await health_json()
        assert body["last_write_dispatch"]["ok"] is False
        assert body["last_write_dispatch"]["at"] is not None
        assert body["last_write_dispatch"]["error"]

    async def test_no_writes_yet_is_not_an_error(self, ambient_health):
        body = await health_json()
        assert body["last_write_dispatch"] == {"at": None, "ok": None, "error": None}
