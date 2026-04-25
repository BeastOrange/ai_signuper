# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

Multi-provider AI account-registration framework, plus a vendored copy of **Sub2API** that consumes the registered accounts.

- **`src/ai_signuper/`** — uv-managed Python package. Currently implements one provider (Grok / xAI) and two sinks (txt file fallback + Sub2API admin API). Designed to grow more providers (OpenAI / Claude / etc.) by dropping a new `providers/<name>.py`.
- **`./sub2api/`** — full clone of [Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api), an AI API gateway. Consumes upstream account credentials via its admin API and resells them as platform API keys. **Carries its own `.git/`** — keep it as a nested clone; an accidental `git push` from inside it would leak captured credentials to the public upstream repo.
- **`turnstilePatch/`** — Chromium MV3 extension required by every provider that uses Cloudflare Turnstile (currently Grok). See its own README.

End-to-end flow per round (Grok provider):
1. Mail.tm provisions a disposable inbox.
2. DrissionPage drives `https://accounts.x.ai/sign-up?redirect=grok-com` in a visible Chromium.
3. Cloudflare Turnstile is solved using the bundled extension + iframe-internal `MouseEvent.prototype` patch.
4. The post-signup `sso` JWT is captured and pushed to the active sink — txt file by default, or the Sub2API admin API when `--sink sub2api`.

Phase C (making Sub2API actually forward requests to Grok using these `sso` cookies) is **not implemented**; the `extra.credential_kind` field on each Sub2API account is the hook for it.

## Run / Develop

uv-managed: `pyproject.toml` + `.python-version` (3.13) + `uv.lock` at the repo root, `.venv/` is created on first sync. **Do not use pip** — there is no `requirements.txt`.

```bash
uv sync                                                       # creates .venv, installs as editable
uv run python -m ai_signuper grok --count 1                   # one round, default txt sink
uv run python -m ai_signuper grok --count 0                   # infinite loop (Ctrl-C to stop)
uv run python -m ai_signuper grok --count 5 --sink sub2api    # batch into local Sub2API (needs .env)
```

Console script also installed: `uv run ai-signuper grok --count 1` works equivalently.

`requires-python` is pinned to `>=3.12,<3.14`: the upper bound encodes the Mail.tm TLS regression on 3.14+. Don't widen it without re-testing the mail step.

There is **no test suite, lint config, or CI** for the bot. Validation = run it and watch the visible browser.

For the Sub2API subtree, see the dedicated section below — its toolchain (Go + pnpm + Postgres + Redis) is completely separate.

### Runtime gotchas
- **Python 3.14 is known-broken** for Mail.tm TLS. `runtime.ensure_stable_python_runtime()` auto re-execs into 3.12/3.13 on Windows (looks under `%LOCALAPPDATA%\Programs\Python`). On other OSes it only prints a warning; the uv `requires-python` upper bound is the real guardrail.
- **Chrome must be forced to Chinese.** All button matchers in `providers/grok.py` (使用邮箱注册 / 注册 / 确认邮箱 / 完成注册) are hardcoded Chinese strings; x.ai renders the page per browser UI language. `runtime.build_chromium_options(lang="zh-CN")` sets `--lang=zh-CN` and `intl.accept_languages` — do not remove those, or the very first `_click_email_signup_button` step times out with `未找到"使用邮箱注册"按钮`.
- The Chromium browser stays **visible by design** (no headless flag). Turnstile requires real-feeling pointer movement.
- Browser is **fully restarted between rounds** (`session.restart()`) — do not refactor toward cookie/session reuse without explicit ask; it's a deliberate anti-detection choice.

## Architecture

### `src/ai_signuper/` layout

```
__main__.py            # CLI entry; selects provider + sink, runs the round loop
runtime.py             # ChromiumOptions builder, DrissionBrowserSession, Python guard, generic wait_for_cookie
mail_otp.py            # Mail.tm + verification-code extraction (provider-agnostic)
providers/
  base.py              # Provider Protocol + RegistrationResult TypedDict + BrowserSession Protocol
  grok.py              # Grok signup state machine (open → email → OTP → profile → sso)
sinks/
  base.py              # Sink Protocol (push, flush)
  txt_file.py          # Append credential per line to output/sso.txt
  sub2api.py           # POST batch to Sub2API admin /accounts/batch
```

