# vgen·eval — video-generation model evaluation on Tokenbay

A local web app for running Tokenbay's video models side by side on the same prompt, watching the clips
together and deciding which model fits each use case. It measures cost, speed, spec compliance and prompt
adherence, and combines them with your own ratings.

Scope (v1): **tokenbay.com only.** The 15 video models are HappyHorse 1.0, Veo 3.1 (generate, fast, lite),
Seedance 2.0 (standard, fast, mini) and 2.5, Wan 2.7 (t2v, i2v, r2v, videoedit) and Wan 3.0.
The gateway layer is config-driven, so tokenbay.me can be added later (see the end of this file).

## Quick start

```bash
make setup        # uv sync + pnpm install + build the UI
make mock         # try everything with a fake gateway: no spending, data-mock/ → http://127.0.0.1:8766
make start        # real runs on tokenbay.com → http://127.0.0.1:8765
```

Credentials come from the workspace `.env`. The app reads only `TOKENBAY_BASE_URL`, `TOKENBAY_API_KEY`,
`TOKENBAY_REQUESTS_PER_MINUTE` and `TOKENBAY_HTTP_TRUST_ENV`, never writes `.env`, and never sends the key
to the browser.

## Using it

| Page | What it's for |
|---|---|
| **Scenarios** | 11 ready-made packs in four groups (see below). Each pack has prompts in English and 中文, a checklist per prompt, example assets and a default model set. Open a pack, review the plan and its cost, and run it. |
| **Compare** | A one-off comparison for any task: text-to-video, image-to-video, first+last frame, reference images, video edit, extend or audio-driven. You supply the prompt, optional checklist, assets and settings, and the app shows only the models that can do that task. |
| **Runs → run board** | All clips for a prompt in one grid. Playback is synchronized (play, scrub and loop together), with live status. Each card shows cost, latency, spec checks, signals, the AI judge's checklist and timestamped defects (click one to seek every player there). You can rate each clip (usable 👍/👎, stars, defect tags), pick the best, switch to **blind mode** (anonymous A/B/C labels, muted), re-run, or export CSV/JSON. |
| **Leaderboard** | Set what your use case needs (aspect ratio, duration, audio, max $ per clip, max latency). Models that can't meet it are greyed out with the reason. The rest are ranked by Quality, Value or Speed. Also shows a quality-vs-cost scatter with the best-value frontier, sample sizes and confidence intervals everywhere, and how well the judge agrees with your ratings. |
| **Models** | A capability matrix (task × model), durations, resolutions, aspect ratios, audio support, a normalized price (5 s at 720p), live availability and the live-probe results for each model. |
| **Settings** | Soft budget, confirmation threshold, USD→CNY rate, judge model and RPM. Also the spending ledger. |

### Scenario packs

- **Quick tour (≈ $1):** one prompt, four cheap settings across four model families.
- **Ads, cinematic and social:**
  - cinematic brand film (karst drone shot, perfume macro, coastal car)
  - vertical 9:16 social clips (latte art, street dancer, flaming wok)
  - stress tests (backflip physics, "OPEN 24H" neon text, ink-wash 汉服 best run in Chinese)
- **Product and photo animation:**
  - image-to-video (surfing cat, street portrait, a generated perfume product shot)
  - first → last frame (blue bird take-off)
- **Character / IP consistency:** a cartoon girl and snake from two reference images, placed in three different scenes.
- **Audio, talking and editing:**
  - native dialogue and sound effects (night-market vendor, thunderstorm, two-person café dialogue)
  - audio-driven talking and singing
  - instruction-based video edit (watercolour, snowy evening, character swap)
  - video extension

Example images, audio and video come from the Apache-2.0 Wan2.1 / Wan2.2 repos, pinned to a commit and
served through jsDelivr (GitHub raw as fallback). The perfume shot was generated with `gpt-image-2`
(`make gen-assets`).

## Metrics: what they mean

- **Success** and **latency.** Latency comes from the gateway's own timestamps (`finish_time − submit_time`),
  shown as a median with the sample size.
