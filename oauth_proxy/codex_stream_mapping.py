"""OpenAI Responses SSE events -> OpenAI chat.completion.chunk stream.

Pure ``iter -> iter`` state machine. Consumes already-parsed Responses event
dicts (each carrying a ``type``) and yields OpenAI streaming chunk dicts. The
app layer is responsible for SSE framing (``data: ...\\n\\n`` and ``[DONE]``).
"""
from __future__ import annotations

from typing import Any, Dict, Iterator, Optional

from oauth_proxy.codex_response_mapping import map_usage


class CodexStreamError(RuntimeError):
    """Raised when the Responses stream reports a terminal error event."""


def _chunk(
    completion_id: str,
    created: int,
    model: str,
    delta: Dict[str, Any],
    *,
    finish: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def responses_events_to_openai_chunks(
    events: Iterator[Dict[str, Any]],
    *,
    model: str,
    completion_id: str,
    created: int,
    include_usage: bool = False,
    include_reasoning: bool = False,
) -> Iterator[Dict[str, Any]]:
    started = False
    finish = "stop"
    usage: Optional[Dict[str, int]] = None
    tool_index: Dict[str, int] = {}   # Responses item id -> OpenAI tool_call index
    next_tool_index = 0

    def _ensure_started() -> Iterator[Dict[str, Any]]:
        nonlocal started
        if not started:
            started = True
            yield _chunk(completion_id, created, model, {"role": "assistant"})

    for ev in events:
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type")

        if etype == "response.output_text.delta":
            yield from _ensure_started()
            delta = ev.get("delta")
            if delta:
                yield _chunk(completion_id, created, model, {"content": delta})

        elif etype == "response.reasoning_summary_text.delta" and include_reasoning:
            yield from _ensure_started()
            delta = ev.get("delta")
            if delta:
                yield _chunk(completion_id, created, model, {"reasoning_content": delta})

        elif etype == "response.output_item.added":
            item = ev.get("item") or {}
            if item.get("type") == "function_call":
                yield from _ensure_started()
                idx = next_tool_index
                next_tool_index += 1
                key = item.get("id") or ev.get("output_index")
                if key is not None:
                    tool_index[key] = idx
                finish = "tool_calls"
                yield _chunk(
                    completion_id,
                    created,
                    model,
                    {
                        "tool_calls": [
                            {
                                "index": idx,
                                "id": item.get("call_id") or item.get("id"),
                                "type": "function",
                                "function": {"name": item.get("name"), "arguments": ""},
                            }
                        ]
                    },
                )

        elif etype == "response.function_call_arguments.delta":
            delta = ev.get("delta")
            if delta:
                yield from _ensure_started()
                key = ev.get("item_id") or ev.get("output_index")
                idx = tool_index.get(key, 0)
                yield _chunk(
                    completion_id,
                    created,
                    model,
                    {"tool_calls": [{"index": idx, "function": {"arguments": delta}}]},
                )

        elif etype == "response.completed":
            resp = ev.get("response") or {}
            usage = map_usage(resp.get("usage"))
            if resp.get("status") == "incomplete" and finish != "tool_calls":
                finish = "length"

        elif etype in {"response.failed", "error"}:
            resp = ev.get("response") or {}
            err = resp.get("error") or ev.get("error") or {}
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise CodexStreamError(msg or "responses stream failed")

    yield from _ensure_started()
    yield _chunk(completion_id, created, model, {}, finish=finish)

    if include_usage and usage is not None:
        yield {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [],
            "usage": usage,
        }
