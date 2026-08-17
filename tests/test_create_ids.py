import json
from unittest.mock import patch

import pytest

import things_mcp.server as server
from things_mcp.server import (
    DEFAULT_CREATE_WAIT_MS,
    _resolve_created,
    _validate_wait_ms,
    add_project,
    add_todo,
    add_todos,
)
from tests._helpers import tool_text


def task(title, uuid, index=0):
    return {"title": title, "uuid": uuid, "index": index, "type": "to-do"}


def sc(result):
    return result.structured_content


class TestValidateWaitMs:
    def test_none_is_allowed(self):
        assert _validate_wait_ms(None) is None

    def test_zero_is_allowed(self):
        assert _validate_wait_ms(0) is None

    def test_negative_is_rejected(self):
        assert "0 or greater" in _validate_wait_ms(-1)

    def test_absurdly_large_is_rejected(self):
        assert "or less" in _validate_wait_ms(10**9)

    @pytest.mark.parametrize("value", ["1500", 1.5, True])
    def test_non_integers_are_rejected(self, value):
        assert "integer" in _validate_wait_ms(value)


class TestResolveCreated:
    async def test_finds_the_new_id_ignoring_a_prior_duplicate(self):
        # An item with the same title already existed; only the new UUID counts.
        with patch.object(
            server, "_tasks_of", return_value=[task("Buy milk", "old"), task("Buy milk", "new")]
        ):
            ids = await _resolve_created("to-do", ["Buy milk"], {"old"}, 500)
        assert ids == ["new"]

    async def test_zero_budget_does_not_poll_at_all(self):
        with patch.object(server, "_tasks_of") as tasks:
            ids = await _resolve_created("to-do", ["Anything"], set(), 0)
        assert ids == [None]
        tasks.assert_not_called()

    async def test_times_out_to_none_rather_than_guessing(self):
        with patch.object(server, "_tasks_of", return_value=[]):
            ids = await _resolve_created("to-do", ["Never appears"], set(), 60)
        assert ids == [None]

    async def test_duplicate_titles_resolve_in_list_order(self):
        rows = [task("Same", "b", index=2), task("Same", "a", index=1)]
        with patch.object(server, "_tasks_of", return_value=rows):
            ids = await _resolve_created("to-do", ["Same", "Same"], set(), 500)
        assert ids == ["a", "b"]

    async def test_partial_batch_resolves_what_it_can(self):
        with patch.object(server, "_tasks_of", return_value=[task("A", "a")]):
            ids = await _resolve_created("to-do", ["A", "B"], set(), 60)
        assert ids == ["a", None]

    async def test_preserves_requested_order_not_database_order(self):
        rows = [task("B", "b", index=1), task("A", "a", index=2)]
        with patch.object(server, "_tasks_of", return_value=rows):
            ids = await _resolve_created("to-do", ["A", "B"], set(), 500)
        assert ids == ["a", "b"]


class TestAddTodo:
    async def test_returns_the_new_id_as_structured_content(self):
        with patch.object(server.url_scheme, "execute_url"), patch.object(
            server, "_existing_ids", return_value=set()
        ), patch.object(server, "_tasks_of", return_value=[task("Write tests", "uuid-1")]):
            result = await add_todo(title="Write tests")
        assert sc(result) == {"id": "uuid-1", "id_resolved": True, "title": "Write tests"}
        assert "uuid-1" in tool_text(result)

    async def test_wait_ms_zero_skips_the_lookup(self):
        with patch.object(server.url_scheme, "execute_url"), patch.object(
            server, "_tasks_of"
        ) as tasks:
            result = await add_todo(title="Fire and forget", wait_ms=0)
        tasks.assert_not_called()
        assert sc(result)["id"] is None
        assert sc(result)["id_resolved"] is False

    async def test_unresolved_id_claims_neither_success_nor_failure(self):
        """A null ID is genuinely ambiguous. Claiming the item "was almost
        certainly created" talked callers out of checking, so a genuinely lost
        write got reported to the user as a success."""
        with patch.object(server.url_scheme, "execute_url"), patch.object(
            server, "_existing_ids", return_value=set()
        ), patch.object(server, "_tasks_of", return_value=[]):
            result = await add_todo(title="Slow one", wait_ms=50)
        text = tool_text(result)
        assert sc(result)["id_resolved"] is False
        assert "UNCONFIRMED" in text
        assert "almost certainly created" not in text
        # It must say what to do, or the caller either invents a duplicate or
        # reports a success it cannot back up.
        assert "Search for the title" in text

    async def test_bad_wait_ms_does_not_dispatch(self):
        with patch.object(server.url_scheme, "execute_url") as dispatch:
            result = await add_todo(title="x", wait_ms=-1)
        dispatch.assert_not_called()
        assert "0 or greater" in sc(result)["error"]


