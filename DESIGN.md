# oauth-proxies — architecture & design

A small, **local, single-user** HTTP server that speaks the OpenAI API and
forwards each request to one of three **subscription** backends — Claude
(Anthropic), Codex (ChatGPT/OpenAI), and Grok (xAI) — chosen from the requested
model name, authenticated by each vendor's OAuth login rather than API keys.

This document is the contributor/architecture reference. For usage, see
[README.md](README.md); for third-party provenance, see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Scope & ToS boundary

- **In scope:** localhost, single user, *your own* subscription logins — the
  narrowly-sanctioned "reuse your own login on your own machine" case.
- **Out of scope (deliberately):** multi-tenant / serverless deployment, fanning
  a subscription out to arbitrary apps or hosts, API-key auth, embeddings, the
  legacy `/v1/completions` route, persistent request-body logging.
- Each provider's OAuth path works only on plans that permit it and bills against
  that subscription's lane. The proxy doesn't change any provider's billing or
  terms — use it within each provider's ToS. Notably, **xAI gates OAuth/API
  access by SuperGrok tier server-side** (a valid login can still 403 at
  inference), and Anthropic's **Agent SDK credit** governs Claude billing.

## Architecture

```
oauth_proxy/
  app.py                     # FastAPI app, routes, per-provider dispatch + handlers
  routing.py                 # route_provider(model) -> "anthropic" | "codex" | "grok"
  config.py                  # env-var Config
  models.py                  # OpenAI request schemas; curated model FALLBACK; model_catalog()
  usage.py                   # in-process per-provider rate-limit/usage snapshot

  # --- Anthropic (Claude) — pre-existing path, unchanged in shape ---
  auth.py                    # TokenProvider: Claude OAuth resolve/refresh, build_client, list_models
  request_mapping.py         # OpenAI Chat -> Anthropic Messages kwargs (wraps the vendored adapter)
  response_mapping.py        # Anthropic Message  -> OpenAI ChatCompletion (non-stream)
  stream_mapping.py          # Anthropic events   -> OpenAI chat.completion.chunk (stream)
  _vendor/
    anthropic_adapter.py     # modified copy of the Hermes Agent adapter (MIT; THIRD_PARTY_NOTICES.md)
    _paths.py / utils.py / tools/*   # shims so the adapter resolves its sibling imports

  # --- Codex (ChatGPT subscription) ---
  codex_auth.py              # CodexTokenProvider: PKCE login, token store, refresh, headers; constants
  codex_client.py            # HTTP/SSE transport to chatgpt.com/backend-api/codex; list_models; fetch_usage
  codex_request_mapping.py   # OpenAI Chat  -> Responses request body
  codex_response_mapping.py  # Responses object -> OpenAI ChatCompletion (non-stream)
  codex_stream_mapping.py    # Responses SSE events -> OpenAI chat.completion.chunk

  # --- Grok (SuperGrok subscription) ---
  grok_auth.py               # GrokTokenProvider: PKCE login via xAI OIDC discovery, store, refresh
  grok_client.py             # passthrough to api.x.ai/v1 (post_json / stream_raw); list_models

  oauth_pkce.py              # shared, provider-agnostic PKCE + loopback-capture + JWT-decode helpers
tests/                       # pytest; converters are pure (dict->dict / iter->iter), no network
```

`_vendor/__init__.py` puts `_vendor/` on `sys.path` so the adapter resolves its
sibling-module imports (`_paths`, `utils`, `tools.*`) against the shims; access
it via `from oauth_proxy._vendor import adapter`.

### The provider seam

One OpenAI-shaped front door, three backends. `routing.route_provider(model)`
maps a model name to a provider purely by prefix:

- `claude*` / `anthropic/*` → **anthropic**
- `grok*` → **grok**
- `gpt*` / `o1*` / `o3*` / `o4*` / `chatgpt*` / contains `codex` → **codex**
- otherwise → `PROXY_DEFAULT_PROVIDER`

`app.py` dispatches on that result. Each provider supplies the same small shape —
a token provider (`get_token` / `headers` / `is_logged_in` / `list_models`) and a
client — while differing in auth quirks and wire format. The Anthropic path is
behavior-preserving (its tests are the guardrail); Codex/Grok are layered
alongside it.

