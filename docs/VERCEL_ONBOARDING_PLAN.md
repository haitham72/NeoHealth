# Vercel frontend + cold-start onboarding — implementation plan

Written 2026-09-04. Target: move `frontend/` to Vercel, keep the FastAPI backend on Render,
and make the ~52s Render cold start invisible instead of fatal.

Measured 2026-09-04 against `neohealth-gpzo.onrender.com`:

```
cold:  http=200  time=51.673s
warm:  http=200  time=0.790s
```

---

## 0. Two findings that change the design — read before planning

### 0.1 The stream timeout survives a cold start by 8 seconds, by luck

`frontend/src/api/client.ts:145`

```ts
const STREAM_IDLE_TIMEOUT_MS = 60_000;
```

The idle timer is armed **before** the `fetch` (`client.ts:156`) and only re-arms once frames
start arriving. So on a cold backend the whole 60s budget is spent waiting for Render to boot:
51.7s measured, 60s allowed. **8.3 seconds of margin**, on a number that varies with Render's
load.

That means the current app does not reliably fail on a cold start — it reliably *nearly* fails,
and when Render is slower than average the first message dies with *"Request timed out — the
server may be slow or unreachable."*

**This is the strongest argument for the queue design.** Holding the message until `/ready`
returns 200, then calling `streamAsk`, means the 60s idle timer starts against a *warm* server
with its full budget intact. The queue is not UX polish — it is what makes the existing timeout
correct. Do not raise `STREAM_IDLE_TIMEOUT_MS` to paper over this; raising it also delays every
genuine failure by the same amount.

### 0.2 The frontend assumes same-origin, and Vercel breaks that

Every call is a **relative path** (`fetch("/ask-stream")`, `client.ts:168`), resolved in dev by
the Vite proxy (`vite.config.ts:8-18`) and in production by nginx inside the Docker image
(`frontend/Dockerfile`, `frontend/nginx.conf`) — the frontend and backend are one origin on
Render today. There is **no `VITE_API_URL` anywhere in the codebase.**

Split them across Vercel and Render and every request 404s against Vercel's static host.

**Decision: absolute base URL + CORS on FastAPI. Not Vercel rewrites.**

A `vercel.json` rewrite would keep the code unchanged and avoid CORS, which is tempting. Reject
it: the app's primary path is **SSE streaming** (`/ask-stream`), and putting a proxy between the
browser and the stream is a well-known source of buffering — the trace steps arrive in one clump
at the end instead of incrementally, which destroys the `ThinkingSteps` experience that the whole
onboarding is built to show off. Go browser-direct to Render.

Work required:
- Add `VITE_API_URL` and a small `apiUrl(path)` helper; route all eight endpoints through it
  (the eight are enumerated in `vite.config.ts`).
- Keep the Vite dev proxy so local development is unchanged (`VITE_API_URL=""` locally).
- Add CORS to FastAPI for the Vercel origin. **Without this the browser blocks every call and
  swallows the reason** — the app will sit in "starting" forever with nothing in the console
  that names the cause. This is the most likely thing to break the whole build.

> **Confirmed live 2026-09-04.** This predicted failure mode is exactly what's happening right
> now. The actual current Vercel deployment is `https://frontend-tawny-kappa-10.vercel.app`
> (project `frontend` under the `system722-1077` Vercel account — `vercel project ls` from the
> repo root shows it; there's no `vercel.json` in the repo, so it isn't git-linked, presumably
> deployed by running `vercel --prod` directly from a local `frontend/` working tree at some
> point). `render.yaml`'s `ALLOWED_ORIGINS` only listed `frontend-blue-zeta-82.vercel.app` — a
> *different, stale* URL — so every request from the real live site was silently CORS-blocked
> (`net::ERR_FAILED`, confirmed via a headless-browser check of the live URL). Fixed in this
> commit by adding `frontend-tawny-kappa-10.vercel.app` to `render.yaml`'s `ALLOWED_ORIGINS`.
> Caveat: editing `render.yaml` only takes effect on Render's next deploy *if* the service is
> Blueprint-managed and in sync; if `ALLOWED_ORIGINS` was instead set by hand in the Render
> dashboard, that manual value wins and needs updating there directly — verify after deploying.
> Separately, production Render (`neohealth-gpzo.onrender.com`) is still serving an old build:
> `/health` and `/ready` both 404 live (checked directly), so the readiness-polling flow in §2
> below cannot work at all until Render redeploys with this branch's backend changes.