class TestAddProject:
    async def test_returns_the_new_id(self):
        with patch.object(server.url_scheme, "execute_url"), patch.object(
            server, "_existing_ids", return_value=set()
        ), patch.object(server, "_tasks_of", return_value=[task("Launch", "proj-1")]):
            result = await add_project(title="Launch")
        assert sc(result)["id"] == "proj-1"


class TestAddTodos:
    def dispatch_payload(self, mock):
        """Pull the JSON payload back out of the things:///json URL."""
        import urllib.parse

        url = mock.call_args[0][0]
        query = urllib.parse.urlparse(url).query
        return json.loads(urllib.parse.parse_qs(query)["data"][0])

    async def test_sends_one_batch_preserving_order(self):
        rows = [task(f"T{i}", f"u{i}", index=i) for i in range(3)]
        with patch.object(server.url_scheme, "execute_url") as dispatch, patch.object(
            server.things, "token", return_value="tok"
        ), patch.object(server, "_existing_ids", return_value=set()), patch.object(
            server, "_tasks_of", return_value=rows
        ):
            result = await add_todos(todos=[{"title": f"T{i}"} for i in range(3)])

        assert dispatch.call_count == 1, "a batch must be a single dispatch"
        payload = self.dispatch_payload(dispatch)
        assert [p["attributes"]["title"] for p in payload] == ["T0", "T1", "T2"]
        assert [i["id"] for i in sc(result)["items"]] == ["u0", "u1", "u2"]
        assert sc(result)["resolved"] == 3

    async def test_maps_fields_to_url_scheme_names(self):
        with patch.object(server.url_scheme, "execute_url") as dispatch, patch.object(
            server.things, "token", return_value="tok"
        ), patch.object(server, "_existing_ids", return_value=set()), patch.object(
            server, "_tasks_of", return_value=[]
        ):
            await add_todos(
                todos=[{
                    "title": "T",
                    "checklist_items": ["a", "b"],
                    "heading_id": "h1",
                    "when": "today",
                }],
                wait_ms=0,
            )
        attrs = self.dispatch_payload(dispatch)[0]["attributes"]
        assert attrs["checklist-items"] == ["a", "b"]
        assert attrs["heading-id"] == "h1"
        assert attrs["when"] == "today"

    async def test_defaults_apply_and_per_todo_wins(self):
        with patch.object(server.url_scheme, "execute_url") as dispatch, patch.object(
            server.things, "token", return_value="tok"
        ), patch.object(server, "_existing_ids", return_value=set()), patch.object(
            server, "_tasks_of", return_value=[]
        ):
            await add_todos(
                todos=[{"title": "inherits"}, {"title": "overrides", "list_id": "own"}],
                list_id="default-list",
                wait_ms=0,
            )
        payload = self.dispatch_payload(dispatch)
        assert payload[0]["attributes"]["list-id"] == "default-list"
        assert payload[1]["attributes"]["list-id"] == "own"

    async def test_creates_rather_than_updates(self):
        with patch.object(server.url_scheme, "execute_url") as dispatch, patch.object(
            server.things, "token", return_value="tok"
        ), patch.object(server, "_existing_ids", return_value=set()), patch.object(
            server, "_tasks_of", return_value=[]
        ):
            await add_todos(todos=[{"title": "T"}], wait_ms=0)
        entry = self.dispatch_payload(dispatch)[0]
        assert entry["type"] == "to-do"
        assert entry.get("operation") in (None, "create")

    async def test_empty_list_is_rejected(self):
        with patch.object(server.url_scheme, "execute_url") as dispatch:
            result = await add_todos(todos=[])
        dispatch.assert_not_called()
        assert "at least one" in sc(result)["error"]

    async def test_missing_title_is_rejected_by_position(self):
        with patch.object(server.url_scheme, "execute_url") as dispatch:
            result = await add_todos(todos=[{"title": "ok"}, {"notes": "no title"}])
        dispatch.assert_not_called()
        assert "Todo 2" in sc(result)["error"]

    async def test_unknown_field_is_rejected(self):
        with patch.object(server.url_scheme, "execute_url") as dispatch:
            result = await add_todos(todos=[{"title": "t", "prioritY": "high"}])
        dispatch.assert_not_called()
        assert "prioritY" in sc(result)["error"]

    async def test_non_object_entry_is_rejected(self):
        with patch.object(server.url_scheme, "execute_url") as dispatch:
            result = await add_todos(todos=["just a string"])
        dispatch.assert_not_called()
        assert "Todo 1" in sc(result)["error"]

    async def test_unresolved_ids_claim_neither_success_nor_failure(self):
        with patch.object(server.url_scheme, "execute_url"), patch.object(
            server.things, "token", return_value="tok"
        ), patch.object(server, "_existing_ids", return_value=set()), patch.object(
            server, "_tasks_of", return_value=[]
        ):
            result = await add_todos(todos=[{"title": "a"}, {"title": "b"}], wait_ms=50)
        text = tool_text(result)
        assert sc(result)["resolved"] == 0
        assert sc(result)["count"] == 2
        # The header must not open with "Created" when nothing was confirmed.
        assert not text.startswith("Created")
        assert "could not be confirmed" in text
        assert "still created" not in text

    async def test_a_fully_resolved_batch_still_reads_as_created(self):
        """The hedge belongs only on the unconfirmed path."""
        with patch.object(server.url_scheme, "execute_url"), patch.object(
            server.things, "token", return_value="tok"
        ), patch.object(server, "_existing_ids", return_value=set()), patch.object(
            server, "_tasks_of",
            return_value=[{"title": "a", "uuid": "a1", "index": 0},
                          {"title": "b", "uuid": "b1", "index": 1}],
        ):
            result = await add_todos(todos=[{"title": "a"}, {"title": "b"}])
        text = tool_text(result)
        assert sc(result)["resolved"] == 2
        assert text.startswith("Created 2 todos")
        assert "UNCONFIRMED" not in text

    async def test_one_wait_covers_the_whole_batch(self):
        """Twenty items must not cost twenty sequential waits."""
        calls = []

        async def counting_sleep(seconds):
            calls.append(seconds)

        rows = [task(f"T{i}", f"u{i}", index=i) for i in range(20)]
        with patch.object(server.url_scheme, "execute_url") as dispatch, patch.object(
            server.things, "token", return_value="tok"
        ), patch.object(server, "_existing_ids", return_value=set()), patch.object(
            server, "_tasks_of", return_value=rows
        ), patch.object(server.anyio, "sleep", counting_sleep):
            result = await add_todos(todos=[{"title": f"T{i}"} for i in range(20)])

        assert dispatch.call_count == 1
        assert sc(result)["resolved"] == 20
        assert len(calls) == 0, "everything was already present; no waiting needed"