## Request flows

**`POST /v1/chat/completions`** — validated by `models.ChatCompletionRequest`,
then routed:

- **anthropic:** `request_mapping.build_kwargs` → `adapter.build_anthropic_kwargs(...,
  is_oauth=True)` (adds the Claude Code system prefix + `mcp_` tool prefixing the
  subscription token requires) → `TokenProvider.build_client()` →
  `client.messages.create(**kwargs)` → `response_mapping`/`stream_mapping` back to
  OpenAI.
- **codex:** `codex_request_mapping.build_responses_body` (Chat→Responses) →
  `codex_client.stream_events` (always streams upstream) →
  `codex_stream_mapping` (stream) or `codex_client.collect_final` +
  `codex_response_mapping` (non-stream).
- **grok:** near-passthrough — forward the body to `api.x.ai/v1/chat/completions`
  with the bearer; stream the SSE bytes verbatim, or return the JSON.

**`POST /v1/responses`** — native OpenAI Responses API, Codex + Grok only (a
Claude-routed model is rejected 400). Codex: thin preflight (`store:false`) +
auth-inject passthrough to the Codex backend. Grok: passthrough to
`api.x.ai/v1/responses`.

**`POST /v1/images/generations`** — Grok only (`grok-imagine-*`); passthrough to
`api.x.ai/v1/images/generations`.

**`GET /v1/models`** — login-gated; for each logged-in provider, the real model
list is fetched **live** (Anthropic `client.models.list()`, Codex
`backend-api/codex/models?client_version=…`, xAI `/v1/models`) with the curated
`models.py` lists as fallback.

**`GET /usage`** — per-provider login + rate-limit/usage (see below).
**`GET /health`** — `{"status":"ok"}`. All errors use the OpenAI error envelope.

## Per-provider design

### Anthropic (Claude)
Auth resolves from the existing Claude Code credential store
(`adapter.read_claude_code_credentials()` → keychain / `~/.claude/.credentials.json`),
refreshed when expired, falling back to `adapter.resolve_anthropic_token()` (env /
`.env`). OAuth-only: a plain `sk-ant-…` API key is rejected. Translation is
OpenAI Chat ↔ Anthropic Messages via the vendored adapter (request) plus our own
`response_mapping`/`stream_mapping` (response). No dedicated `login` command yet
(deferred).

### Codex (ChatGPT subscription)
Own Authorization-Code + PKCE loopback login against `auth.openai.com` using the
**official public Codex CLI client id** (`app_EMoamEEZ73f0CkXaXp7hrann`), redirect
`http://localhost:1455/auth/callback` (fallback 1457); tokens stored at
`~/.oauth-proxy/.codex_oauth.json`, auto-refreshed. The `chatgpt_account_id` is
decoded from the `id_token` JWT and sent as the `ChatGPT-Account-ID` header
alongside `Authorization: Bearer …` and `originator: codex_cli_rs`.

Backend is the **Responses API** at `chatgpt.com/backend-api/codex/responses`.
Verified facts that shaped the code:
- The accepted model set is **version-gated**: the `/models` listing returns more
  models for a higher `client_version` (we send `1.0.0` → `gpt-5.5/5.4/5.4-mini/
  5.3-codex/5.2`; an old version returns only `gpt-5.2`). The inference endpoint
  accepts all of them regardless.
- A non-empty top-level `instructions` is **required** (default injected when the
  client sends no system message); `store:false` is required.
- The backend streams; the finalized output arrives in `response.output_item.done`
  while `response.completed` carries only usage with an empty `output` — so
  non-stream aggregation (`collect_final`) reads the per-item `done` events.

### Grok (SuperGrok subscription)
Own PKCE login using the **official public Grok-CLI client id**
(`b1a00492-…`); the authorize/token endpoints are resolved at runtime from xAI's
**OIDC discovery** (`auth.x.ai/.well-known/openid-configuration`). Two xAI
quirks: the authorize request must include `plan=generic` (else `accounts.x.ai`
rejects the loopback client), and the PKCE challenge is echoed at the token
exchange. Tokens stored at `~/.oauth-proxy/.grok_oauth.json`.

