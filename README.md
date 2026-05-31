# oauth-proxies

One local, OpenAI-compatible endpoint in front of your **Claude, ChatGPT, and
Grok subscriptions** — authenticated by their OAuth logins, not API keys.

`oauth-proxies` is a small, **local, single-user** HTTP server that speaks the
OpenAI API and forwards each request to one of three subscription backends,
chosen automatically from the model name you ask for. Point any OpenAI client
(aider, Continue, LibreChat, the `openai` SDK, plain `curl`) at
`http://127.0.0.1:8787/v1` and switch providers by just changing `model`.

| Provider | Subscription | Models (examples) | Reached via |
|----------|--------------|-------------------|-------------|
| **Claude** (Anthropic) | Claude Pro / Max | `claude-opus-4-8`, `claude-sonnet-4-6`, … | Anthropic Messages API |
| **Codex** (OpenAI) | ChatGPT Plus / Pro | `gpt-5.5`, `gpt-5.4`, `gpt-5.3-codex`, … | OpenAI Responses API (`chatgpt.com/backend-api/codex`) |
| **Grok** (xAI) | SuperGrok / Premium+ | `grok-4.3`, `grok-4.20-*`, `grok-imagine-*`, … | xAI OpenAI-compatible API (`api.x.ai`) |

Translation happens in-process: OpenAI Chat ↔ Anthropic Messages for Claude,
OpenAI Chat ↔ Responses for Codex; Grok is a near-passthrough (xAI is natively
OpenAI-compatible). Each provider authenticates with its own vendor's official
public OAuth client — the subscription backends only honor those — so no API
keys are involved.

## Quickstart

```bash
# 1. Install (Python 3.10+)
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                       # add '.[dev]' for the test deps

# 2. Log in to the subscriptions you want (any subset)
oauth-proxy login codex                # ChatGPT Plus/Pro — opens browser, PKCE
oauth-proxy login grok                 # SuperGrok / Premium+ — opens browser, PKCE
#   Claude needs no login command — see "Authentication" below.

# 3. Run the server
oauth-proxy                            # serves http://127.0.0.1:8787

# 4. Make a request
curl http://127.0.0.1:8787/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"claude-opus-4-8","messages":[{"role":"user","content":"Say hi"}]}'
```

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8787/v1", api_key="unused")
resp = client.chat.completions.create(
    model="gpt-5.2",                   # routes to your ChatGPT subscription
    messages=[{"role": "user", "content": "Say hi"}],
    stream=True,
)
for chunk in resp:
    print(chunk.choices[0].delta.content or "", end="")