- **Cost.**
  - The worst-case estimate comes from the public prices in `vgen/catalog/prices.yaml`:
    - Seedance tokens are estimated as W×H×(24×s + 1)/1024, which matched the tokens the gateway reported for every clip.
    - Audio is counted at ×2 where the docs say it costs more.
  - Each task also reports a `quota`. Once the probe has worked out the quota-per-USD ratio, the actual cost is shown.
- **Spec check.** Compares the output with what was requested: resolution, aspect ratio, duration, fps,
  browser-playable codec, audio track, black bars and blank frames. It also catches parameters that silently
  had no effect.
- **Signals.**
  - **Warnings:**
    - frozen frames
    - scene cuts
    - **first/last-frame fidelity** for I2V and first+last frame. Letterbox bars are stripped, then the clip is ECC-aligned against the exact image that was sent and graded high, medium or low.
  - **Information only:** amount of motion and audio loudness. More motion is not better.
- **AI judge (optional).**
  - Gemini (`gemini-3.1-pro-preview` by default) looks at 8–16 timestamped frames from each clip, along with the input images, and answers the prompt's **checklist** item by item with evidence and timestamps. It also lists defects by severity.
  - It never sees model names.
  - It costs about $0.03 per clip and is shown in the plan before you run.
  - Treat it as assistive. The leaderboard switches to **your ratings** once you have rated at least 3 clips, and it shows judge-vs-human agreement (Kendall τ).

## Cost and safety

- Every run shows a worst-case plan first. Runs above `VGEN_CONFIRM_ABOVE_USD` (default $3) or beyond the soft budget
  (`VGEN_BUDGET_USD`, default $40) need confirmation. `VGEN_HARD_CAP_USD` blocks submits outright.
- How submits are handled:
  - **They are never retried blindly.** A rejected request, a rate-limit response or a connection failure means no task was created, so it's safe to resend.
  - A timeout after sending becomes `submit_unknown` with a "may be billed" warning.
- **There is no cancel API.** *Stop* only stops tracking, and the gateway keeps generating and billing.
- One rate limiter (default 15 requests per minute) is shared by submits, polls, downloads and judge calls. Polling slows
  down automatically as more jobs run, and a rate-limit 401 pauses until the next clock minute.
- Jobs survive restarts. A task id is saved immediately, polling resumes, and the mp4 is downloaded as soon as a job succeeds
  (results may expire after about 24 h).
- The server binds to 127.0.0.1 and protects against DNS rebinding (trusted hosts). State-changing calls need a token
  generated at each launch.

## Live probe

`make probe` (or `uv run vgen probe --dry-run` to only print the plan) sends one call at the cheapest settings
for each model and request shape, about 23 calls for roughly $7. It checks:

- which parameters actually take effect
- whether data-URI images are accepted
- the experimental modes (Wan edit, first+last frame, Seedance extend and audio-driven)
- the quota unit
- the judge

Results are written to `vgen/catalog/probe_status.json`. The Models page and the model picker use them:
experimental modes are switched on only after they pass.

### What the live probe found (2026-10-29, tokenbay.com)