Inference goes to `api.x.ai/v1`, which is **natively OpenAI-compatible** for both
`/chat/completions` and `/responses` — so Grok needs **no translation**; the
provider just injects `Authorization: Bearer …` and forwards. Only header sent is
the bearer (no account-id). `grok-4.20-multi-agent-*` is `/responses`-only;
`grok-imagine-*` image models use `/v1/images/generations`; video models use an
API the proxy doesn't route.

## Live model catalog & `/usage`

The catalog reflects reality — only logged-in providers, fetched live (`/v1/models`
above). `/usage` is asymmetric by what each backend exposes:
- **Codex** — pulled live and free from `backend-api/codex/usage`: `plan_type`, a
  5h `primary_window` and weekly `secondary_window` (used-percent + reset), credits.
  PII (email/user_id) is dropped from the proxy's output.
- **Grok** — `x-ratelimit-*` headers captured **passively** from real requests
  (via `usage.record_ratelimit_headers` in `grok_client`); `null` until first use.
- **Claude** — `logged_in` only; rate-limit capture not yet implemented (deferred).

## Build vs. reuse — why this exists

We evaluated using NousResearch's `hermes proxy` directly. Adversarially-verified
research showed it can't meet the goals: its shipped proxy upstreams are only
`nous`/`xai` (no Codex/ChatGPT), it exposes no `/v1/responses`, and it's a heavy,
fast-moving monolithic dependency. Hermes is MIT, so the right move was to
**vendor only what helps** and build a thin proxy:
- The Anthropic adapter is vendored (it bundles the OAuth client identity +
  OpenAI→Messages translation cleanly).
- For Codex/Grok we wrote **original** converters/clients rather than vendoring
  Hermes' Responses adapter (it depends on `agent.*` and carries multi-provider
  cruft). Only **public, non-secret OAuth client identifiers and endpoint URLs**
  are reused for interoperability — the subscription backends only honor each
  vendor's official client.

## Auth, storage & refresh

- Anthropic: `auth.TokenProvider` (in-process cache, re-resolve near expiry).
- Codex/Grok: `CodexTokenProvider` / `GrokTokenProvider` — cache, refresh ahead of
  expiry, persist to `~/.oauth-proxy/*.json` (mode 0600). Shared PKCE + loopback
  callback + JWT-decode logic lives in `oauth_pkce.py`.
- `is_logged_in()` is a cheap, local, no-network check used to gate `/v1/models`.

## Config (env)

`PROXY_HOST` / `PROXY_PORT` (127.0.0.1:8787), `PROXY_API_KEY` (optional client
bearer gate), `PROXY_DEFAULT_PROVIDER` (anthropic), `DEFAULT_MODEL` /
`CODEX_DEFAULT_MODEL` / `GROK_DEFAULT_MODEL` (per-provider substitution for
unrecognized names), `DEFAULT_REASONING_EFFORT`, `PROXY_INCLUDE_REASONING`,
`PROXY_PROMPT_CACHE` (Claude prefix cache breakpoint), `PROXY_REQUEST_TIMEOUT`,
`LOG_LEVEL`. `.env` is loaded only at server start, never on import (so tests
don't pick up a developer's `.env`).

## Testing

All converters are pure `dict -> dict` / `iter -> iter`, tested directly with
recorded payloads — no token, no network. OAuth helpers are tested as pure
functions (PKCE, JWT decode, expiry, authorize-URL build) with I/O mocked.
Endpoint tests use FastAPI's `TestClient` with the upstream clients monkeypatched;
a `conftest` fixture isolates `OAUTH_PROXY_HOME` and the usage snapshot so tests
never touch real credentials or the network.

Endpoints run synchronously in FastAPI worker threads, keeping the blocking SDK
calls and streaming generators off the event loop — a fine trade-off for a
single-user, localhost proxy.

## Known constraints / deferred

- **Claude:** no dedicated `login` command (relies on Claude Code creds / a
  `.env` token); no rate-limit capture in `/usage`. Both are deferred.
- **Grok:** SuperGrok tier gating is server-side (possible `403` despite a valid
  login); multi-agent is `/responses`-only; video generation isn't routed.
