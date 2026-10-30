# Handoff: finish the vgen·eval video-model evaluation platform

> **Status 2026-10-29 (evening): remaining tasks 1–5 and 7 are done.**
> - All jobs are terminal, all 18 seeded clips are judged, and no judge call failed.
> - `probe_status.json` has been rebuilt and actual costs backfilled. The ledger is $19.37 (video $18.48 incl. the
>   possibly-unbilled Veo $2.60, judge $0.89).
> - UI checked in EN/中文 and light/dark, small bugs fixed, and `make test` passes (48).
> - Code changes:
>   - Seedance estimate is W×H×(24·s+1)/1024, matching the reported tokens.
>   - Wan 3.0 is marked `audio: always`.
>   - Poll budget is 20 min.
>   - Run cost now comes from the ledger.
> - Only the optional, paid task 6 is left; ask the user first. The task list below is kept for reference.

You are continuing work in `/Users/caoruixin/projects/poc-H3` (macOS, zsh, not a git repo). The app is built and
tested. What's left is to finish the in-flight results, refresh the probe status, verify the UI, and give the user a
final rundown. Read `README.md` first; it documents the app, the metrics and the probe findings.

## What the app is
A local web app for comparing video-generation models on **tokenbay.com** (Tokenbay's OpenAI-style async video API).
You run the same prompt on several models, watch the clips side by side (synchronized players, blind mode), and see
cost, latency, spec compliance and objective signals, an AI judge's checklist, and your own ratings. A leaderboard
ranks models per scenario, with requirement filters and a quality-vs-cost frontier.

- **Backend:** Python 3.13 (uv), FastAPI, SQLite (SQLModel), in-process asyncio job engine, PyAV + OpenCV analysis.
  Package `vgen/`:
  - `api.py`, `jobs.py` (engine), `gateway.py` (client, rate limiter, error classes)
  - `adapters/` (alibaba = HappyHorse/Wan, veo, seedance)
  - `analysis/`, `judge.py`, `ranking.py`, `runs.py`, `probe.py`, `costs.py`
  - `catalog/{models.yaml, prices.yaml, probe_status.json}`
- **Frontend:** React 19 + Vite 8 + TS 7 + Tailwind 4 in `web/`, built to `web/dist` and served by FastAPI.
  EN/中文 dictionaries live in `web/src/i18n.tsx`.
- **Scenarios:** `scenarios/packs.yaml` has 11 packs in 4 categories, with EN+ZH prompts and checklists. Assets are in
  `scenarios/assets/`: pinned Wan2.1/2.2 example files served via jsDelivr, plus a generated perfume image.
- **Commands:**

  | Command | What it does |
  |---|---|
  | `make setup` | install and build |
  | `make start` | real app at http://127.0.0.1:8765 |
  | `make mock` | fake gateway, no spending, `data-mock/`, http://127.0.0.1:8766 |
  | `make test` | 48 pytest tests, all passing |
  | `pnpm -C web build` | rebuild the frontend |
  | `uv run vgen probe --list \| --dry-run \| --resummarize` | probe tools |

- **Data:** `data/app.db` holds runs, jobs, ratings, picks and the ledger. Videos and frames are under `data/`.

## Hard constraints (from the user)
- **Scope is tokenbay.com only.** Don't read the `TOKENBAY_ME_*` keys, call tokenbay.me, or spend on it.
  `.env` is read-only: never edit it. Only `TOKENBAY_BASE_URL`, `TOKENBAY_API_KEY`, `TOKENBAY_REQUESTS_PER_MINUTE` (15) and
  `TOKENBAY_HTTP_TRUST_ENV` are used.
- **Budget:**
  - The user approved ≈¥150 (~$21) of build-time spending, for probes plus seeded demo runs. The ledger is at about
    **$18.7–19** (worst-case estimates). That figure includes $2.60 for 3 Veo submits that got an instant HTTP 500 and were
    probably not billed.
  - **Do not start new paid generations** without asking the user first.
  - Finishing already-submitted jobs and re-running a failed judge call (~$0.03 each) is fine.
  - Start the server with `VGEN_HARD_CAP_USD=21` so the engine refuses submits beyond the cap.
- **No background `claude` CLI jobs.** Running the app server with Bash `run_in_background` is fine.
- **Communication:** the user writes English. Keep updates short and plain. Ask before anything destructive.

## Gateway facts learned (already handled in the code)
- **Field names:** task JSON is **camelCase** (`taskId`, `submitTime`, `finishTime`, `failReason`, `resultUrl`). There is no
  `quota` field. Seedance returns `usage.totalTokens`, which gives an "actual" cost; per-second models use billed seconds × price.
- **Veo 3.1** (all 3 models) returns HTTP 500 on every submit. It's marked `unavailable` in `probe_status.json`, and the
  Models page has a paid "Re-check" button. Don't re-check without asking.
- **Gemini judge:** the route rejects inline **video** (upstream 400) but accepts images, so the judge
  (`gemini-3.1-pro-preview`) looks at 8–16 timestamped frames. Checklist items that only concern sound are "not assessable".
- **Wan:**
  - Wan 3.0 only accepts 720p/1080p.
  - Wan 2.7 can take 11 min for a 3 s clip, or 37 s.
  - HappyHorse 1.0 and Wan 2.7 always add audio.
- **Inputs:**
  - Data-URI images work for HappyHorse, Wan 2.7 and Seedance.
  - Seedance needs `role: reference_video` / `reference_audio` on video and audio inputs.
  - Seedance content safety rejected the `market_clip` source (cartoon boy, from the Wan2.2 repo) for extend.
- **Rate limit:** about 20 requests per clock minute, after which you get a 401 "rate limit" response. The app's limiter is set to 15 rpm.

## State when the last session stopped (verify; don't trust)
- **Servers:** both were stopped cleanly. Unfinished jobs resume automatically when the server starts. Polling
  continues and mp4s download. Start within about 24 h, since results may expire.
- **Seeded example runs** (kind `pack`, judge on, one prompt each):
  - "Cinematic brand film" (`run_f4fba6f7f6bc`, Li River prompt): Seedance 2.0/2.5, HappyHorse 1.0 T2V, Wan 2.7 T2V, Wan 3.0.
  - "Animate a photo (I2V)" (cat): Seedance 2.0/mini, HappyHorse I2V, Wan 2.7 I2V.
  - "Keep my characters consistent (R2V)" (girl + snake, New Year): HappyHorse R2V, Wan 2.7 R2V, Seedance 2.0/mini.
  - "Native dialogue & sound effects" (night-market vendor): Seedance 2.0/fast, HappyHorse T2V, Wan 2.7 T2V.

  At the stop, most clips had succeeded. A few were still generating or downloading (both Wan 2.7 T2V jobs, HappyHorse in dialogue,
  Seedance mini R2V), and several judge calls were pending.
- **Second-round probe jobs** (kind `probe`): `wan30-t2v`, `wan30-i2v` (the poll showed "Video generation failed"),
  `sdmini-extend` (content-safety rejected), `sdmini-a2v`, plus a re-judge of the old `hh10-t2v` probe clip.
  `probe_status.json` has **not** been rebuilt from them yet.
- **Not yet backfilled:** some ledger entries that finished before the "actual cost" code have `actual_usd = null`.

## Remaining tasks
1. **Start and wait.**
   - Run `VGEN_HARD_CAP_USD=21 VGEN_MAX_INFLIGHT=12 uv run vgen serve --port 8765` in the background.
   - Wait until every job is terminal and no judge is pending/running. Check with `GET /api/runs` and `GET /api/runs/{id}`.
   - Use Python `urllib` or `curl -o file`. A shell hook rewrites raw `curl` JSON output.
2. **Re-run failed judges.** State-changing calls need the header `x-vgen-token`. Its value is embedded in `GET /` HTML as
   `window.__VGEN__={"token": ...}`. Re-run with `POST /api/jobs/{id}/judge`.
3. **Rebuild the probe status** with `POST /api/probe/resummarize` (free; re-analyzes probe clips with the area-based
   resolution check). Confirm in `vgen/catalog/probe_status.json` that:
   - Veo is still `unavailable`
   - wan3.0 t2v is ok (and i2v as found)
   - seedance-2.0-mini extend fails because of content safety, and a2v shows whatever result it got
   - the judge shows frames-mode ok
4. **Backfill actual costs.** For succeeded jobs, call `vgen.runs.settle_actual(registry, job)` in a small script
   (`vgen.db.init_db("data/app.db")`, then `Registry.load()`). Then check that `GET /api/ledger` totals still make sense.
5. **Check the UI in the browser** (claude-in-chrome: load the core tools in one ToolSearch, then open a new tab). Check:
   - Scenarios, Run board (sync play, judge panel, ratings, blind mode), Leaderboard (badges, scatter, table), Models and Settings
   - the EN/中文 toggle and dark mode

   Fix small UI bugs, rebuild with `pnpm -C web build`, and keep `make test` green. Don't trigger `alert`/`confirm`; the app
   uses inline two-step buttons.
6. **Optional, ask first:**
   - replace the extend/edit source clip with one Seedance accepts
   - re-check Veo
   - run more prompts per scenario. n=1 per model so far, so rankings are indicative only.
7. **Final rundown for the user.** Cover:
   - how to run it and the URL
   - what each page does
   - per-scenario observations from the seeded results: judge verdicts, defects, cost/clip, latency. Say clearly that each is n=1
     and judged from frames.
   - gateway findings: Veo down, slow Wan, audio behaviour
   - total spend from the ledger (`GET /api/ledger`), split by kind and model, and noting the possibly-unbilled Veo $2.60
   - next steps: rate clips (👍/👎, stars, pick best) so the leaderboard switches to human quality after ≥3 ratings; add
     more prompts; enable tokenbay.me later (see the README)
