from app.config import get_settings
from app.services.tools import apply_tool_policy


def _tool(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_tool_policy_allows_everything_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ALLOWED_TOOL_NAMES", raising=False)
    get_settings.cache_clear()
    payload = {"tools": [_tool("weather"), _tool("calendar")]}
    try:
        result = apply_tool_policy(payload)
        assert len(result["tools"]) == 2
    finally:
        get_settings.cache_clear()


def test_tool_policy_filters_disallowed_tools(monkeypatch) -> None:
    monkeypatch.setenv("ALLOWED_TOOL_NAMES", "weather")
    get_settings.cache_clear()
    payload = {
        "tools": [_tool("weather"), _tool("shell")],
        "tool_choice": {"type": "function", "function": {"name": "shell"}},
    }

    try:
        result = apply_tool_policy(payload)
        assert [item["function"]["name"] for item in result["tools"]] == ["weather"]
        assert result["tool_choice"] == "auto"
    finally:
        get_settings.cache_clear()
