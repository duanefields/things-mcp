import json
import urllib.parse
from unittest.mock import patch

import pytest

import things_mcp.server as server
from things_mcp.server import _build_project_items, _read_project_contents, add_project


def sc(result):
    return result.structured_content


def dispatched_payload(mock):
    url = mock.call_args[0][0]
    query = urllib.parse.urlparse(url).query
    return json.loads(urllib.parse.parse_qs(query)["data"][0])


def row(title, uuid, index=0, heading=None):
    return {"title": title, "uuid": uuid, "index": index, "heading": heading}


class TestBuildProjectItems:
    def test_headings_and_todos_keep_their_order(self):
        payload, plan, err = _build_project_items([
            {"type": "heading", "title": "A"},
            {"type": "todo", "title": "a1"},
            {"type": "heading", "title": "B"},
        ])
        assert err is None
        assert [p["type"] for p in payload] == ["heading", "to-do", "heading"]
        assert plan == [("heading", "A"), ("todo", "a1"), ("heading", "B")]

    @pytest.mark.parametrize("alias", ["todo", "to-do", "task", "TODO", " ToDo "])
    def test_todo_type_aliases(self, alias):
        _, plan, err = _build_project_items([{"type": alias, "title": "t"}])
        assert err is None and plan == [("todo", "t")]

    def test_todo_attributes_are_mapped(self):
        payload, _, err = _build_project_items([{
            "type": "todo", "title": "t", "notes": "n",
            "when": "today", "deadline": "2026-12-31", "tags": ["x"],
        }])
        assert err is None
        assert payload[0]["attributes"] == {
            "title": "t", "notes": "n", "when": "today",
            "deadline": "2026-12-31", "tags": ["x"],
        }

    def test_checklist_items_are_refused_with_a_way_forward(self):
        # Things rejects the entire project payload if a nested todo carries
        # these, and does it with a modal dialog, so it must never be dispatched.
        _, _, err = _build_project_items(
            [{"type": "todo", "title": "t", "checklist_items": ["a"]}]
        )
        assert "checklist_items" in err
        assert "add-todos" in err

    def test_headings_take_only_a_title(self):
        _, _, err = _build_project_items([{"type": "heading", "title": "h", "when": "today"}])
        assert "heading" in err and "when" in err

    @pytest.mark.parametrize("bad,expected", [
        ({"type": "note", "title": "x"}, "expected 'heading' or 'todo'"),
        ({"type": "todo"}, "missing a title"),
        ({"title": "no type"}, "expected 'heading' or 'todo'"),
        ("a string", "must be an object"),
    ])
    def test_rejections(self, bad, expected):
        _, _, err = _build_project_items([bad])
        assert expected in err

    def test_the_position_of_a_bad_item_is_reported(self):
        _, _, err = _build_project_items([
            {"type": "heading", "title": "ok"},
            {"type": "todo", "title": "ok"},
            {"type": "todo"},
        ])
        assert "Item 3" in err


class TestReadProjectContents:
    """A todo's index is relative to its heading, so the display order cannot be
    recovered by sorting everything on index together."""

    def test_todos_are_grouped_under_their_heading(self):
        headings = [row("H-B", "hb", index=0), row("H-A", "ha", index=-631)]
        todos = [
            row("b1", "b1", index=0, heading="hb"),
            row("a2", "a2", index=0, heading="ha"),
            row("a1", "a1", index=-479, heading="ha"),
        ]
        with patch.object(server.things, "todos", return_value=todos), patch.object(
            server.things, "tasks", return_value=headings
        ):
            got = [(k, r["title"]) for k, r in _read_project_contents("p")]
        assert got == [
            ("heading", "H-A"), ("todo", "a1"), ("todo", "a2"),
            ("heading", "H-B"), ("todo", "b1"),
        ]

    def test_todos_before_any_heading_come_first(self):
        with patch.object(server.things, "todos", return_value=[
            row("loose", "l1", index=-10, heading=None),
            row("under", "u1", index=0, heading="h1"),
        ]), patch.object(server.things, "tasks", return_value=[row("H", "h1", index=0)]):
            got = [(k, r["title"]) for k, r in _read_project_contents("p")]
        assert got == [("todo", "loose"), ("heading", "H"), ("todo", "under")]

    def test_empty_project(self):
        with patch.object(server.things, "todos", return_value=[]), patch.object(
            server.things, "tasks", return_value=[]
        ):
            assert _read_project_contents("p") == []