| Finding | What the app does about it |
|---|---|
| **Veo 3.1 (generate / fast / lite) returns HTTP 500 "Internal server error" within 1 s** for every request: the documented minimal payload, both endpoints, all three models. | Marked *unavailable*. The picker disables it and shows the reason. Re-check from the Models page (↻) or with `vgen probe --cases veo-t2v`. |
| The task API answers in **camelCase** (`taskId`, `submitTime`, `finishTime`, `failReason`, `resultUrl`), not the documented snake_case. There is **no `quota`**. Seedance reports `usage.totalTokens`, which equals W×H×(24×s + 1)/1024: one frame more than the documented formula. | Both spellings are accepted. Seedance's cost is computed from the reported tokens ("actual"); per-second models use billed seconds × the public price. |
| HappyHorse 1.0, Wan 2.7 and Wan 3.0 always add an audible soundtrack. Seedance adds audio only with `generate_audio`. | The audio setting is shown as an adjustment, and the spec check reports it. |
| Wan 3.0 rejects 480p ("resolution must be one of [720p, 1080p]"). | 720p/1080p only. |
| Wan 2.7 t2v is slow: 11–14 min per clip in all 3 runs (3 s and 6 s clips). Wan 2.7 i2v took 37 s and r2v 2 min. | The poll budget is 20 min, and **Check again** resumes polling. |
| Images may be sent as **data URIs**: verified for HappyHorse i2v, Wan 2.7 i2v and Seedance mini i2v. | Local uploads work without a public URL. |
| Seedance needs `role: reference_video` / `reference_audio` for video and audio inputs. Its content safety may reject some reference clips. | Built into the adapter. A rejected task fails with the gateway's reason. |
| The Gemini route accepts inline **images** but rejects inline **video** (upstream 400). | The judge looks at 8–16 timestamped frames and can't hear audio. Sound-only checklist items are reported as "not assessable". |

## Configuration (`VGEN_*`, from `.env` or the shell)

| Variable | Default | Meaning |
|---|---|---|
| `VGEN_ENDPOINTS` | `com` | enabled gateways (`com`, later `com,me`) |
| `VGEN_BUDGET_USD` | `40` | soft budget (confirmation beyond it) |
| `VGEN_CONFIRM_ABOVE_USD` | `3` | runs above this need confirmation |
| `VGEN_HARD_CAP_USD` | — | refuse to submit beyond this total spend |
| `VGEN_JUDGE_MODEL` | `gemini-3.1-pro-preview` | judge model (`gemini-3.5-flash` is cheaper) |
| `VGEN_MAX_INFLIGHT` | `6` | concurrent tasks per gateway (also editable in Settings) |
| `VGEN_USD_CNY` | `7.2` | display exchange rate |
| `VGEN_DATA_DIR` | `data/` (`data-mock/` in mock mode) | database, videos, frames |
| `VGEN_PORT` | `8765` | web port |

## Hosted demo (public mode)

`VGEN_PUBLIC=1` turns the app into a demo you can share. The server holds **no** Tokenbay key.

- Your existing runs become read-only examples; probe runs are hidden from visitors. Visitors can watch, compare
  and export them, or re-run one with their own key.
- **To generate, a visitor enters their own Tokenbay API key** in the cost dialog.
  - It is checked with a free model-list call, then used for every call of that run (submit, poll, download, judge).
  - The server keeps it in memory only, until the run is finished. It is never written to the database, logs or
    responses.
  - The browser keeps it in sessionStorage, or in localStorage if the visitor ticks "remember".
- Each browser gets an anonymous cookie. Visitors see only the examples plus their own runs and uploads.
- Limits: `VGEN_PUBLIC_MAX_ACTIVE_RUNS` runs in progress per visitor (default 2), and uploads up to 20 MB.
- Closed in public mode: settings, the spending ledger, live probes, model refresh and fetching assets from a URL.
- After a restart the keys are gone. Jobs wait instead of failing, and the visitor's page hands the key back
  automatically (or asks for it).

| Variable | Meaning |
|---|---|
| `VGEN_PUBLIC` | `1` = hosted demo. Only `TOKENBAY_BASE_URL` is read from the env file; a key in it is ignored |
| `VGEN_ALLOWED_HOSTS` | extra host names to accept, e.g. `video.llm-pro.cn` |
| `VGEN_PUBLIC_MAX_ACTIVE_RUNS` | runs one visitor may have in progress (default 2) |
| `VGEN_ADMIN_SECRET_HASH` | optional. Turns on admin sign-in at `/admin`; paste the line printed by `uv run vgen admin-hash`. Unset = sign-in disabled. Ignored on a local instance |

To try it locally without spending, run `VGEN_PUBLIC=1 uv run vgen serve --mock --port 8766`. The mock rejects
keys that start with `sk-bad`.

### Admin view

