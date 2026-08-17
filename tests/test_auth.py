import json

import pytest
from mcp.server.auth.provider import AuthorizationParams, AuthorizeError
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyHttpUrl

from things_mcp.auth import SCOPE, PasswordOAuthProvider, build_auth

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def provider(tmp_path):
    return PasswordOAuthProvider(
        password=PASSWORD,
        base_url="https://things.example.com",
        state_path=tmp_path / "oauth-state.json",
    )


@pytest.fixture
def client():
    return OAuthClientInformationFull(
        client_id="client-1",
        client_name="Test Client",
        redirect_uris=[AnyHttpUrl(REDIRECT)],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )


def make_params():
    return AuthorizationParams(
        state="state-value",
        scopes=[SCOPE],
        code_challenge="challenge-value",
        redirect_uri=AnyHttpUrl(REDIRECT),
        redirect_uri_provided_explicitly=True,
    )


# ---------------------------------------------------------------- build_auth


class TestBuildAuth:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("THINGS_MCP_AUTH", raising=False)
        assert build_auth() is None

    @pytest.mark.parametrize("value", ["", "none", "NONE", " none "])
    def test_explicitly_disabled(self, monkeypatch, value):
        monkeypatch.setenv("THINGS_MCP_AUTH", value)
        assert build_auth() is None

    def test_unknown_mode_is_rejected(self, monkeypatch):
        monkeypatch.setenv("THINGS_MCP_AUTH", "magic")
        with pytest.raises(ValueError, match="Unknown THINGS_MCP_AUTH"):
            build_auth()

    def test_password_mode_requires_a_password(self, monkeypatch):
        monkeypatch.setenv("THINGS_MCP_AUTH", "password")
        monkeypatch.delenv("THINGS_MCP_PASSWORD", raising=False)
        with pytest.raises(ValueError, match="THINGS_MCP_PASSWORD"):
            build_auth()

    def test_password_mode_requires_a_base_url(self, monkeypatch):
        monkeypatch.setenv("THINGS_MCP_AUTH", "password")
        monkeypatch.setenv("THINGS_MCP_PASSWORD", PASSWORD)
        monkeypatch.delenv("THINGS_MCP_BASE_URL", raising=False)
        with pytest.raises(ValueError, match="THINGS_MCP_BASE_URL"):
            build_auth()

    def test_builds_provider(self, monkeypatch, tmp_path):
        monkeypatch.setenv("THINGS_MCP_AUTH", "password")
        monkeypatch.setenv("THINGS_MCP_PASSWORD", PASSWORD)
        monkeypatch.setenv("THINGS_MCP_BASE_URL", "https://things.example.com/")
        monkeypatch.setenv("THINGS_MCP_STATE_DIR", str(tmp_path))
        auth = build_auth()
        assert isinstance(auth, PasswordOAuthProvider)
        # A trailing slash on the configured URL must not produce a doubled slash.
        assert auth._base() == "https://things.example.com"

    def test_dynamic_client_registration_is_enabled(self, provider):
        # Without this, MCP clients cannot self-register and fail with a bare
        # invalid_client that says nothing about the cause.
        assert provider.client_registration_options.enabled is True

    def test_serves_the_routes_clients_probe(self, provider):
        paths = {r.path for r in provider.get_routes("/mcp")}
        assert {"/authorize", "/token", "/register", "/login"} <= paths
        assert "/.well-known/oauth-authorization-server" in paths
        assert "/.well-known/oauth-protected-resource/mcp" in paths


# ------------------------------------------------------------- authorization


class TestAuthorize:
    async def test_parks_the_request_instead_of_issuing_a_code(self, provider, client):
        await provider.register_client(client)
        target = await provider.authorize(client, make_params())
        assert target.startswith("https://things.example.com/login?txn=")
        # Nothing is grantable until the password has been checked.
        assert provider.auth_codes == {}

    async def test_rejects_an_unregistered_client(self, provider, client):
        with pytest.raises(AuthorizeError):
            await provider.authorize(client, make_params())

    async def test_each_request_gets_a_distinct_transaction(self, provider, client):
        await provider.register_client(client)
        first = await provider.authorize(client, make_params())
        second = await provider.authorize(client, make_params())
        assert first != second
        assert len(provider._pending_logins) == 2


# --------------------------------------------------------------------- login


async def start_login(provider, client):
    await provider.register_client(client)
    target = await provider.authorize(client, make_params())
    return target.split("txn=")[1]


class FakeRequest:
    """Minimal stand-in for a Starlette request carrying a submitted form."""

    def __init__(self, form, method="POST"):
        self.method = method
        self._form = form
        self.headers = {}
        self.client = None
        self.query_params = {}

    async def form(self):
        return self._form


