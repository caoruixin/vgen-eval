# Admin / public split and a fairer public comparison (vgen-eval)

## Context

The hosted site (https://video.llm-pro.cn, `VGEN_PUBLIC=1`) is meant for customers to compare video models from real outputs. Today it has no admin identity: the site is either fully "public" or fully "operator", and the public side leaks operator diagnostics. Verified in code:

- `job_out` (`vgen/api.py:518-557`) is a denylist, so every visitor receives Tokenbay's raw error text (`fail_reason`, `error_code`), full raw gateway responses (`last_detail`, `submit_response`, incl. an account group id), `task_id`, `payload`, `quota`, and the operator's per-run spend.
- `GET /api/probe`, the backfill plan, and all `/api/benchmarks*` routes (Vast account figures, raw attempt records) are ungated.
- The UI prints `job.fail_reason` on every failed card (`web/src/components/JobCard.tsx:136`) and shows raw JSON in an always-visible Details block.

H3 clips already sit next to Tokenbay clips on `/results` (matched on exact prompt + input image hash), but the page says almost nothing about how the conditions differ (one hard-coded sentence about resolution).

Goal: you sign in and see everything; customers get a clean, server-enforced view that still shows failures honestly and states plainly what is and isn't comparable, with H3 included as a reference that Tokenbay does not sell.

## Decisions you made

| Question | Decision |
|---|---|
| Admin access | Sign in on the live site with a secret; server stores only a hash |
| Public scope | Browse and generate with own key (as today) |
| Failed clips | Slot stays visible with a plain reason category; raw Tokenbay wording admin-only (also for a visitor's own clips) |
| H3 detail | Public: clips, hardware/settings, latency/throughput, estimated cost per clip, report. Admin-only on the page: Vast account figures, raw attempt records, raw JSON |
| H3 report | Leave as is (it keeps the Vast figures it already contains) |
| GitHub repo | Keep public |

Consequence of the last two: the site changes control what customers see on video.llm-pro.cn. The report download and the public repo still contain the Vast figures and Tokenbay's raw probe error text.

## Design

Two separate notions:

- **operator** = this process holds the operator's Tokenbay key (local instance only). The six `operator_only` routes stay exactly as they are. The key still never goes on the server.
- **admin** = may see diagnostics. Local mode: everyone (no behaviour change). Hosted mode: only with a valid admin session cookie.

Admin adds read visibility only. It does not let a session edit showcase runs, spend, or see other visitors' private runs and uploads. Admin may see operator probe runs.

## Part 1 — Admin sign-in (server)

- `vgen/config.py`: `Settings.admin_secret_hash` from `VGEN_ADMIN_SECRET_HASH`. Unset in hosted mode → sign-in disabled, nobody is admin.
- `vgen/security.py` (extend):
  - `hash_admin_secret` / `verify_admin_secret`: stdlib `hashlib.scrypt`, format `scrypt:16384:8:1:<salt-hex>:<hash-hex>`, constant-time compare, malformed → False.
  - `AdminAuth`: in-memory sessions (`sha256(random token) → expiry`, 12 h), logout revokes, restart signs out. Chosen over an HMAC cookie because a key derived from the stored hash would let anyone who reads the env file mint sessions.
  - Throttle: 5 failures per client per 15 min, keyed on `request.client.host` (uvicorn already resolves it from nginx's header). No global lockout.
  - `NoStoreApi` middleware: `Cache-Control: no-store` on `/api/` JSON and CSV, since the same URL now varies by cookie.
- `vgen/cli.py`: `vgen admin-hash` reads the secret twice with `getpass` (min 16 chars) and prints the one env line. Does not load settings.
- `vgen/api.py`:
  - `is_admin(request)`, `admin_only(request)` (403) next to `operator_only`.
  - `POST /api/admin/login` (sync handler; 404 when local or no hash, 401 wrong secret, 429 + `Retry-After` when throttled; refuses non-https except localhost/testserver) and `POST /api/admin/logout`. Cookie `vgen_admin`: HttpOnly, Secure, SameSite=Lax (changed from Strict after review: with Strict, an admin arriving from a link on another site booted into the public view; the launch-token header still covers cross-site requests).
  - `window.__VGEN__` gains `admin`; `/api/config` gains `admin` and `admin_login`.
  - `can_see` and the run list admit probe runs for admin.
  - `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)` in hosted mode.

## Part 2 — Public projection (server)

New `vgen/public_view.py`: pure dict → dict functions and allowlist constants (no FastAPI import), used by `api.py` and `evaluations.py`. Allowlists, so new columns default to private.

**Job** (`job_out(st, j, admin)` builds the full dict, then `public_job` for non-admin):

- Keep: `id, run_id, prompt_key, model, label, origin, experiment_id, deployment_id, status, progress, judge_status, adjustments, effective, est_cost_usd, created_at, submitted_at, gw_submit_time, gw_start_time, gw_finish_time, done_at, media, compliance, fidelity, model_label, family, vendor, video_url, poster_url, strip_url, latency_s, latency_basis`.
- Add: `has_task` (the UI branches on `task_id` truthiness at `JobCard.tsx:182,371,377`) and `failure: {category, retrying} | null`.
- Sub-objects, projected: `spec` (the `GenerationSpec` fields), `signals` (metrics; `error` → `analysis_failed: bool`), `judge` (verdict/checklist/defects/scores; no `usage`, no raw `error`), `cost` (`estimate_usd, actual_usd, per_second, basis`; no `quota`).
- `provenance`: always a dict, filtered after the step-count fill at `api.py:534-538`. Keep `phase, variant, seed, preferred, source, e2e_seconds, server_inference_time_s, model_revision, turbo_activation_verified, requested_num_inference_steps, actual_denoise_updates, variant_evidence_scope, cost_usd, cost_basis, timing_scope`.
- Admin-only: `task_id, submit_response, last_detail, payload, fail_reason, error_code, quota, poll_count, submit_attempts, next_poll_at, remote_status, endpoint, case_fingerprint, video_sha`, and all other provenance keys.

**`failure_category(status, error_code, fail_reason)`**, computed at read time so existing production rows need no migration. First match wins:

| # | Category | Rule | Public sentence (en; zh alongside) |
|---|---|---|---|
| 1 | `unsupported` | status `blocked` | Not run: this model does not support the requested task or settings. |
| 2 | `timeout` | status `timeout` | No clip yet: generation did not finish within the waiting time. |
| 3 | `insufficient_balance` | code, or text `insufficient (balance\|credits\|quota\|funds)` / `余额不足` | Not generated: the API key used for this run did not have enough balance. |
| 4 | `content_policy` | code or text mentions content/safety/moderation/policy/"real person" | Not generated: a content safety check declined this prompt or input image. |
| 5 | `auth` | auth codes, or text starts "auth failed" | Not generated: the API key used for this run was not accepted. |
| 6 | `rate_limited` | "rate limit" | Not generated: the request rate limit was reached and retries ran out. |
| 7 | `internal` | text starts "internal error", "app stopped while submitting", "endpoint … is not enabled" | Not generated: an error occurred on this site, not in the model. |
| 8 | `download_failed` | "generated but download failed", "download retry" | Generated, but the clip could not be retrieved. |
| 9 | `stopped` | status `stopped` | Stopped before a clip was produced. |
| 10 | `network` | text starts "not submitted:" / "retrying:" | Not generated: the generation service could not be reached. |
| 11 | `timeout` | code `after_send` | (as 2) |
| 12 | `invalid_request` | code starts `invalid`, or `business` | Not generated: the requested settings were not accepted for this model. |
| 13 | `provider_error` | status `failed` / `submit_unknown`, "poll error", "task not found" | Not generated: the generation service returned an error for this request. |
| 14 | `unknown` | fallback | Not generated: the reason was not recorded. |

Rule 3 must precede 9 and 12: Tokenbay sends insufficient balance with code `invalid_request` and a message that discloses the account balance, and the backfill script left some as `stopped`.

**Other endpoints, for non-admin:**

| Endpoint | Change |
|---|---|
| `/api/runs`, `/api/runs/{id}` | `spent_usd` only on the viewer's own runs; drop `run.notes`, `run.endpoint` |
| `/api/runs/{id}/export` | JSON follows the projection; CSV swaps `fail_reason` → `failure_category`, drops `quota` (line 640 would otherwise raise `KeyError`) |
| `/api/plan` | drop `items[].payload` |
| `/api/config` | keep `mock, public, admin, admin_login, endpoints, spent_usd, usd_cny, judge_model, tasks` |
| `/api/models` | `probe` reduced to `tasks`, `delivery`, `unavailable: true`; no raw text, notes, cases; `unregistered` empty |
| `/api/probe` (GET) | 403 |
| `/api/evaluations/…/cases/…` | projected samples; `coverage[].reason` blank; groups gain `hardware` (`gpu, gpu_count, memory_gb_per_gpu`) |
| `/api/evaluations/{ds}/backfill-plan` | 403 |
| `/api/benchmarks`, `/{id}` | `config` without `model_path`, `limits.*`, `turbo.path`; `summary.costs` reduced to `generated_utc, currency, api_usage_price_estimate_usd, self_technical_valid_outputs, official_technical_valid_outputs`; `scenes[].official_task_id` dropped; attempts keep a 38-key `metrics` subset (identity, timing, settings, media, cost estimate) without `request_id`, task ids, errors, ledger ids, paths, hashes |
| `/api/benchmarks/{id}/export` | 403 |
| `/api/benchmarks/{id}/artifacts/{name}` | public: `report.(md\|docx\|html)`, `licenses.md`, `figures/*.(png\|svg)`; everything else 404 |
| `visitor_key` errors (`api.py:250,253`) | fixed wording without upstream text ("Tokenbay rejected this key; check that it is active") |

## Part 3 — Role plumbing and diagnostics in the UI

- `web/src/api.ts`: `IS_ADMIN = window.__VGEN__?.admin ?? !IS_PUBLIC`; `adminLogin`, `adminLogout`; `adminFields(job)` guard that returns the admin view only when the data is actually present (so an expired session degrades cleanly); `normalizeJob` defaults `provenance` to `{}` (there is no React error boundary, so an unguarded read blanks the page).
- `web/src/types.ts`: the eleven diagnostic job fields move to `AdminJobFields`, with `Job extends Partial<AdminJobFields>` so an unguarded read is a compile error; add `failure`, `has_task`, `Provenance`.
- `web/src/pages/Admin.tsx` (new): hidden `/admin` route, one secret field (reusing `KeyField`), reload on success. Header shows an "Admin" chip and sign-out when hosted and signed in.
- `components/common.tsx`: `AdminOnly` wrapper, tagging blocks "admin only" on the hosted site so you can see what visitors don't.

| File | Public | Admin-only |
|---|---|---|
| `JobCard.tsx` | category sentence on failed cards; cost/latency; spec checks; signals and judge results; Details with model ID, requested → used settings, adjustments, frame strip | raw `fail_reason` + `error_code`, raw signal/judge errors, `task_id`, raw `payload` / `provenance` / `last_detail` JSON |
| `RunSetup.tsx` (PlanDialog) | estimate, price basis, adjustments, warnings | "Request payload" JSON |
| `Results.tsx` | comparison, coverage with plain statuses | backfill plan button/table, probe-failure tooltips, experiment/job ids in the sample selector |
| `Benchmarks.tsx` | platform cards, latency/throughput, phase and telemetry tables, estimated cost per clip, hardware/settings, slim attempts table, report download | account-debit and invoice tiles, raw costs/config JSON, per-attempt raw JSON, request ids, CSV/JSON export |
| `Models.tsx` | capability/price matrix with a plain "currently unavailable" state | probe-case table, probe notes, raw unavailable text, unregistered models; `/api/probe` query gets `enabled: IS_ADMIN` |
| `Runs.tsx`, `RunBoard.tsx` | spend only when present (own runs) | probe tab |

Existing `!IS_PUBLIC` gates (Settings, spend pill, model refresh, paid probe, "From URL", CLI hint) are unchanged.

## Part 4 — Making the comparison fair and legible

All presentation of existing data; no new generations.

- **Source label on every group** (`SourceTag`): "Tokenbay", "Self-hosted (8× RTX PRO 6000) · not on Tokenbay", "MiniMax official API · not on Tokenbay". Driven by the group's `provider` + `hardware`, and "not on Tokenbay" by the model being absent from the Tokenbay registry list.
- **"How to read this comparison" panel** on `/results` (`components/HowToRead.tsx`, collapsible, en + zh), replacing the hard-coded `eval.spec_note`. Every line is computed from the clips on screen, so it stays true as models are added:
  - Identical: prompt (matched byte for byte), input image (matched by hash), any setting with one distinct value.
  - Different: output size, frame rate, clip length, seed fixed or not, inference steps, latency basis, cost basis, with counts.
  - Not measured: human ratings, AI judge, single-sample cells ("a single clip does not show run-to-run variation"), unverified Turbo.
  - Reference statement: "{model} is not sold on Tokenbay. Its clips were produced outside Tokenbay and are shown as a reference point, under the settings listed here."
  - "This page places clips side by side. It does not score or rank them."
- **Failures and gaps stay visible**: failed cards remain in the grid with their category; a "Not in this comparison" strip lists models that were not run, don't support the task, or were unavailable.
- **Blind mode**: state lifted to the page so it persists across scenarios (it resets on every scenario today, `Results.tsx:54,129`), `?blind=1` link, card order shuffled while blind.
- **Benchmarks framing card**: what the numbers are, H3 not sold on Tokenbay, cost per clip is an estimate (not an invoice, not a Tokenbay price), small samples, no human quality scores, Turbo unverified.
- New copy goes in `i18n.tsx` / `evaluationCopy.ts` in en and zh (both dictionaries are type-checked, so a missing key fails the build).

## Part 5 — Tests

- New `tests/test_public_view.py`: `failure_category` over the real observed strings; `public_job` drops unknown and private fields; hash round-trip; `AdminAuth` sessions/throttle with a fake clock; CLI prints the env line and never the secret.
- `tests/test_public_mode.py`:
  - Leak canary: a showcase run with a sentinel string in every private field (job, run notes, ledger, probe result, registry overlay); as an anonymous visitor the sentinel is absent from the raw body of runs list, run detail, export JSON + CSV, evaluations, models, config, leaderboard, and `/api/probe` + backfill-plan return 403.
  - Sign-in: disabled without a hash; wrong secret → 401, then 429; right secret sets the cookie with HttpOnly/Secure/SameSite=Strict and the same endpoints then include the raw fields; logout revokes.
  - Admin still cannot see another visitor's run; can see probe runs.
  - A visitor's own failed clip shows the category only.
  - Local mode unchanged (raw fields present, `admin: true`, login route 404).
- `tests/test_experiments.py`: public benchmark body lacks the instance id, credit fields, ledger ids and paths but keeps throughput and cost per clip; export 403; `attempts.csv` 404; admin gets the full body.
- One existing assertion changes: `tests/test_public_mode.py:186` reads `task_id` on a visitor's own run → `has_task`.

## Part 6 — Docs and deploy

- `deploy/vgen.env.example`: commented `#VGEN_ADMIN_SECRET_HASH=` with the generation command. README: an "Admin view" paragraph. No change to `provision.sh`, `deploy.sh`, nginx.
- Work on a branch (`admin-public-split`); commit, push and deploy only when you say so.
- Deploy (I will ask before running any of it):
  1. You run `! uv run vgen admin-hash` and choose the secret; it prints one line.
  2. That line is added to `/etc/vgen/vgen.env` on the server (`sudoedit` over SSH).
  3. `deploy/deploy.sh` (code + UI, restart; no DB migration, no data upload, `llms-study.conf` and `tokenbay-kb.conf` untouched).
  4. You sign in at `/admin` yourself. I will not type the secret or any Tokenbay key into the live page.

## Delivery: dedicated agents

This doc is saved in the repo at `docs/plans/2026-11-12-admin-public-split.md` and is the brief every agent works from. Work happens on branch `admin-public-split`. No agent commits, pushes, deploys, contacts the server, or makes a paid call.

| Step | Agent | Model | Owns | Why this model |
|---|---|---|---|---|
| 1a | Server build | Fable | `vgen/`, `tests/`, `deploy/vgen.env.example` (Parts 1, 2, 5) | Auth and the leak allowlists are where a mistake costs most |
| 1b | UI build (parallel with 1a) | Opus | `web/` (Parts 3, 4) | Large, mostly mechanical UI work plus en/zh copy against a fixed contract |
| 2 | Docs | Sonnet | `README.md` | Short, factual; written after the code so it describes what shipped |
| 3a | QA run | `qa` agent | nothing (reports only) | Runs `make test`, the web build, and the anonymous / admin / local checks with curl against the mock server on a data copy |
| 3b | Leak and auth review (parallel with 3a) | Opus | nothing (read-only) | A different model from the server builder, reviewing the diff adversarially for any path that still returns private fields |
| 4 | Fixes | the owning builder | its own files | Findings go back to whoever wrote the code, then 3a re-runs |

1a and 1b touch disjoint directories and share the API contract in Parts 2 and 3, so they run side by side in one working tree. I do the final browser walk-through of the three states myself and report what passed, what failed, and what was skipped.

## Known limits

- I cannot see the production database. Failure rows there with codes I haven't seen fall to `provider_error` or `unknown`; checked after deploy by reading the public JSON anonymously.
- A server restart signs the admin out (same as the launch token and visitor keys today).
- The gitignored backfill scripts (`data/backfill/h3-tokenbay-2026-11-08/execute.py`, `verify_public.py`) read fields that become admin-only; they would need a sign-in step before any reuse. Not changed here.
- Per your decisions, the report download and the public repo still expose the Vast figures and raw probe error text.

## Verification

No paid runs at any point.

1. `make test` (existing 68 + new tests) and `pnpm --dir web build` (typecheck + bundle).
2. Local three-state check on a copy of the real data with the mock gateway, so H3 clips and real failed jobs are present and nothing can be billed: clone `data/` into the scratchpad (`cp -Rc`), then `VGEN_MOCK=1 VGEN_PUBLIC=1 VGEN_DATA_DIR=<copy> VGEN_ADMIN_SECRET_HASH=<test hash> uv run vgen serve --port 8766`.
   - Anonymous: failed cards show a category sentence; Network tab shows no `fail_reason`, `last_detail`, `task_id`; no backfill button; Models makes no `/api/probe` request; Benchmarks shows the framing card and no account tiles, raw JSON or export; source labels and the "How to read" panel update with the model filter; blind mode persists across scenarios.
   - Signed in at `/admin`: wrong secret rejected; right secret reloads with the Admin chip; all diagnostics reappear tagged "admin only"; Settings, spend pill and paid probe stay hidden; sign-out returns to the public view.
   - Local operator (`make mock`): everything as today.
3. After deploy, anonymous read-only checks: `/api/config` shows `"admin": false, "admin_login": true`; `/api/probe` → 403; the F01 case JSON contains no "real person" text and the Seedance samples carry `failure.category = "content_policy"`.
