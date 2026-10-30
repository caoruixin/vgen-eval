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

## Development

```bash
make test         # 48 tests: adapters, costs, gateway errors, limiter, analysis on synthetic clips, mock end-to-end
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
