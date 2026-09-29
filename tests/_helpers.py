"""Shared test helpers."""

from typing import Any


def tool_text(result):
    """Return the human-readable text from a tool result.

    Read tools now return a FastMCP ToolResult (text + structured_content);
    a few tools and error paths still return plain strings. This normalizes
    both so text-based assertions work either way.
    """
    if isinstance(result, str):
        return result
    return result.content[0].text


def tool_data(result) -> dict[str, Any]:
    """Return the structured_content from a tool result.

    The companion to tool_text. ToolResult types it as optional, but every read
    tool fills it, so a missing one is a failure rather than a value to test.
    """
    assert result.structured_content is not None
    return result.structured_content