### Provider state machine (`providers/grok.py`)

`GrokProvider.run_round(session)` is the orchestrator:

```
session.open_url(signup_url)
→ _click_email_signup_button(page)
→ _fill_email_and_submit(page)         # uses mail_otp.get_email_and_token
→ _fill_code_and_submit(session, ...)  # uses mail_otp.get_oai_code; survives PageDisconnectedError
→ _fill_profile_and_submit(session)    # solves Turnstile inline via _get_turnstile_token
→ runtime.wait_for_cookie(session, "sso")
```

Critical implementation details — these are the most fragile parts of the codebase:

- **Every form interaction is `page.run_js(...)` inline JS, not Python `.input()`.** x.ai uses React-controlled inputs; the script writes via the native `HTMLInputElement.prototype` setter and clears `_valueTracker` before dispatching `beforeinput` / `input` / `change`. Python-side `.input()` silently desyncs React state — the submit button stays disabled forever. **Do not "simplify" by switching to DrissionPage's high-level input methods.**
- **OTP entry has two code paths in one JS block**: a single aggregate input (`data-input-otp="true"`) and a fallback to per-digit `maxLength=1` boxes. Different x.ai A/B variants ship different DOMs; both must remain.
- **`PageDisconnectedError` is expected**, not a bug. Clicking 确认邮箱 navigates and invalidates the old tab handle. `session.refresh_page()` re-grabs the live tab; `_has_profile_form(page)` is the success signal.
- **Turnstile solver in `_get_turnstile_token()`** reaches into the challenge iframe's shadow root and clicks the `<input>` checkbox. It also re-defines `MouseEvent.prototype.screenX/screenY` *inside the iframe's JS context* — the bundled extension patches the top frame, but the iframe needs its own patch.

### Sinks

- **`txt_file.TxtFileSink`** — appends `result["credential"]` per line. Synchronous, no batching. Used as fallback even when sub2api sink is the primary.
- **`sinks.sub2api.Sub2ApiSink`** — calls `POST {SUB2API_BASE_URL}/api/v1/admin/accounts/batch` with `x-api-key` header. Builds each entry as `{platform=<provider>, type="apikey", credentials.api_key=<credential>, extra.credential_kind="<provider>_sso_cookie", confirm_mixed_channel_risk=true}`. **`type="apikey"` is a deliberate hack** because Sub2API's `type` field is bound to `oneof=oauth setup-token apikey upstream bedrock` — there is no cookie type. The hack is safe today because Sub2API doesn't yet forward requests to Grok; Phase C will use `extra.credential_kind` to identify these accounts and route them to a separate grok-proxy. **On any failure (network or partial-success batch), failed entries are dumped per-line to `output/sso-failed.txt`** so a bad gateway run doesn't waste a registration.

### `turnstilePatch/`