class TestCandidateQuery:
    """The scan must stay bounded by recent activity, not by backlog size.

    Dropping either filter is a real performance bug: without `status` the query
    pulls the whole logbook, and without `last` it scales with how many open
    tasks the user has.
    """

    def test_todo_query_is_filtered_by_status_and_recency(self):
        with patch.object(server.things, "tasks", return_value=[]) as tasks:
            server._tasks_of("to-do")
        assert tasks.call_args.kwargs == {
            "type": "to-do",
            "status": "incomplete",
            "last": server._CREATE_LOOKBACK,
        }

    def test_project_query_is_filtered_the_same_way(self):
        with patch.object(server.things, "tasks", return_value=[]) as tasks:
            server._tasks_of("project")
        assert tasks.call_args.kwargs["type"] == "project"
        assert tasks.call_args.kwargs["status"] == "incomplete"
        assert tasks.call_args.kwargs["last"] == server._CREATE_LOOKBACK

    def test_lookback_comfortably_exceeds_the_confirmation_budget(self):
        # The window only has to span dispatch-to-confirmation.
        assert server._CREATE_LOOKBACK.endswith(("d", "w", "y"))


class TestDefaults:
    def test_default_wait_matches_documented_value(self):
        assert DEFAULT_CREATE_WAIT_MS == 1500
