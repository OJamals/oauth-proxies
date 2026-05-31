# Third-Party Notices

This project includes the following third-party software.

---

## Hermes Agent — `anthropic_adapter.py`

`oauth_proxy/_vendor/anthropic_adapter.py` is a **modified copy** derived from
[`agent/anthropic_adapter.py`](https://github.com/NousResearch/hermes-agent/blob/main/agent/anthropic_adapter.py)
in the [Hermes Agent](https://github.com/NousResearch/hermes-agent) project by
Nous Research, used under the MIT License.

Modifications in this distribution: hermes-specific identifiers (function,
constant, module, env-var names) were renamed to neutral names; a small
runtime sanitization block targeting upstream product names was removed
(unused in this proxy's context); and provenance comments/docstrings were
neutralized. No public behavior of the OAuth-subscription request path was
changed. See `git log -- oauth_proxy/_vendor/anthropic_adapter.py` for the
exact diff.

Original license text reproduced below:

```
MIT License

Copyright (c) 2025 Nous Research

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

The local shim modules (`oauth_proxy/_vendor/_paths.py`, `utils.py`,
`tools/schema_sanitizer.py`, `tools/lazy_deps.py`) are original to this
project and licensed under the project's MIT License (see `LICENSE`).

---

## Codex and Grok providers — provenance of interoperability constants

The Codex (`codex_auth.py`, `codex_client.py`, `codex_*_mapping.py`) and Grok
(`grok_auth.py`, `grok_client.py`) provider code is **original to this project**
(MIT, see `LICENSE`); no third-party source is vendored for them.

To interoperate with the providers' subscription backends, this code reuses
**public, non-secret OAuth client identifiers and endpoint URLs** that those
backends require — these are interoperability facts, not copyrightable code:

- **Codex / ChatGPT:** the public Codex CLI OAuth client id and
  `auth.openai.com` / `chatgpt.com/backend-api/codex` endpoints, as published in
  [openai/codex](https://github.com/openai/codex).
- **Grok / xAI:** the public Grok-CLI OAuth client id and `auth.x.ai` /
  `api.x.ai` endpoints, as surfaced in
  [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) (MIT)
  and xAI's live OIDC discovery document.

The subscription backends only honor each vendor's official public client, so
reusing these identifiers is required for the "use your own subscription on your
own machine" interop case this proxy targets.