Chromium MV3 extension. Loaded via `runtime.build_chromium_options(...).add_extension(TURNSTILE_EXTENSION_PATH)` at startup. Two files (`manifest.json`, `script.js`) inject at `document_start` in the `MAIN` world and overwrite `MouseEvent.prototype.screenX/screenY` with realistic random integers, defeating the Chromium CDP fingerprint described in [crbug 40280325](https://issues.chromium.org/issues/40280325). Treat as a vendored dependency.

## Adding a new provider

1. Create `src/ai_signuper/providers/<name>.py`. Implement a class that satisfies `providers.base.Provider`:
   ```python
   class FooProvider:
       name = "foo"                       # used as Sub2API platform field
       signup_url = "https://..."
       chrome_lang = "en-US"              # whatever locale your button matchers expect
       success_cookie_name = "session"

       def run_round(self, session) -> RegistrationResult: ...
   ```
2. Register it in `__main__.py`'s `PROVIDERS` dict.
3. If the page is React/SPA, **copy the JS-injection pattern from `providers/grok.py`** verbatim. Do not call DrissionPage `.input()` on controlled forms.
4. Reuse `mail_otp` for any email-OTP flow. The regex ladder in `_extract_code` already handles xAI / OpenAI / Chinese / generic 6-digit formats.
5. Update root `README.md` with a one-line note on the provider and any quirks (locale, special MFA handling, etc.).

## `./sub2api/` — vendored gateway

Cloned verbatim from `https://github.com/Wei-Shaw/sub2api.git`. **Authoritative dev docs are inside the subtree**: read `sub2api/README.md` (deployment) and `sub2api/DEV_GUIDE.md` (local dev, CI, pitfalls) before touching it. Notes below are only the cross-cutting parts.

- **Stack:** Go 1.25.7 + Gin + Ent ORM (backend), Vue 3.4 + Vite 5 + Pinia + Vitest (frontend), PostgreSQL 15+, Redis 7+.
- **Frontend uses `pnpm`, not `npm`.** CI runs `pnpm install --frozen-lockfile`; committing only `package.json` without `pnpm-lock.yaml` breaks the build.
- **Local dev** (from `./sub2api/`):
  ```bash
  cd backend  && go test -tags=unit ./...
  cd backend  && go test -tags=integration ./...   # needs Postgres + Redis
  cd backend  && golangci-lint run ./...            # requires golangci-lint v2.7
  cd frontend && pnpm install && pnpm dev
  cd frontend && pnpm typecheck && pnpm test:run
  ```
- **Deployment is Docker Compose.** `sub2api/deploy/docker-compose.local.yml` is the production-grade variant (local-dir volumes); the script `sub2api/deploy/docker-deploy.sh` auto-generates `.env` with random `JWT_SECRET / TOTP_ENCRYPTION_KEY / POSTGRES_PASSWORD`.
- **Admin API surface relevant to the bot:**
  - Auth: `x-api-key: <admin-api-key>` header (mint at `/admin/settings → Admin API Key`). Implemented in `sub2api/backend/internal/server/middleware/admin_auth.go:47-55`.
  - Single create: `POST /api/v1/admin/accounts` (`account_handler.go:505`).
  - Batch create: `POST /api/v1/admin/accounts/batch` with `{"accounts": [CreateAccountRequest...]}` (`account_handler.go:1157`). Response: `{"success", "failed", "results"}`. Partial failures still return 200.
  - `CreateAccountRequest` fields: `name, platform, type (oneof oauth setup-token apikey upstream bedrock), credentials (JSONB), extra (JSONB), group_ids, expires_at, auto_pause_on_expired, confirm_mixed_channel_risk`.
- **Nginx note** (production): when Sub2API sits behind Nginx, add `underscores_in_headers on;` — Nginx drops `session_id` by default, breaking sticky-session routing across upstream accounts.
- **Never `git push` from inside `sub2api/`** — its `.git/` points at the upstream public repo; `output/` and any captured credential could leak.

## Sub2API integration (active sink)

Wiring lives in `src/ai_signuper/sinks/sub2api.py`. It's a finished implementation, not a TODO. Two operational notes:

- **Account `type` is hacked to `apikey`.** Sub2API's `type` field has no cookie option; the `sso` JWT is stored under `credentials.api_key`. The Phase C grok-proxy will need to filter by `extra.credential_kind="grok_sso_cookie"` to identify these and avoid Sub2API's built-in apikey forwarding logic — which today is dormant for `platform="grok"` because no upstream forwarder exists.
- **Failed entries always go to `output/sso-failed.txt`** (not just dropped). On total batch failure (network / 500), every entry's credential is appended; on partial-success, only the entries reported as `success: false` in the response. Runs that captured an account but failed to push it can be retried offline by feeding that file back later.

`.env` keys (root, gitignored): `SUB2API_BASE_URL`, `SUB2API_ADMIN_API_KEY`, optional `SUB2API_DEFAULT_GROUP_IDS` (comma-separated). `.env.example` is the template.
