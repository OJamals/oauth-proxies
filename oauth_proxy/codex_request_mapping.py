"""OpenAI ChatCompletion request -> OpenAI Responses API request body.

Pure ``dict``-producing functions (no network). The Codex ChatGPT-subscription
backend speaks the Responses API, so a Chat-Completions request must be
restructured: system/developer messages become a top-level ``instructions``
string, the remaining turns become typed ``input`` items, and chat ``tools``
become Responses function tools. ``store`` is forced ``false`` (the subscription
backend rejects stored responses).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from oauth_proxy.models import ChatCompletionRequest

_OFF_EFFORTS = {None, "", "off", "none"}
_SYSTEM_ROLES = {"system", "developer"}


def _looks_like_openai(name: str) -> bool:
    n = (name or "").lower()
    return n.startswith(("gpt", "o1", "o3", "o4", "codex", "chatgpt")) or "codex" in n


def _resolve_model(requested: str, default_model: str) -> str:
    """Pass OpenAI/Codex model names through; substitute the default otherwise."""
    return requested if _looks_like_openai(requested) else default_model


def _stringify(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for p in content:
            if isinstance(p, str):
                out.append(p)
            elif isinstance(p, dict) and isinstance(p.get("text"), str):
                out.append(p["text"])
        return "".join(out)
    return json.dumps(content)


def _content_to_parts(content: Any, *, role: str) -> List[Dict[str, Any]]:
    """Convert chat content (str | parts list) to Responses content parts.

    Assistant text uses ``output_text``; user/tool text uses ``input_text``
    (the Responses API rejects the wrong text type for a given role).
    """
    text_type = "output_text" if role == "assistant" else "input_text"
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": text_type, "text": content}] if content else []
    parts: List[Dict[str, Any]] = []
    if isinstance(content, list):
        for p in content:
            if isinstance(p, str):
                if p:
                    parts.append({"type": text_type, "text": p})
                continue
            if not isinstance(p, dict):
                continue
            ptype = str(p.get("type") or "").lower()
            if ptype in {"text", "input_text", "output_text"}:
                if isinstance(p.get("text"), str) and p["text"]:
                    parts.append({"type": text_type, "text": p["text"]})
            elif ptype in {"image_url", "input_image"}:
                ref = p.get("image_url")
                url = ref.get("url") if isinstance(ref, dict) else ref
                if isinstance(url, str) and url:
                    parts.append({"type": "input_image", "image_url": url})
    return parts


def _messages_to_input(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role in _SYSTEM_ROLES:
            continue  # folded into top-level instructions
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": m.get("tool_call_id"),
                    "output": _stringify(m.get("content")),
                }
            )
            continue
        if role == "assistant":
            parts = _content_to_parts(m.get("content"), role="assistant")
            if parts:
                items.append({"type": "message", "role": "assistant", "content": parts})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                items.append(
                    {
                        "type": "function_call",
                        "name": fn.get("name"),
                        "arguments": fn.get("arguments") or "{}",
                        "call_id": tc.get("id"),
                    }
                )
            continue
        parts = _content_to_parts(m.get("content"), role="user")
        if parts:
            items.append({"type": "message", "role": role or "user", "content": parts})
    return items


def _instructions(messages: List[Dict[str, Any]]) -> str:
    texts: List[str] = []
    for m in messages:
        if m.get("role") in _SYSTEM_ROLES:
            t = _stringify(m.get("content"))
            if t:
                texts.append(t)
    return "\n\n".join(texts)


def _tools(tools: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for t in tools or []:
        if not isinstance(t, dict) or t.get("type") != "function":
            continue
        fn = t.get("function") or {}
        out.append(
            {
                "type": "function",
                "name": fn.get("name"),
                "description": fn.get("description"),
                "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return out


def _tool_choice(req: ChatCompletionRequest) -> Optional[Any]:
    tc = req.tool_choice
    if isinstance(tc, str):
        return tc  # "auto" | "none" | "required"
    if isinstance(tc, dict):
        fn = tc.get("function") or {}
        name = fn.get("name")
        if name:
            return {"type": "function", "name": name}
        return "auto"
    return None


def build_responses_body(
    req: ChatCompletionRequest,
    *,
    default_model: str,
    default_reasoning_effort: Optional[str] = None,
    stream: bool,
) -> Dict[str, Any]:
    """Return a Responses API request body for the Codex subscription backend."""
    messages = [m.model_dump(exclude_none=True) for m in req.messages]
    body: Dict[str, Any] = {
        "model": _resolve_model(req.model, default_model),
        "input": _messages_to_input(messages),
        "store": False,
        "stream": stream,
    }
    instructions = _instructions(messages)
    if instructions:
        body["instructions"] = instructions
    tools = _tools(req.tools)
    if tools:
        body["tools"] = tools
    tc = _tool_choice(req)
    if tc is not None:
        body["tool_choice"] = tc
    if (max_out := req.resolved_max_tokens()) is not None:
        body["max_output_tokens"] = max_out
    effort = (req.reasoning_effort or default_reasoning_effort or "").lower()
    if effort and effort not in _OFF_EFFORTS:
        body["reasoning"] = {"effort": effort}
    return body