The hosted site has two views, and the server decides which one a request gets. Visitors get the **public view**:
every response is built from an allowlist of fields, so anything not listed stays private. The site owner signs in
at the hidden page `/admin` and then also sees diagnostics, each block tagged "admin only". A local instance
(without `VGEN_PUBLIC=1`) has no sign-in and shows everything, as before.

Only the admin sees:

- the gateway's raw error text and error code on failed clips, raw request and response JSON, and task ids
- the spend on example runs, run notes and the operator's probe runs
- probe results (`GET /api/probe`) and the backfill plan, both of which answer 403 to visitors
- in the H3 benchmark: the account-level cost figures, raw attempt records, raw JSON and the CSV/JSON export

A failed clip is not hidden from visitors. It keeps its place in the comparison with a plain reason, for example
"Not generated: a content safety check declined this prompt or input image.", so a model that returned nothing
stays visible. This holds for a visitor's own clips too, and exports carry the category instead of the raw reason.

Admin is extra visibility and nothing else. Signing in does not allow editing example runs or spending, and does
not reveal other visitors' private runs or uploads. Features that need the operator's Tokenbay key (settings,
model refresh, paid probes, the spending ledger) stay local-only, and the operator key is still never placed on
the server.

To set it up, choose a secret (it is a password of your own, not a Tokenbay key) and hash it:

```bash
uv run vgen admin-hash   # asks for the secret twice (16 to 1024 characters, never echoed)
                         # prints one line: VGEN_ADMIN_SECRET_HASH=scrypt:...
```

Put that line in the server's `/etc/vgen/vgen.env` and restart the service. Only the hash is stored. If the variable
is unset, or is not a valid hash (the server logs a warning), sign-in is disabled and nobody is admin on the hosted
site. `deploy/provision.sh` copies `deploy/vgen.env.example` to that path only when the file is missing, so an
existing server needs the line added by hand.

Behaviour to know:

- Sign-in needs https. Over plain http it is refused, except on localhost.
- A session lasts 12 hours and lives in the server's memory, so restarting the service signs the admin out. The
  server keeps at most 8 sessions; a ninth sign-in ends the one closest to expiry.
- A wrong secret answers 401. Five wrong secrets from one address within 15 minutes lock that address out for the
  rest of that window (at most 15 minutes; the answer is 429 with a `Retry-After` header). Other addresses are
  not affected.
- To rotate the secret, run `uv run vgen admin-hash` again, replace the line and restart the service.

### Deploying

`deploy/` sets up https://video.llm-pro.cn on the llm-pro host without touching the sites already there:

- a `vgenapp` user, code in `/opt/vgen`, data in `/var/lib/vgen/data` and settings in `/etc/vgen/vgen.env` (no key)
- a hardened systemd unit, capped at 1 GB and one CPU
- its own nginx server and Let's Encrypt certificate

```bash
deploy/deploy.sh         # build the UI, ship the code, provision (idempotent), uv sync, restart, health check
deploy/upload-data.sh    # one-off: publish the local data/ (runs and clips) as the example runs
```

## Development

```bash
make test         # 188 tests: adapters, costs, gateway errors, limiter, analysis on synthetic clips, public and admin views, mock end-to-end
make dev          # backend + Vite dev server (http://127.0.0.1:5173, proxies /api)
uv run vgen models   # which registered models are live on the gateway
```

Layout:
- `vgen/` is the Python backend:
  - `gateway.py`: client, limiter and error classes
  - `jobs.py`: the engine
  - `adapters/`: one request builder per family
  - `analysis/`: video decoding and analysis
  - `judge.py`, `ranking.py`, `api.py`
- `vgen/catalog/` holds the model registry, prices and probe results.
- `scenarios/` holds the packs and assets.
- `web/` is the React + Vite + Tailwind UI, with EN/中文 dictionaries in `src/i18n.tsx`.

## Adding tokenbay.me later