```

The catalog and routing adjust automatically to whichever providers you're
logged into; you don't have to log in to all three.

## Routing

The provider is chosen purely from the requested model name, so any OpenAI
client works by just setting `model` (see `oauth_proxy/routing.py`):

| Model name | Routed to |
|------------|-----------|
| `claude*`, `anthropic/*` | Claude |
| `grok*` | Grok |
| `gpt*`, `o1*`, `o3*`, `o4*`, `chatgpt*`, or any name containing `codex` | Codex |
| anything else | `PROXY_DEFAULT_PROVIDER` (default: Claude) |

If a name routes to a provider but isn't a model that provider recognizes
(e.g. `gpt-4o` routed to Codex), the proxy substitutes that provider's default
model (`DEFAULT_MODEL` / `CODEX_DEFAULT_MODEL` / `GROK_DEFAULT_MODEL`).

## Endpoints

- **`POST /v1/chat/completions`** — streaming and non-streaming; routed by model
  name across all three providers. Just change `model` to pick the backend:

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

- **`POST /v1/responses`** — native **OpenAI Responses API**, for the
  Responses-native providers only (**Codex and Grok**). This is the
  highest-fidelity path — the body is forwarded with minimal massaging. A model
  that routes to Claude is rejected with a 400 here.

- **`POST /v1/images/generations`** — **Grok only** (`grok-imagine-*` models),
  an OpenAI-images-compatible passthrough to xAI:

  ```bash
  curl http://127.0.0.1:8787/v1/images/generations -H 'Content-Type: application/json' \
    -d '{"model":"grok-imagine-image","prompt":"a red apple on a white table"}'
  # -> {"data":[{"url":"https://imgen.x.ai/..."}]}
  ```

- **`GET /v1/models`** — OpenAI-style catalog, **login-gated**: only providers
  you're actually logged into are listed. For each, the real allowlist is
  fetched **live** from the backend (Anthropic `models.list()`, Codex
  `backend-api/codex/models`, xAI `/v1/models`), with a small curated list as
  fallback if the live call fails.

- **`GET /usage`** — per-provider login status and rate-limit/usage. The shape
  differs by provider because of what each backend exposes:
  - **Codex** — pulled live (and free) from its usage endpoint: `plan_type`, a
    5-hour `primary_window` and weekly `secondary_window` (with used-percent and
    reset), and `credits`.
  - **Grok** — `x-ratelimit-*` headers captured **passively** from your real
    Grok requests, so `rate_limit` is `null` until you've made at least one.
  - **Claude (anthropic)** — `logged_in` status only. Rate-limit/usage capture
    is not yet implemented, so `rate_limit` is always `null`.

- **`GET /health`** — `{"status": "ok"}`.

All errors use the OpenAI error envelope
(`{"error": {"message", "type", "param", "code"}}`).

## Configuration

The server reads configuration from environment variables. On startup it also
loads a **`.env`** file from the working directory if one is present (real
environment variables take precedence). `.env` loading happens only when you run
the server, never on import — so the test suite won't pick up a developer's
`.env`. See `.env.example`.

```bash
# .env  (gitignored — never commit it)
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
```

| Var | Default | Meaning |
|-----|---------|---------|
| `PROXY_HOST` | `127.0.0.1` | Bind host |
| `PROXY_PORT` | `8787` | Bind port |
| `PROXY_API_KEY` | _(unset)_ | If set, clients must send `Authorization: Bearer <key>` |
| `PROXY_DEFAULT_PROVIDER` | `anthropic` | Backend for an unrecognized model name (`anthropic`/`codex`/`grok`) |
| `DEFAULT_MODEL` | `claude-opus-4-8` | Claude model substituted for an unknown name routed to Claude |
| `CODEX_DEFAULT_MODEL` | `gpt-5.2` | Codex model substituted for a non-OpenAI name routed to Codex |
| `GROK_DEFAULT_MODEL` | `grok-4.3` | Grok model substituted for a non-Grok name routed to Grok |
| `DEFAULT_REASONING_EFFORT` | `off` | `off`/`low`/`medium`/`high`/`xhigh`/`max` — extended-thinking effort |
| `PROXY_INCLUDE_REASONING` | `false` | Surface model thinking as a non-standard `reasoning_content` field |
| `PROXY_PROMPT_CACHE` | `true` | Inject one ephemeral prompt-cache breakpoint on each request's stable prefix (Claude) |
| `PROXY_REQUEST_TIMEOUT` | `900` | Upstream read timeout (seconds) |
| `LOG_LEVEL` | `INFO` | Server log verbosity (`DEBUG`/`INFO`/`WARNING`/…) |

## Authentication

Tokens for Codex and Grok are stored under `~/.oauth-proxy/` (mode 0600) and
refreshed automatically. Each provider runs its own Authorization-Code + PKCE
loopback login using that vendor's official **public** client id (the
subscription backends only honor it; these are interoperability identifiers,
not secrets — see `THIRD_PARTY_NOTICES.md`).

- **Claude** — read from your existing **Claude Code** login; there is no
  separate `login` command yet. The proxy resolves a token, in order, from the
  macOS Keychain (`Claude Code-credentials`), `~/.claude/.credentials.json`, or
  the `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_TOKEN` environment variables (which
  may come from `.env`). Refreshable Claude Code credentials are refreshed
  automatically. Mint a token with `claude setup-token` (requires the `claude`
  CLI logged in). The proxy is OAuth-only: a plain `sk-ant-...` API key is
  rejected.

- **Codex** — `oauth-proxy login codex` opens a browser, runs a PKCE login
  against `auth.openai.com`, and stores the bundle at
  `~/.oauth-proxy/.codex_oauth.json`. Inference goes to the ChatGPT-subscription
  Responses backend at `chatgpt.com/backend-api/codex`.

- **Grok** — `oauth-proxy login grok` runs a PKCE login against xAI (endpoints
  resolved at runtime from its OIDC discovery document) and stores the bundle at
  `~/.oauth-proxy/.grok_oauth.json`. Inference goes to `api.x.ai/v1`.

## Scope and caveats

This project targets one narrow, deliberate case: **reuse your own subscription
login, on your own machine, localhost, single user.** It is not a multi-tenant
gateway and is not built to fan a subscription out to arbitrary apps or hosts.
Use it within each provider's terms of service.

- **Claude Agent SDK credit.** Anthropic provides a monthly Agent SDK credit on
  Pro, Max, Team, and Enterprise plans (starting 2026-06-15) that covers Claude
  Agent SDK usage in your own projects; when it's exhausted, overflow goes to
  your plan's usage credits (if enabled), otherwise requests pause until the
  next cycle. See
  [Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan).
  This is one Claude-specific way to consume that credit from any
  OpenAI-compatible client — not the scope of the whole project.

- **Grok tier gating.** xAI gates OAuth/API access by SuperGrok tier
  *server-side*. A valid login can still return `403` at inference if your tier
  isn't entitled — that's an xAI entitlement wall, not a proxy bug.

- **Grok model quirks.** `grok-4.20-multi-agent-0309` works only via
  `/v1/responses` (xAI rejects it on `/chat/completions`). Grok video models
  (`grok-imagine-video*`) may appear in the live catalog but use a video API
  this proxy does not route.

## Develop

```bash
pip install -e '.[dev]'
pytest -q
```

Architecture and design decisions live in [DESIGN.md](DESIGN.md). The
OpenAI↔Anthropic and OpenAI↔Responses converters are pure `dict -> dict` /
`iter -> iter` functions, tested with recorded payloads — no token, no network.

Built on FastAPI + httpx, with the official `anthropic` SDK for the Claude path.
Endpoints run synchronously in worker threads, which keeps the blocking SDK
calls and streaming generators off the event loop — a fine trade-off for a
single-user, localhost proxy.

## Attribution

MIT-licensed (see [LICENSE](LICENSE)). The Anthropic-Messages adapter under
`oauth_proxy/_vendor/` is a modified copy from the
[Hermes Agent](https://github.com/NousResearch/hermes-agent) project (Nous
Research, MIT). The Codex and Grok provider code is original to this project;
only public, non-secret OAuth client identifiers and endpoint URLs are reused
for interoperability. Full details in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Not included (by design)

API-key authentication, multi-user or serverless deployment, embeddings, the
legacy `/v1/completions` route, and (for now) a Claude `login` command and
Claude rate-limit/usage reporting.