class TestLogin:
    async def test_correct_password_issues_a_code(self, provider, client):
        txn = await start_login(provider, client)
        response = await provider._handle_login(
            FakeRequest({"txn": txn, "password": PASSWORD})
        )
        assert response.status_code == 302
        location = response.headers["location"]
        assert location.startswith(REDIRECT)
        assert "state=state-value" in location
        assert len(provider.auth_codes) == 1

    async def test_wrong_password_issues_nothing(self, provider, client, monkeypatch):
        monkeypatch.setattr("things_mcp.auth.FAILED_LOGIN_DELAY_SECONDS", 0)
        txn = await start_login(provider, client)
        response = await provider._handle_login(
            FakeRequest({"txn": txn, "password": "wrong"})
        )
        assert response.status_code == 401
        assert provider.auth_codes == {}
        # The transaction survives so a typo does not force restarting the flow.
        assert txn in provider._pending_logins

    async def test_unknown_transaction_is_refused(self, provider):
        response = await provider._handle_login(
            FakeRequest({"txn": "made-up", "password": PASSWORD})
        )
        assert response.status_code == 400
        assert provider.auth_codes == {}

    async def test_transaction_is_single_use(self, provider, client):
        txn = await start_login(provider, client)
        await provider._handle_login(FakeRequest({"txn": txn, "password": PASSWORD}))
        again = await provider._handle_login(
            FakeRequest({"txn": txn, "password": PASSWORD})
        )
        assert again.status_code == 400

    async def test_expired_transactions_are_dropped(self, provider, client, monkeypatch):
        await start_login(provider, client)
        monkeypatch.setattr("things_mcp.auth.LOGIN_TXN_TTL_SECONDS", -1)
        await provider.authorize(client, make_params())
        assert len(provider._pending_logins) == 1


# ------------------------------------------------------------------- tokens


class TestTokens:
    async def test_code_exchange_then_replay(self, provider, client):
        txn = await start_login(provider, client)
        await provider._handle_login(FakeRequest({"txn": txn, "password": PASSWORD}))
        code_value = next(iter(provider.auth_codes))
        code = await provider.load_authorization_code(client, code_value)
        assert code is not None

        token = await provider.exchange_authorization_code(client, code)
        assert token.access_token
        assert token.scope == SCOPE
        assert await provider.load_authorization_code(client, code_value) is None

    async def test_refresh_rotates(self, provider, client):
        txn = await start_login(provider, client)
        await provider._handle_login(FakeRequest({"txn": txn, "password": PASSWORD}))
        code = await provider.load_authorization_code(
            client, next(iter(provider.auth_codes))
        )
        token = await provider.exchange_authorization_code(client, code)

        old = await provider.load_refresh_token(client, token.refresh_token)
        new = await provider.exchange_refresh_token(client, old, [SCOPE])
        assert new.refresh_token != token.refresh_token
        assert await provider.load_refresh_token(client, token.refresh_token) is None

    async def test_verify_token_round_trip(self, provider, client):
        txn = await start_login(provider, client)
        await provider._handle_login(FakeRequest({"txn": txn, "password": PASSWORD}))
        code = await provider.load_authorization_code(
            client, next(iter(provider.auth_codes))
        )
        token = await provider.exchange_authorization_code(client, code)

        verified = await provider.verify_token(token.access_token)
        assert verified is not None and verified.client_id == client.client_id
        assert await provider.verify_token("not-a-token") is None

    async def test_expired_access_token_is_refused(self, provider, client):
        txn = await start_login(provider, client)
        await provider._handle_login(FakeRequest({"txn": txn, "password": PASSWORD}))
        code = await provider.load_authorization_code(
            client, next(iter(provider.auth_codes))
        )
        token = await provider.exchange_authorization_code(client, code)
        provider.access_tokens[token.access_token].expires_at = 1
        assert await provider.verify_token(token.access_token) is None


# -------------------------------------------------------------- persistence


class TestPersistence:
    async def test_survives_a_restart(self, tmp_path, client):
        state = tmp_path / "oauth-state.json"
        first = PasswordOAuthProvider(
            password=PASSWORD, base_url="https://things.example.com", state_path=state
        )
        txn = await start_login(first, client)
        await first._handle_login(FakeRequest({"txn": txn, "password": PASSWORD}))
        code = await first.load_authorization_code(client, next(iter(first.auth_codes)))
        token = await first.exchange_authorization_code(client, code)

        # A fresh process reading the same state file.
        second = PasswordOAuthProvider(
            password=PASSWORD, base_url="https://things.example.com", state_path=state
        )
        assert await second.get_client("client-1") is not None
        assert await second.verify_token(token.access_token) is not None
        assert await second.load_refresh_token(client, token.refresh_token) is not None

    async def test_state_file_is_not_world_readable(self, tmp_path, client):
        state = tmp_path / "oauth-state.json"
        provider = PasswordOAuthProvider(
            password=PASSWORD, base_url="https://things.example.com", state_path=state
        )
        await provider.register_client(client)
        assert state.stat().st_mode & 0o077 == 0

    async def test_expired_tokens_are_not_reloaded(self, tmp_path, client):
        state = tmp_path / "oauth-state.json"
        provider = PasswordOAuthProvider(
            password=PASSWORD, base_url="https://things.example.com", state_path=state
        )
        txn = await start_login(provider, client)
        await provider._handle_login(FakeRequest({"txn": txn, "password": PASSWORD}))
        code = await provider.load_authorization_code(
            client, next(iter(provider.auth_codes))
        )
        token = await provider.exchange_authorization_code(client, code)

        stored = json.loads(state.read_text())
        for entry in stored["access_tokens"]:
            entry["expires_at"] = 1
        state.write_text(json.dumps(stored))

        reloaded = PasswordOAuthProvider(
            password=PASSWORD, base_url="https://things.example.com", state_path=state
        )
        assert reloaded.access_tokens == {}
        assert await reloaded.verify_token(token.access_token) is None

    async def test_corrupt_state_does_not_prevent_startup(self, tmp_path):
        state = tmp_path / "oauth-state.json"
        state.write_text("{ not json")
        provider = PasswordOAuthProvider(
            password=PASSWORD, base_url="https://things.example.com", state_path=state
        )
        assert provider.clients == {}

    def test_empty_password_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="password must not be empty"):
            PasswordOAuthProvider(
                password="", base_url="https://things.example.com", state_path=tmp_path / "s.json"
            )