class TestAddProjectWithItems:
    async def test_items_are_nested_inside_attributes(self):
        # A sibling `items` key is ignored and the project is created empty.
        with patch.object(server.url_scheme, "execute_url") as dispatch, patch.object(
            server.things, "token", return_value="tok"
        ), patch.object(server, "_existing_ids", return_value=set()), patch.object(
            server, "_tasks_of", return_value=[row("P", "pid")]
        ), patch.object(server, "_await_project_contents", return_value=[]):
            await add_project(title="P", items=[{"type": "heading", "title": "H"}])

        entry = dispatched_payload(dispatch)[0]
        assert entry["type"] == "project"
        assert "items" in entry["attributes"]
        assert "items" not in entry

    async def test_project_attributes_travel_with_the_items(self):
        with patch.object(server.url_scheme, "execute_url") as dispatch, patch.object(
            server.things, "token", return_value="tok"
        ), patch.object(server, "_existing_ids", return_value=set()), patch.object(
            server, "_tasks_of", return_value=[row("P", "pid")]
        ), patch.object(server, "_await_project_contents", return_value=[]):
            await add_project(
                title="P", notes="n", when="today", tags=["t"], area_id="area-1",
                items=[{"type": "todo", "title": "x"}],
            )
        attrs = dispatched_payload(dispatch)[0]["attributes"]
        assert attrs["notes"] == "n" and attrs["when"] == "today"
        assert attrs["tags"] == ["t"] and attrs["area-id"] == "area-1"

    async def test_ids_come_back_aligned_to_what_was_asked_for(self):
        created = [
            ("heading", row("Design", "h1")),
            ("todo", row("Wireframes", "t1")),
            ("heading", row("Build", "h2")),
        ]
        with patch.object(server.url_scheme, "execute_url"), patch.object(
            server.things, "token", return_value="tok"
        ), patch.object(server, "_existing_ids", return_value=set()), patch.object(
            server, "_tasks_of", return_value=[row("P", "pid")]
        ), patch.object(server, "_await_project_contents", return_value=created):
            result = await add_project(title="P", items=[
                {"type": "heading", "title": "Design"},
                {"type": "todo", "title": "Wireframes"},
                {"type": "heading", "title": "Build"},
            ])
        assert sc(result)["id"] == "pid"
        assert sc(result)["items"] == [
            {"type": "heading", "title": "Design", "id": "h1"},
            {"type": "todo", "title": "Wireframes", "id": "t1"},
            {"type": "heading", "title": "Build", "id": "h2"},
        ]

    async def test_a_short_read_leaves_later_ids_null_not_wrong(self):
        # Better to admit an ID is unknown than to guess at it.
        with patch.object(server.url_scheme, "execute_url"), patch.object(
            server.things, "token", return_value="tok"
        ), patch.object(server, "_existing_ids", return_value=set()), patch.object(
            server, "_tasks_of", return_value=[row("P", "pid")]
        ), patch.object(server, "_await_project_contents",
                        return_value=[("heading", row("A", "h1"))]):
            result = await add_project(title="P", items=[
                {"type": "heading", "title": "A"}, {"type": "todo", "title": "b"},
            ])
        assert [i["id"] for i in sc(result)["items"]] == ["h1", None]
        assert "still created" in result.content[0].text

    async def test_todos_and_items_together_are_refused(self):
        with patch.object(server.url_scheme, "execute_url") as dispatch:
            result = await add_project(
                title="P", todos=["a"], items=[{"type": "todo", "title": "b"}]
            )
        dispatch.assert_not_called()
        assert "not both" in sc(result)["error"]

    async def test_empty_items_is_refused(self):
        with patch.object(server.url_scheme, "execute_url") as dispatch:
            result = await add_project(title="P", items=[])
        dispatch.assert_not_called()
        assert "empty" in sc(result)["error"]

    async def test_a_bad_item_never_reaches_things(self):
        # The cost of dispatching a malformed payload is a modal dialog on the
        # host's screen, so validation has to happen before dispatch.
        with patch.object(server.url_scheme, "execute_url") as dispatch:
            result = await add_project(
                title="P", items=[{"type": "todo", "title": "t", "checklist_items": ["a"]}]
            )
        dispatch.assert_not_called()
        assert "checklist_items" in sc(result)["error"]

    async def test_without_items_the_old_path_is_used(self):
        with patch.object(server.url_scheme, "execute_url") as dispatch, patch.object(
            server, "_existing_ids", return_value=set()
        ), patch.object(server, "_tasks_of", return_value=[row("P", "pid")]):
            result = await add_project(title="P", todos=["a", "b"])
        assert dispatch.call_args[0][0].startswith("things:///add-project")
        assert "items" not in sc(result)

    async def test_wait_ms_zero_skips_the_item_lookup(self):
        with patch.object(server.url_scheme, "execute_url"), patch.object(
            server.things, "token", return_value="tok"
        ), patch.object(server, "_tasks_of") as tasks:
            result = await add_project(
                title="P", items=[{"type": "heading", "title": "H"}], wait_ms=0
            )
        tasks.assert_not_called()
        assert sc(result)["id"] is None
        assert [i["id"] for i in sc(result)["items"]] == [None]