1. Set `VGEN_ENDPOINTS=com,me` (the app then reads `TOKENBAY_ME_BASE_URL` / `TOKENBAY_ME_API_KEY`).
2. Add `me` to `endpoints:` for the shared models in `vgen/catalog/models.yaml`. Add the .me-only models
   (PixVerse V6, HappyHorse 1.1) with their prices in `prices.yaml`; .me prices are in CNY, so convert them or extend the price
   schema. PixVerse needs its own adapter.
3. Add `me` probe cases to `vgen/probe.py` and run `make probe`.

## Shared video results and H3 deployment benchmarks

The site now opens on **Video results** (`/results`). **H3 Benchmark** (`/benchmarks`) is a separate view of
hardware experiments; the existing scenarios, Tokenbay generation tools, runs and leaderboard remain available.

The [PRO6000 PoC report](https://video.llm-pro.cn/api/benchmarks/h3-pro6000-2026-11-07/artifacts/report.html)
is readable online and downloadable as Word from H3 Benchmark. The [architecture revision](reports/h3-pro6000/README.md)
adds H3 inference stages, GPU mapping and measurement boundaries while preserving the original experiment archive.

- The H3 v1 dataset contains six text-to-video and four first-frame image-to-video scenarios. Its frozen Chinese
  prompts and PNG bytes are unchanged. The original Tokenbay demonstrations use different inputs and remain in
  their own dataset. A match requires the same task, exact prompt bytes and ordered, role-specific asset hashes.
- Results group the model independently of its deployment and show self-hosted step configurations as separate
  cards. PRO6000's 50-step card defaults to the baseline request designated by `scene-results.csv`; the 8-step
  experiment appears alongside it for T01, T04, T06, F01, F02 and F03. Each card labels requested steps, available
  observed denoising updates and the current phase; official API steps remain undisclosed. The `×8` in the
  deployment name means eight GPUs, not eight inference steps. Other seeds and phases remain selectable. Group play, seek and loop
  work across sources; the sound selector enables only one clip. Blind mode is visual anonymity, not an access
  control boundary. It does not modify or reveal the original offline A/B review files.
- Every group on the Video results page carries a source label: Tokenbay, self-hosted (with its GPU count and
  model) or the vendor's official API, plus "not on Tokenbay" for a model the Tokenbay registry does not list. The
  collapsible **How to read this comparison** panel lists what is identical (prompt bytes, input image hash, any
  setting with one value), what differs (output size, frame rate, clip length, seed, inference steps, how latency
  and cost were measured) and what was not measured (human ratings, AI judge, single clips, unverified Turbo).
  All of it is computed from the clips on screen, and the page places clips side by side without scoring or
  ranking them. Blind mode persists across scenarios and can be linked with `?blind=1`.
- On H3 Benchmark, visitors see the platform cards, latency and throughput, hardware and settings, the estimated
  cost per clip, a trimmed attempts table and the report download. The account-level cost figures, raw attempt
  records, raw JSON and the CSV/JSON export are admin-only on the hosted site (see [Admin view](#admin-view)).
- Imported clips are terminal archive records. They cannot be resubmitted, polled, deleted as runs or sent to the
  AI judge. Local human ratings are supported; hosted showcase results remain read-only. Imported costs never
  enter the Tokenbay spending ledger, and imported experiments are excluded from the existing leaderboard.
- Benchmark values retain the archive's definitions and sample sizes. E2E download destinations differ between
  self-hosting and the API. Phase throughput and resource cost use whole observation windows. Empty human scores
  and unmeasured costs stay missing, and the unverified 8-step experiment is not presented as proven Turbo gain.

### Import the existing PRO6000 experiment

Stop the app before importing. The importer verifies the source package and original media hashes before
changing the database, copies the required files to the data directory, and generates playback posters locally.
It never contacts a model service. `--dry-run` does not initialize or migrate the database.

```bash
uv run vgen import-experiment experiments/h3-pro6000.json --dry-run
uv run vgen import-experiment experiments/h3-pro6000.json --data-dir data
```

The full import adds 10 shared cases, 89 attempt records and 87 playable original clips. Repeat imports are
idempotent; changed content under an existing experiment id is rejected. An old database receives a consistent
`app.db.pre-migration-*.bak` backup before additive schema migration. The source archive is never modified.

After import, Scenarios contains two H3 input packs. **View backfill plan** (admin-only on the hosted site) computes
missing Tokenbay combinations without submitting them. The 2026-11-08 repository catalog yields 66 combinations,
approximately $42.90, without AI judging. This is an offline estimate, not a price guarantee or a spending
authorization. Existing successful results and previously attempted combinations are excluded from the ready
list; review previous attempts before explicitly generating again. New results join the comparison through their
input fingerprint, not their title.
The earlier $40.58 estimate included a Seedance Mini promotion that has ended. Each new job saves its price
snapshot so a later catalog update does not change that clip's recorded pricing basis.

### Future PRO5500 and other experiment archives

Copy `experiments/h3-pro6000.json` as the descriptor for a new experiment. Use a new experiment id and deployment
id for a different hardware profile; retain the dataset id only when the cases are identical. The importer is
hardware-independent and accepts the same archive-v1 data format:

- `schema_version: 1`, stable `id`, `title`, `date`, `source_root`, and `dataset: {id, title: {en, zh}}`.
- `deployments` maps the CSV provider values to `{id, model, label, hardware, timing_scope, output_root}`.
  `timing_scope` is `server_local_download` or `client_internet_download`. Hardware is descriptive metadata;
  `gpu`, `gpu_count`, and `memory_gb_per_gpu` are displayed without inferring measurements.
- `files` identifies the package manifest, case definitions, first-frame asset lock, baseline configuration,
  all-attempt CSV, latency groups, complete phase windows, scene summaries, telemetry, cost reconciliation and
  demand-utilization scenarios. Use the existing PRO6000 archive as the version-1 field reference. CSV blanks and
  `NA` stay null. `fl2va` in this version means **first frame only**, mapped to `i2v`, not first+last frame.
- `artifacts` explicitly allowlists downloadable reports and evidence. `support_files` includes any additional
  files needed by reports. Every consumed file must appear in the package manifest with `path`, `packaged_size`
  and `packaged_sha256`; valid output media must also match the attempt CSV's `sha256`.
- `scene-results.csv` selects each default self-hosted request through `matched_baseline_request_id`.
  `all-request-attempts.csv` uses a unique `(provider, request_id, attempt)` within the experiment and preserves
  invalid/cancelled attempts without creating successful video jobs. `video_path` is relative to that provider's
  `output_root`. Configuration, resource and timing differences remain visible and are never pooled across
  experiments. New platforms using this contract appear without changes to React pages.

Version 1 intentionally imports the existing H3 harness format. It does not orchestrate machines or define new
experimental methods. A new archive with a different measurement format needs an explicit format adapter.

### Incremental hosting updates

```bash
deploy/deploy.sh
deploy/upload-experiment.sh experiments/h3-pro6000.json
```

The second command prepares a portable bundle, validates it on the host, briefly stops the service, backs up the
live SQLite database and imports into it transactionally. It preserves existing runs, ratings and visitor data;
never use `FORCE=1 deploy/upload-data.sh` for experiment updates. The previous source and database backup can be
restored together if rollback is needed; imported media is immutable and can remain on disk.

Portable bundles can also be prepared without publishing:

```bash
uv run vgen package-experiment experiments/h3-pro6000.json --output /tmp/h3-publication-bundle
```

Read-only APIs: `GET /api/evaluations`, `GET /api/evaluations/{dataset}/cases/{key}`,
`GET /api/evaluations/{dataset}/backfill-plan`, `GET /api/benchmarks`,
`GET /api/benchmarks/{experiment}`, and `GET /api/benchmarks/{experiment}/export?format=json|csv`.
Generation remains on the existing budgeted Tokenbay APIs and is never triggered by these read endpoints. On the
hosted site the backfill plan and the benchmark export answer 403 unless you are signed in as admin.
