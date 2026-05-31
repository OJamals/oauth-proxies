"""Tests for the Codex Chat<->Responses converters (pure, no network)."""
from __future__ import annotations

import pytest

from oauth_proxy import codex_request_mapping as reqm
from oauth_proxy import codex_response_mapping as respm
from oauth_proxy import codex_stream_mapping as strm
from oauth_proxy.models import ChatCompletionRequest


def _req(**kw) -> ChatCompletionRequest:
    kw.setdefault("model", "gpt-5-codex")
    return ChatCompletionRequest.model_validate(kw)


# ── Request mapping ───────────────────────────────────────────────────────────

def test_system_folds_into_instructions_and_user_becomes_input_text():
    body = reqm.build_responses_body(
        _req(messages=[
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ]),
        default_model="gpt-5-codex",
        stream=False,
    )
    assert body["instructions"] == "be brief"
    assert body["store"] is False
    assert body["stream"] is False
    assert body["input"] == [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    ]


def test_assistant_text_uses_output_text():
    body = reqm.build_responses_body(
        _req(messages=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]),
        default_model="gpt-5-codex",
        stream=False,
    )
    assert body["input"][1] == {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "hello"}],
    }


def test_assistant_tool_calls_and_tool_result_become_function_items():
    body = reqm.build_responses_body(
        _req(messages=[
            {"role": "user", "content": "weather?"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "call_1", "type": "function",
                             "function": {"name": "get_weather", "arguments": "{\"city\":\"NYC\"}"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "72F"},
        ]),
        default_model="gpt-5-codex",
        stream=False,
    )
    fc = body["input"][1]
    assert fc == {"type": "function_call", "name": "get_weather",
                  "arguments": "{\"city\":\"NYC\"}", "call_id": "call_1"}
    out = body["input"][2]
    assert out == {"type": "function_call_output", "call_id": "call_1", "output": "72F"}


def test_tools_and_tool_choice_converted():
    body = reqm.build_responses_body(
        _req(
            messages=[{"role": "user", "content": "x"}],
            tools=[{"type": "function", "function": {
                "name": "f", "description": "d", "parameters": {"type": "object", "properties": {}}}}],
            tool_choice={"type": "function", "function": {"name": "f"}},
        ),
        default_model="gpt-5-codex",
        stream=True,
    )
    assert body["tools"] == [{"type": "function", "name": "f", "description": "d",
                              "parameters": {"type": "object", "properties": {}}}]
    assert body["tool_choice"] == {"type": "function", "name": "f"}
    assert body["stream"] is True


def test_non_openai_model_substituted_with_default():
    body = reqm.build_responses_body(
        _req(model="claude-opus-4-8", messages=[{"role": "user", "content": "x"}]),
        default_model="gpt-5-codex",
        stream=False,
    )
    assert body["model"] == "gpt-5-codex"


def test_openai_model_passed_through():
    body = reqm.build_responses_body(
        _req(model="o3-mini", messages=[{"role": "user", "content": "x"}]),
        default_model="gpt-5-codex",
        stream=False,
    )
    assert body["model"] == "o3-mini"


def test_default_instructions_when_no_system_message():
    # The Codex backend rejects requests without a non-empty instructions field.
    body = reqm.build_responses_body(
        _req(messages=[{"role": "user", "content": "hi"}]),
        default_model="gpt-5.2",
        stream=False,
    )
    assert body["instructions"] == reqm.DEFAULT_INSTRUCTIONS


# ── Response mapping (non-stream) ─────────────────────────────────────────────

def test_text_response_maps_to_content():
    resp = {
        "status": "completed",
        "output": [{"type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": "hello world"}]}],
        "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
    }
    out = respm.responses_to_openai(resp, model="gpt-5-codex", completion_id="c1", created=1)
    choice = out["choices"][0]
    assert choice["message"]["content"] == "hello world"
    assert choice["finish_reason"] == "stop"
    assert out["usage"] == {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}


def test_function_call_response_maps_to_tool_calls():
    resp = {
        "status": "completed",
        "output": [{"type": "function_call", "call_id": "call_9",
                    "name": "lookup", "arguments": "{\"q\":1}"}],
    }
    out = respm.responses_to_openai(resp, model="m", completion_id="c", created=1)
    choice = out["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["tool_calls"] == [
        {"id": "call_9", "type": "function", "function": {"name": "lookup", "arguments": "{\"q\":1}"}}
    ]
    assert choice["message"]["content"] is None


def test_incomplete_status_maps_to_length():
    resp = {"status": "incomplete", "output": [
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "x"}]}]}
    out = respm.responses_to_openai(resp, model="m", completion_id="c", created=1)
    assert out["choices"][0]["finish_reason"] == "length"


def test_reasoning_surfaced_only_when_enabled():
    resp = {"status": "completed", "output": [
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "thinking"}]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "answer"}]}]}
    without = respm.responses_to_openai(resp, model="m", completion_id="c", created=1)
    assert "reasoning_content" not in without["choices"][0]["message"]
    with_r = respm.responses_to_openai(resp, model="m", completion_id="c", created=1, include_reasoning=True)
    assert with_r["choices"][0]["message"]["reasoning_content"] == "thinking"


# ── Stream mapping ────────────────────────────────────────────────────────────

def _collect(events, **kw):
    kw.setdefault("model", "m")
    kw.setdefault("completion_id", "c")
    kw.setdefault("created", 1)
    return list(strm.responses_events_to_openai_chunks(iter(events), **kw))


def test_stream_text_emits_role_then_content_then_finish():
    chunks = _collect([
        {"type": "response.output_text.delta", "delta": "Hel"},
        {"type": "response.output_text.delta", "delta": "lo"},
        {"type": "response.completed", "response": {"status": "completed",
            "usage": {"input_tokens": 1, "output_tokens": 1}}},
    ])
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert chunks[1]["choices"][0]["delta"] == {"content": "Hel"}
    assert chunks[2]["choices"][0]["delta"] == {"content": "lo"}
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_stream_includes_usage_chunk_when_requested():
    chunks = _collect([
        {"type": "response.output_text.delta", "delta": "x"},
        {"type": "response.completed", "response": {"status": "completed",
            "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}}},
    ], include_usage=True)
    usage_chunks = [c for c in chunks if c.get("usage")]
    assert usage_chunks and usage_chunks[0]["usage"] == {
        "prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}
    assert usage_chunks[0]["choices"] == []


def test_stream_function_call_emits_tool_call_deltas():
    chunks = _collect([
        {"type": "response.output_item.added", "output_index": 0,
         "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "f"}},
        {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": "{\"a\":"},
        {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": "1}"},
        {"type": "response.completed", "response": {"status": "completed"}},
    ])
    # role chunk, tool start, two arg deltas, final
    start = chunks[1]["choices"][0]["delta"]["tool_calls"][0]
    assert start["index"] == 0 and start["id"] == "call_1" and start["function"]["name"] == "f"
    args = "".join(
        c["choices"][0]["delta"]["tool_calls"][0]["function"].get("arguments", "")
        for c in chunks if c["choices"] and c["choices"][0]["delta"].get("tool_calls")
    )
    assert args == "{\"a\":1}"
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_stream_failure_raises():
    with pytest.raises(strm.CodexStreamError, match="boom"):
        _collect([
            {"type": "response.output_text.delta", "delta": "x"},
            {"type": "response.failed", "response": {"error": {"message": "boom"}}},
        ])
