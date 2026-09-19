from typing import Any

from app.config import get_settings


def apply_tool_policy(payload: dict[str, Any]) -> dict[str, Any]:
    """Filter OpenAI function tools by the configured allowlist.

    The gateway deliberately does not execute arbitrary tools. Open WebUI or
    another trusted tool runner remains responsible for execution.
    """
    settings = get_settings()
    allowed = settings.allowed_tool_name_set

    if not allowed or not isinstance(payload.get("tools"), list):
        return payload

    filtered: list[dict[str, Any]] = []
    for tool in payload["tools"]:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        name = function.get("name") if isinstance(function, dict) else None
        if name in allowed:
            filtered.append(tool)

    result = dict(payload)
    if filtered:
        result["tools"] = filtered
        choice = result.get("tool_choice")
        if isinstance(choice, dict):
            function = choice.get("function")
            name = function.get("name") if isinstance(function, dict) else None
            if name and name not in allowed:
                result["tool_choice"] = "auto"
    else:
        result.pop("tools", None)
        result.pop("tool_choice", None)
        result.pop("parallel_tool_calls", None)

    return result
