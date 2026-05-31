"""OpenAI Responses object -> OpenAI ChatCompletion (non-streaming).

Pure ``dict -> dict``. Mirrors ``response_mapping`` on the Anthropic side but
reads the Responses ``output[]`` item array instead of Anthropic content blocks.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def map_usage(usage: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """Responses usage -> OpenAI usage (prompt/completion/total tokens)."""
    u = usage or {}
    prompt = int(u.get("input_tokens") or 0)
    completion = int(u.get("output_tokens") or 0)
    total = int(u.get("total_tokens") or (prompt + completion))
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


def _finish_from_status(status: Optional[str], *, has_tool_calls: bool) -> str:
    if has_tool_calls:
        return "tool_calls"
    if status == "incomplete":
        return "length"
    return "stop"


def responses_to_openai(
    response: Dict[str, Any],
    *,
    model: str,
    completion_id: str,
    created: int,
    include_reasoning: bool = False,
) -> Dict[str, Any]:
    """Convert a Responses API object to an OpenAI ChatCompletion dict."""
    output = response.get("output") or []
    text_parts: List[str] = []
    reasoning_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []

    for item in output:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "message":
            for c in item.get("content") or []:
                if isinstance(c, dict) and c.get("type") in {"output_text", "text"}:
                    if isinstance(c.get("text"), str):
                        text_parts.append(c["text"])
        elif itype == "function_call":
            tool_calls.append(
                {
                    "id": item.get("call_id") or item.get("id"),
                    "type": "function",
                    "function": {
                        "name": item.get("name"),
                        "arguments": item.get("arguments") or "",
                    },
                }
            )
        elif itype == "reasoning" and include_reasoning:
            for s in item.get("summary") or []:
                if isinstance(s, dict) and isinstance(s.get("text"), str):
                    reasoning_parts.append(s["text"])

    message: Dict[str, Any] = {"role": "assistant"}
    message["content"] = "".join(text_parts) if text_parts else None
    if tool_calls:
        message["tool_calls"] = tool_calls
    if include_reasoning and reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)

    finish = _finish_from_status(response.get("status"), has_tool_calls=bool(tool_calls))
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": map_usage(response.get("usage")),
    }
