# oauth-proxies

A small, **local, single-user** server that exposes an **OpenAI-compatible API**
and forwards requests to your AI **subscriptions** via their OAuth login — no API
keys. Point any OpenAI client (aider, Continue, LibreChat, the `openai` SDK, plain
`curl`) at it and talk to:

| Provider | Subscription | Models (examples) | Reached via |
|----------|--------------|-------------------|-------------|
| **Claude** | Claude Pro/Max (Claude Code OAuth) | `claude-opus-4-8`, `claude-sonnet-4-6`, … | Anthropic Messages API |
| **Codex** | ChatGPT Plus/Pro (OpenAI Codex OAuth) | `gpt-5.2`, … | OpenAI Responses API (`chatgpt.com/backend-api/codex`) |
| **Grok** | SuperGrok / Premium+ (xAI OAuth) | `grok-4.3`, … | xAI OpenAI-compatible API (`api.x.ai`) |

The proxy routes **by the requested model name** (`claude-*`→Claude,
`gpt-*`/`o*`/`codex-*`→Codex, `grok-*`→Grok), so one endpoint serves all three.
`GET /v1/models` lists only the subscriptions you're actually logged into,
fetched **live** from each provider. Each provider runs its own
Authorization-Code + PKCE login using that vendor's official public client
identity (the subscription backends only honor it). Translation is done in-process:
OpenAI↔Anthropic for Claude, OpenAI-Chat↔Responses for Codex; Grok is a
near-passthrough (xAI is natively OpenAI-compatible).

## Scope

A **local, single-user** proxy for indie developers who want to use their
**Claude subscription's Agent SDK credit** with OpenAI-compatible tools
(aider, Continue, LibreChat, the `openai` SDK, plain `curl`) — without
routing through Claude Code itself.

Anthropic provides a monthly **Agent SDK credit** on Pro, Max, Team, and
Enterprise plans starting June 15, 2026 that explicitly covers *"Claude
Agent SDK usage in your own projects"* and *"third-party apps built on the
Agent SDK"* — see [Use the Claude Agent SDK with your Claude plan][docs].
This proxy is one way to tap that credit from any OpenAI-compatible client,
locally. When the monthly credit is exhausted, additional usage flows to
your plan's usage credits at standard API rates (if you've enabled them);
otherwise requests pause until the next billing cycle.

[docs]: https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .            # or: pip install -e '.[dev]' to run tests
```

## Log in to your subscriptions

Log into whichever providers you want; the catalog and routing adjust
automatically. Tokens are stored under `~/.oauth-proxy/` (mode 0600) and
refreshed automatically.

```bash
oauth-proxy login codex     # ChatGPT Plus/Pro — opens browser, PKCE login
oauth-proxy login grok      # SuperGrok / Premium+ — opens browser, PKCE login
```

**Claude** is read from your existing Claude Code login (no separate command):
the macOS Keychain (`Claude Code-credentials`), `~/.claude/.credentials.json`, or
the `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_TOKEN` env vars. Create one with
`claude setup-token` (requires the `claude` CLI logged in).

> **Note on Grok:** xAI gates OAuth/API access by SuperGrok tier *server-side*.
> A valid login can still get `403` at inference if your tier isn't entitled —
> that's an xAI entitlement wall, not a proxy bug.

## Run

```bash
oauth-proxy               # serves on http://127.0.0.1:8787
# or: python -m oauth_proxy.app
```

On startup the server loads a **`.env`** file from the working directory if one
is present (real environment variables take precedence). Put your token there:

```bash
# .env  (gitignored — never commit it)
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
```

See `.env.example`. `.env` loading happens only when you run the server, not
when the package is imported (so tests never pick up a developer's `.env`).

### Configuration (environment variables)

| Var | Default | Meaning |
|-----|---------|---------|
| `PROXY_HOST` | `127.0.0.1` | Bind host |
| `PROXY_PORT` | `8787` | Bind port |
| `PROXY_API_KEY` | _(unset)_ | If set, clients must send `Authorization: Bearer <key>` |
| `PROXY_DEFAULT_PROVIDER` | `anthropic` | Backend for an unrecognized model name (`anthropic`/`codex`/`grok`) |
| `DEFAULT_MODEL` | `claude-opus-4-8` | Claude model substituted for an unknown name routed to Anthropic |
| `CODEX_DEFAULT_MODEL` | `gpt-5.2` | Codex model substituted for a non-OpenAI name routed to Codex |
| `GROK_DEFAULT_MODEL` | `grok-4.3` | Grok model substituted for a non-Grok name routed to Grok |
| `DEFAULT_REASONING_EFFORT` | `off` | `off`/`low`/`medium`/`high`/`xhigh`/`max` — extended-thinking effort |
| `PROXY_INCLUDE_REASONING` | `false` | Surface model thinking as a non-standard `reasoning_content` field |
| `PROXY_REQUEST_TIMEOUT` | `900` | Upstream read timeout (seconds) |

## Use it

Just change the `model` to pick the backend — same endpoint, any OpenAI client:

```bash
# Claude (Anthropic)
curl http://127.0.0.1:8787/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"claude-opus-4-8","messages":[{"role":"user","content":"Say hi"}]}'

# Codex (ChatGPT subscription)
curl http://127.0.0.1:8787/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"gpt-5.2","messages":[{"role":"user","content":"Say hi"}]}'

# Grok (SuperGrok)
curl http://127.0.0.1:8787/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"grok-4.3","messages":[{"role":"user","content":"Say hi"}]}'
```

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8787/v1", api_key="unused")
resp = client.chat.completions.create(model="gpt-5.2",
    messages=[{"role": "user", "content": "Say hi"}], stream=True)
for chunk in resp:
    print(chunk.choices[0].delta.content or "", end="")
```

Endpoints:
- `POST /v1/chat/completions` — stream + non-stream; routed by model name (all providers).
- `POST /v1/responses` — native **OpenAI Responses API** for the Responses-native
  providers (Codex, Grok); the highest-fidelity path.
- `POST /v1/images/generations` — **Grok only** (`grok-imagine-*` models), an
  OpenAI-images-compatible passthrough to xAI. Example:
  ```bash
  curl http://127.0.0.1:8787/v1/images/generations -H 'Content-Type: application/json' \
    -d '{"model":"grok-imagine-image","prompt":"a red apple on a white table"}'
  # -> {"data":[{"url":"https://imgen.x.ai/..."}]}
  ```
- `GET /v1/models` — live catalog of your logged-in subscriptions.
- `GET /health`.

## Develop

```bash
pip install -e '.[dev]'
pytest -q
```

Architecture and design decisions: see [DESIGN.md](DESIGN.md). The converters
(`response_mapping.py`, `stream_mapping.py`) are pure `dict -> dict` functions,
tested without any network or token.

## Not included (by design)

API-key auth, multi-user/serverless deployment, Bedrock/Azure/Kimi/MiniMax
endpoints, embeddings, the legacy `/v1/completions` route.
