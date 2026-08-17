from unittest.mock import patch

import pytest

import things_mcp.server as server


@pytest.fixture
def run():
    with patch.object(server.mcp, "run") as mock:
        yield mock


class TestTransportSelection:
    def test_stdio_by_default(self, monkeypatch, run):
        monkeypatch.delenv("THINGS_MCP_TRANSPORT", raising=False)
        server.main()
        run.assert_called_once_with()

    def test_stdio_takes_no_auth(self, monkeypatch, run):
        """Stdio is secured by local execution; auth would be meaningless."""
        monkeypatch.delenv("THINGS_MCP_TRANSPORT", raising=False)
        with patch.object(server, "build_auth") as build:
            server.main()
        build.assert_not_called()

    def test_http_uses_configured_host_and_port(self, monkeypatch, run):
        monkeypatch.setenv("THINGS_MCP_TRANSPORT", "http")
        monkeypatch.setenv("THINGS_MCP_HOST", "0.0.0.0")
        monkeypatch.setenv("THINGS_MCP_PORT", "1234")
        with patch.object(server, "build_auth", return_value=None):
            server.main()
        kwargs = run.call_args.kwargs
        assert kwargs["host"] == "0.0.0.0" and kwargs["port"] == 1234

    def test_http_defaults_to_localhost(self, monkeypatch, run):
        monkeypatch.setenv("THINGS_MCP_TRANSPORT", "http")
        monkeypatch.delenv("THINGS_MCP_HOST", raising=False)
        monkeypatch.delenv("THINGS_MCP_PORT", raising=False)
        with patch.object(server, "build_auth", return_value=None):
            server.main()
        assert run.call_args.kwargs["host"] == "127.0.0.1"

    def test_settings_are_read_at_run_time_not_import_time(self, monkeypatch, run):
        # The environment is often set by a service manager after import.
        monkeypatch.setenv("THINGS_MCP_TRANSPORT", "http")
        monkeypatch.setenv("THINGS_MCP_PORT", "4321")
        with patch.object(server, "build_auth", return_value=None):
            server.main()
        assert run.call_args.kwargs["port"] == 4321


class TestStatelessHttp:
    """Sessions are the wrong model for a remote client dialed from a pool of
    addresses: a request landing from a different address than the one that
    opened the session is rejected, and the connection wedges."""

    def test_stateless_by_default(self, monkeypatch, run):
        monkeypatch.setenv("THINGS_MCP_TRANSPORT", "http")
        monkeypatch.delenv("THINGS_MCP_STATELESS", raising=False)
        with patch.object(server, "build_auth", return_value=None):
            server.main()
        assert run.call_args.kwargs["stateless_http"] is True

    def test_can_be_turned_off(self, monkeypatch, run):
        monkeypatch.setenv("THINGS_MCP_TRANSPORT", "http")
        monkeypatch.setenv("THINGS_MCP_STATELESS", "false")
        with patch.object(server, "build_auth", return_value=None):
            server.main()
        assert run.call_args.kwargs["stateless_http"] is False

    @pytest.mark.parametrize("value", ["FALSE", " false ", "False"])
    def test_the_opt_out_is_forgiving(self, monkeypatch, run, value):
        monkeypatch.setenv("THINGS_MCP_TRANSPORT", "http")
        monkeypatch.setenv("THINGS_MCP_STATELESS", value)
        with patch.object(server, "build_auth", return_value=None):
            server.main()
        assert run.call_args.kwargs["stateless_http"] is False

    @pytest.mark.parametrize("value", ["true", "yes", "1", "anything"])
    def test_anything_other_than_false_stays_stateless(self, monkeypatch, run, value):
        monkeypatch.setenv("THINGS_MCP_TRANSPORT", "http")
        monkeypatch.setenv("THINGS_MCP_STATELESS", value)
        with patch.object(server, "build_auth", return_value=None):
            server.main()
        assert run.call_args.kwargs["stateless_http"] is True


class TestHttpAuth:
    def test_auth_is_attached_for_http(self, monkeypatch, run):
        monkeypatch.setenv("THINGS_MCP_TRANSPORT", "http")
        sentinel = object()
        with patch.object(server, "build_auth", return_value=sentinel):
            server.main()
        assert server.mcp.auth is sentinel
        server.mcp.auth = None