---

## 1. Architecture

```
Vercel (static)                        Render (FastAPI + pgvector)
├── onboarding slides  ──── poll ────▶ GET  /ready      (cheap, touches DB)
├── chat UI            ──── SSE  ────▶ POST /ask-stream
└── queued message                     POST /ask, /pdf, /diff-followup, ...
```

Supabase is **not** part of this. The backend is FastAPI + Docling + pgvector and needs a
container host; Supabase would only replace the database, not give the process a home. That is a
separate decision and it is out of scope here.

---

## 2. Phase 1 — readiness (do this first, everything else depends on it)

### Backend

Two endpoints, distinct on purpose:

```python
@app.get("/health")          # liveness only, no DB — for the keep-alive cron
async def health(): return {"ok": True}

@app.get("/ready")           # readiness — the frontend polls THIS
async def ready():
    await db.execute("select 1")     # proves pgvector is reachable, not just that Python booted
    return {"ok": True}
```

Polling `/health` would advance the user to the chat while the DB is still cold or the free
Postgres has expired — a 200 from the process is not proof the retrieval path works.

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://<app>.vercel.app", "http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
```

### Frontend — `src/hooks/useBackendReady.ts`

- Fire poll #1 at t=0. **On Render the first inbound request is what triggers the wake**, so the
  poll is the wake-up call. There is no separate "ping to wake" step.
- Then every 5s. `AbortController` at 4.5s so at most one request is ever in flight; an
  `inFlight` guard so a hung request cannot stack a queue behind it.
- Treat **everything** non-200 as "still booting" — 503s, connection resets and aborts are all
  normal during a Render boot, none of them is an error.
- Cache `ready` in `sessionStorage` so an in-session remount does not re-run the whole dance.
- Give up at 120s (~2x measured cold start) → `failed`.

Exposes `{ ready, elapsed, failed }`. Nothing else in the app polls.

---

## 3. Phase 2 — onboarding slides

> **Superseded 2026-09-04, twice.** First replaced by a vertical, scroll-driven product page
> (`IntersectionObserver`-based section reveals, no swipe) per `docs/ONBOARDING_REDESIGN_HANDOFF.md`
> — then, later the same day, that vertical page was itself replaced by a compact **blocking modal
> wizard** (dimmed backdrop, two-panel dialog, 3 sliding steps) per direct instruction; see the
> superseded-note at the top of `ONBOARDING_REDESIGN_HANDOFF.md` and
> `frontend/src/components/OnboardingWelcome.tsx` for what's actually live now. Everything else in
> this section — that onboarding never gates readiness, that it covers the wait rather than
> depending on it, and the `sessionStorage` completion semantics — is still accurate; only the
> interaction mechanics changed, twice.

Shown immediately on visit, while Phase 1 polls in the background. **The user is never blocked by
readiness** — they can click through and reach the chat whenever they like. The slides cover the
wait; they do not gate on it.

Three slides, matching what the app actually does:

1. **What this is** — version- and jurisdiction-aware retrieval over UAE health regulation.
2. **Why it abstains** — tiered confidence; it declines below a calibrated floor rather than
   answering everything with equal certainty.
3. **Citations are exact** — Docling per-element bounding boxes, so a citation highlights the
   real passage, not a guessed window.

Design notes: `prefers-reduced-motion` respected; a visible Skip on every slide; slide state in
`sessionStorage` so a returning visitor in the same session goes straight to chat.

**If the user skips at t=3s and the backend is not ready, that is fine** — they land on the chat
and Phase 4 handles it. Nothing here assumes the slides bought enough time.

---

## 4. Phase 3 — in-chat feature popup

> **Built 2026-09-04** as `frontend/src/components/FeaturePopup.tsx` — matches this spec closely:
> 3 panels (superseded-versions default, version comparison, source/PDF trail), `localStorage`-gated
> (`regulense-feature-popup-v1`, one-time), re-openable from a `?` button in `ChatHeader.tsx`. One
> deliberate deviation from "swipe": it's a **blocking modal** (dimmed backdrop, horizontal sliding
> deck, Next/Back/Got it) rather than a lightweight dismissible toast, per direct instruction that
> this popup should have real presence, not fade into the corner.

Three swipes, on first arrival at the chat only (`localStorage`, dismissible, re-openable from a
`?` in `ChatHeader`). These map to components that already exist — the popup explains real UI, so
it should be written against those files, not invented:

| Swipe | What to explain | Components |
|---|---|---|
| 1 | Exclude superseded versions — the filter, and what the badge means | `SupersessionBadge.tsx`, `VersionLedger.tsx` |
| 2 | Compare against the previous version | `DiffFollowup.tsx`, `CrossCheckRegulation.tsx` |
| 3 | Check sources — open the cited passage in the PDF | `ResearchSources.tsx`, `SourceCard.tsx`, `CitationPopover.tsx`, `PdfOverlay.tsx` |

The supersession filter defaults to `true` (`App.tsx:33`) — swipe 1 must say so, because a user
who does not know that will not understand why older regulations are missing from results.

---

## 5. Phase 4 — the queued first message

The core of the design. **The user sends normally; the UI holds the request until ready.**

`App.tsx` already has the state this needs:

- `askInFlightRef` (`App.tsx:26`) — the existing rapid-click guard. Extend it, do not add a
  second competing lock.
- `steps: TraceStep[]` + `ThinkingSteps` (`ThinkingSteps.tsx`) — already renders a step list with
  a pulsing `thinking…`.

**The queued state is a synthetic `TraceStep`, not a new component.** Add one label to
`STEP_LABELS` in `ThinkingSteps.tsx`:

```ts
waiting_for_backend: (s) => `Starting the server — ${s.detail ?? "0"}s`,
```

Flow in `ask()`:

1. Append the user message as it does today (`App.tsx:81`) — the message appears instantly.
2. If `!ready`: push a `waiting_for_backend` step and update its `detail` each second from
   `elapsed`. Do **not** call `mutation.mutate` yet.
3. When `ready` flips true: drop that step, then `mutation.mutate(...)` exactly as today. The
   real trace (`embedding_query` → `searching_sources` → `citing_source` → `generating_answer`)
   streams in behind it, and the 60s idle timer starts warm with full headroom (§0.1).
4. If `failed`: replace the step with an error state on that message. It must not spin forever.

Second and later messages skip all of this — `ready` is already true, so `ask()` runs unchanged.

**Queue in order.** If three messages are sent while booting, send them sequentially after ready.
`askInFlightRef` already prevents parallel asks; make sure the queued path respects it rather
than bypassing it.

---

## 6. Still add the keep-alive cron

Phase 1–4 make the cold start *survivable and honest*. They do not remove it — a recruiter still
waits ~52s on the first visit of the day.

A cron ping every 10 minutes (cron-job.org / UptimeRobot) against `/health` keeps Render awake and
the boot path essentially never fires. Then all of the above becomes the safety net for when the
cron fails.

Constraint: Render free is **750 instance-hours/month account-wide**. One always-on service costs
~730. It fits — but only for one service, so `cron_linkedin` cannot also live there on free.

---

## 7. Acceptance test

1. **Warm visit** — slides appear, no "starting the server" text ever renders, chat responds in
   under 2s.
2. **Cold visit, user waits** — slides play, user reaches chat, sends nothing, first message after
   the boot completes answers immediately.
3. **Cold visit, user skips and sends at t≈3s** — message appears instantly, thinking box shows
   "Starting the server — Ns" counting up, then the real trace streams and the answer lands.
   **No timeout error.** This is the case the current build nearly fails (§0.1) and the one that
   proves the plan.
4. **Backend down** — after 120s the queued message shows a clear failure, not a spinner.
5. **CORS** — verified from the deployed Vercel origin, not localhost. A localhost pass proves
   nothing, because the Vite proxy hides the cross-origin problem entirely.

---

## 8. Do not

- Do not raise `STREAM_IDLE_TIMEOUT_MS` (§0.1).
- Do not use a fixed countdown. Poll and show real elapsed seconds; a timer that does not
  correspond to anything is what reads as broken.
- Do not proxy SSE through Vercel rewrites (§0.2).
- Do not block the chat route on readiness — the whole point is that the user is never gated.
- Do not autoplay audio. Browsers block it without prior interaction.
