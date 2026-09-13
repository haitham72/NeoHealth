# Semantic Answer Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On each ask, check a durable semantic cache of prior Q&A; on hit return the stored answer with zero LLM/chat calls and show a tiny UI “cache hit” cue; on miss run the normal pipeline and store the result. Persist across Render free-tier sleep.

**Architecture:** Store cached answers in the existing Postgres+pgvector database (new `answer_cache` table). Lookup embeds the question once, then cosine-searches prior query embeddings filtered by exact `superseded_filter` + `authority_filter`. Hit → short-circuit before retrieval and `generate_answer` / streaming LLM. Miss → full pipeline, then insert. LangSmith tags/metadata record hit vs miss and the match mode. Frontend shows a bottom-of-trace “Cache hit” line from a new SSE step + `cache_hit` on the result.

**Tech Stack:** Postgres + pgvector (already in repo), OpenAI `text-embedding-3-small` (already used), FastAPI SSE `/ask-stream`, React `ThinkingSteps` / `AssistantMessage`, LangSmith `@traceable` + `get_current_run_tree()`.

**Spec:** Decisions locked in this plan (no separate design doc). See “Locked decisions” and “Why not Redis/Mongo” below.

## Amendment (2026-09-13): Redis is required, diff-followup is in scope, Postgres gets pre-baked

**Context that changes the calculus:** this app's only real traffic is the author running
their own demo during interviews — a handful of visitors ever, not the public. This
flips several of the original v1 calls, and adds one the original plan didn't have at all:

- **Redis moves from "optional nice-to-have" to "actually run it in prod."** The
  original concern (Redis on Render free is a second billable/free-tier service for
  marginal benefit at real scale) doesn't apply when total traffic is one person
  clicking through a demo. A free Upstash instance costs nothing and turns every
  repeat question in an interview into a sub-50ms response instead of a Postgres round
  trip. **`REDIS_URL` is now set in prod (Upstash), not just local Docker.**
- **Redis is unconditionally warm-loaded from Postgres on every process boot** — not
  a fallback path, the normal path every time the process starts. The interviewer's
  Redis instance realistically lives for a few minutes per interview session and then
  sits idle for days or weeks until the next one; nothing about "was Redis recently
  used" can be assumed true at boot. Postgres is the permanent source of truth precisely
  *because* Redis's uptime pattern is this bursty — every boot re-derives a fully warm
  Redis from it, with no partial or conditional loading. See Task 3.
- **`/diff-followup` ("compare with last year") gets its own cache**, previously
  out of scope entirely. It's a separate endpoint from `/ask`/`/ask-stream` with its own
  key shape (doc pair + question, no embedding needed — see Task 6) but the same spirit:
  zero LLM calls on a repeat, durable in Postgres, warm-loaded into Redis at boot.
- **Postgres is pre-baked with the full demo question set before an interview, not
  just organically filled by live asks.** The realistic universe here is small and
  known in advance — on the order of ten question phrasings across the corpus, each
  potentially followed by a "compare with last year" click — so there is no reason to
  rely on the *first* time a question is asked live being slow. A one-time seed script
  (Task 7) runs the real pipeline once per question (and once per resulting diff
  follow-up) ahead of time and writes every result into `answer_cache` /
  `diff_cache`. Combined with the unconditional boot warm-load, this means every
  question in the known set is already a Redis hit from the very first live request of
  an interview — not just the second time it's asked.

This does not change the core `/ask` design (dual store, semantic L2 floor, gating in
`retrieval.py`) — it changes whether Redis actually ships to prod, adds a second,
simpler cache for the diff endpoint, and adds an explicit pre-bake step so the cache is
never depending on live traffic to warm up during the interview itself. The "Why not
Redis or MongoDB?" section below still explains why Postgres stays the durable source of
truth and why semantic matching stays Postgres-only (RediSearch would be new infra for
one query type); it no longer means "don't run Redis at all."

## Locked decisions (updated)

| Topic | Decision |
| --- | --- |
| Cache key / hit scope | **Query + all ask filters that are not LLM choice:** `superseded_filter` + `authority_filter`. **Provider/model never part of the key.** |
| LLM on hit | **No chat/LLM call.** Embedding still runs when checking Postgres semantic L2 (or after Redis exact miss). |
| Dual store | **L1 Redis (exact key)** → **L2 Postgres/Supabase (`answer_cache` semantic)**. Miss both → answer → **write both**. Postgres hit → **backfill Redis**. |
| Redis persistence | **Required in prod now (Amendment above), not optional.** Sleeping the **Render web** does **not** wipe Redis because Redis lives on a **separate** managed service (Upstash) with persistence — the free web dyno never hosts Redis itself. Local dev: Redis via `docker compose` with a named volume. **Postgres is always the durable source of truth and the boot-time warm-load source for Redis (Task 4)**, so even a wiped/rotated Redis self-heals on next boot. |
| History | **Only cache when `history` is empty/None.** |
| What to store | Successful **answered** payloads only (not abstentions) for v1. Diff-followup: only `available: true` results (Task 7). |
| Similarity floor | **0.92** cosine on Postgres L2 (`CACHE_HIT_THRESHOLD`). |
| LangSmith | Tags `cache-hit` / `cache-miss`; metadata: `cache_layer` (`redis` \| `postgres` \| null), `cache_match_mode` (`exact_key_plus_filters` \| `query_plus_filters`), similarity, filters. |
| Diff-followup cache | **New, Task 6.** Exact-key only (no semantic search — the question is already anchored to one citation): key = `(current_document_id, previous_document_id, question_normalized)`. Same dual-store + boot-warm-load pattern as `/ask`. |
| Pre-baking | **New, Task 7.** A one-time seed script runs the real pipeline for every question in a user-supplied demo question list (and its diff follow-ups) and writes results straight into `answer_cache` / `diff_cache` before an interview. Not run automatically (costs real LLM calls); not part of CI/deploy. |

### Redis vs sleep (plain English)

| Setup | Web sleeps 15m | Data still there? |
| --- | --- | --- |
| Redis inside the free web container / no volume | Dyno gone | **No — reset** |
| Docker Redis + named volume (local) | n/a | **Yes** across `compose down/up` if volume kept |
| Upstash / managed Redis + `REDIS_URL` | Web sleeps; Redis stays up | **Yes** |
| Postgres on Supabase (`answer_cache`) | Web sleeps | **Yes — always the backup** |

So: dual cache is proper — Redis for fast exact hits; Supabase/Postgres so nothing important depends on Redis surviving.

## Global Constraints

- Dual cache: Redis L1 (**required in prod via `REDIS_URL`** — Upstash; see Amendment) + Postgres L2 (required durable store). No MongoDB.
- Postgres must survive Render free web sleep; Redis lives on Upstash so web sleep never touches it, and Postgres warm-loads Redis on every boot regardless (Task 3) — belt and suspenders.
- The boot warm-load (Task 3) is unconditional and total: every row in `answer_cache` and `diff_cache`, every time the process starts. No "only if Redis looks empty" check — the bursty few-minutes-then-idle-for-weeks Redis lifecycle means that check can't be trusted anyway.
- On cache hit (either layer, `/ask` or `/diff-followup`): zero `chat.completions` / `generate_answer` / NaraRouter calls.
- Redis and Postgres writes on a miss/store path are both best-effort: a Redis failure (e.g. Upstash hiccup) must never break the answer or skip the Postgres write, same spirit as `enrich_result`.
- Do not break citation-or-abstain behavior; cached rows are prior grounded answers only.
- Follow existing patterns: SCHEMA_SQL migrations in `db.py`, `@traceable` in `retrieval.py`, SSE steps for the UI, fire-and-forget enrichment stays after the core result.
- Frontend “cache hit” cue stays tiny (trace line / footer), matching existing `ThinkingSteps` language — not a new dashboard card system.

---

## Why not Redis or MongoDB?

- **Redis:** Great for exact-key TTL caches. Semantic (embedding) search needs Redis Stack / RediSearch or a custom scan. Render free does not give durable Redis; a dyno-local Redis dies on sleep. Upstash would work but is a second billable/free-tier service when Postgres already has vectors.
- **MongoDB:** Atlas Vector Search works, but this app is already Postgres+pgvector end-to-end. A second database for one table is unnecessary.
- **Postgres+pgvector (chosen):** Same `DATABASE_URL`, same embed model (1536-dim), same ops story. If `DATABASE_URL` points at Supabase/Neon (not the sleeping web container), rows survive sleep.

## Hosting: Render vs Supabase (after local works)

```
[Browser] → [Render free web: FastAPI + static] → [Supabase/Neon Postgres: documents, chunks, answer_cache]
                      ↑ sleeps after ~15m                    ↑ stays up; cache persists
```

1. **Keep the API on Render free** — current `render.yaml` / dashboard deploy stays; no need to move Python to Supabase.
2. **Move or keep the database off the sleeping web process** — set Render env `DATABASE_URL` to a managed Postgres with `vector` extension (Supabase project → Database settings → connection string, or Neon). Enable `vector` in Supabase SQL once: `create extension if not exists vector;`.
3. **Do not host the FastAPI app “in Supabase”** — Supabase is not a general Python app host; Edge Functions are a different runtime. Wrong tool.
4. **Do not rely on Render disk or process memory for cache** — wiped/irrelevant on sleep/restart.
5. **Optional later:** paid Render always-on or Render Key Value — not required if Postgres is external.

Local first: `docker compose` Postgres already persists in the `pgdata` volume; same schema works locally and in prod.

---

## File map

| File | Role |
| --- | --- |
| `backend/app/core/db.py` | `answer_cache` table DDL already present; Task 1 adds `diff_cache` |
| `backend/app/core/answer_cache.py` | **already implements** the dual-layer (Redis L1 + Postgres L2) `/ask` cache; Task 2 adds tests, Task 4 wires it into `retrieval.py` |
| `backend/app/core/cache_warmup.py` | new (Task 3): `warm_all_caches()`, unconditional every-boot Postgres → Redis load for both tables |
| `backend/app/core/diff_cache.py` | new (Task 6): same dual-layer shape as `answer_cache.py`, for `/diff-followup` |
| `backend/app/core/retrieval.py` | already imports the cache functions (Task 4 makes them actually get called); LangSmith flags; store on miss |
| `backend/app/api/routers/diff.py` | gate at start of `diff_followup`; store on miss |
| `backend/app/main.py` | existing startup hook (`_run_schema_migrations`) gains a `warm_all_caches()` call |
| `backend/app/core/config.py` | `CACHE_HIT_THRESHOLD`, `REDIS_URL`, `CACHE_REDIS_TTL_SECONDS` already present |
| `backend/tests/test_answer_cache.py` | new file: hit/miss/filter mismatch/history skip, Redis exact-key hit/backfill |
| `backend/tests/test_diff_cache.py` | new file: diff-followup hit/miss |
| `backend/tests/test_cache_warmup.py` | new file: boot warm-load covers every row of both tables |
| `backend/scripts/seed_answer_cache.py` / `demo_questions.json` | new (Task 7): pre-bake the demo question set |
| `frontend/src/types/api.ts` | `cache_hit`, `cache_layer`, `cache_similarity`, stream step types |
| `frontend/src/components/ThinkingSteps.tsx` | label for `cache_hit` / `cache_miss` steps |
| `frontend/src/components/AssistantMessage.tsx` (or tiny footer) | persistent “Served from cache” when `response.cache_hit` |
| `frontend/src/index.css` / tokens | minimal style for the cue (reuse brass/ink tokens) |
| `docker-compose.yml` | `redis` service already present; Task 8 fixes its stale "don't run Redis in prod" comment |
| Render dashboard | `REDIS_URL` env var pointing at Upstash (Task 8) |

---

## Pre-existing code found on this branch (read before Task 1)

Commit `cf4821b` ("Implement Redis caching for improved answer retrieval performance"),
already on this branch before this plan's execution began, wrote most of the `/ask`
data layer directly — no tests, no ledger, not through this skill. **Do not treat Tasks
1-2 below as greenfield; they are "add the missing pieces and the tests that should have
existed" tasks against real code that already works.** What's already there:

- `backend/app/core/db.py` — the `answer_cache` table DDL already exists in `SCHEMA_SQL`,
  matching Task 1's original design exactly (including the `superseded_filter` +
  `authority_filter` index). **`diff_cache` does not exist yet** — that's the only schema
  gap.
- `backend/app/core/config.py` — `REDIS_URL`, `CACHE_HIT_THRESHOLD` (default `0.92`, matches
  the locked decision), and `CACHE_REDIS_TTL_SECONDS` (default 7 days — a Redis key TTL,
  separate from the boot warm-load this plan still needs; see Task 3) already exist.
- `backend/app/core/answer_cache.py` — **already implements the full dual-layer design**
  for `/ask`, combined into one file rather than split into `answer_cache.py` +
  `redis_cache.py`. Read it before writing anything here. Key exports actually used
  everywhere below: `normalize_question(question)`, `lookup_answer_cache(conn, question,
  query_vec, superseded_filter, authority_filter, threshold=None)`, `store_answer_cache(conn,
  question, query_vec, superseded_filter, authority_filter, result)`,
  `decorate_cached_result(hit, run_id)`, and the constants `MATCH_EXACT =
  "exact_key_plus_filters"` / `MATCH_SEMANTIC = "query_plus_filters"` (these are the exact
  `cache_match_mode` strings the rest of this plan, including the frontend types in Task 5,
  already assumes). **`lookup_answer_cache` was deliberately written to support a
  Redis-only probe:** call it once with `query_vec=None` to check Redis alone (no embed
  call at all); if that returns `None`, embed the question and call it again with the real
  vector, which checks Redis again then falls through to the Postgres cosine query and
  backfills Redis on a Postgres hit. `store_answer_cache` already writes both Postgres and
  Redis. None of this is wired into `retrieval.py` yet (Task 4) and none of it has tests
  (Tasks 1-2).
- `docker-compose.yml` / `requirements.txt` — the `redis` service (named volume, AOF) and
  the `redis` Python package are already present. The docker-compose comment still says
  *"do NOT run Redis on the sleeping web dyno"* — that line describes the pre-Amendment
  decision and needs updating in Task 8 once Upstash is actually wired into prod.

Given this, **build the new `diff_cache.py` (Task 6) as the same shape of module** —
Redis-first + Postgres-backed, best-effort writes to both, one file — for consistency
with what's already shipped, rather than introducing a different structure.

---

### Task 1: Schema — add the missing `diff_cache` table, and tests for both tables

**Files:**
- Modify: `backend/app/core/db.py` (`SCHEMA_SQL`) — add `diff_cache` only; `answer_cache`'s DDL already exists and must not be touched or duplicated
- Test: `backend/tests/test_answer_cache.py`, `backend/tests/test_diff_cache.py` (new files — neither exists yet)

**Interfaces:**
- Produces: table `diff_cache`, usable by Task 6 and Task 3's boot warm-load. Confirms the existing `answer_cache` table via a test that should have shipped with it.

- [ ] **Step 1: Write failing tests that both tables exist**

```python
# backend/tests/test_answer_cache.py
def test_answer_cache_table_exists(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'answer_cache'
            """
        )
        assert cur.fetchone() is not None
```

```python
# backend/tests/test_diff_cache.py
def test_diff_cache_table_exists(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'diff_cache'
            """
        )
        assert cur.fetchone() is not None
```

- [ ] **Step 2: Run tests** — `test_answer_cache_table_exists` should already PASS (table exists); `test_diff_cache_table_exists` should FAIL

Run: `cd backend && pytest tests/test_answer_cache.py::test_answer_cache_table_exists tests/test_diff_cache.py::test_diff_cache_table_exists -v`

- [ ] **Step 3: Add ONLY `diff_cache`'s DDL to `SCHEMA_SQL`**

Append (idempotent) — do not re-add or modify the existing `answer_cache` DDL:

```sql
CREATE TABLE IF NOT EXISTS diff_cache (
    id SERIAL PRIMARY KEY,
    current_document_id INTEGER NOT NULL REFERENCES documents(id),
    previous_document_id INTEGER NOT NULL REFERENCES documents(id),
    question_normalized TEXT NOT NULL,
    question_raw TEXT NOT NULL,
    result_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    hit_count INTEGER NOT NULL DEFAULT 0,
    last_hit_at TIMESTAMPTZ,
    UNIQUE (current_document_id, previous_document_id, question_normalized)
);
```

Normalize questions the same way as the existing `answer_cache.py`'s `normalize_question`
(`" ".join(question.strip().lower().split())`) — Task 6 imports that function rather than
forking it. No vector index needed; `diff_cache` is exact-key only.

- [ ] **Step 4: Ensure tests apply schema (reuse `conftest` init)** — both tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/db.py backend/tests/test_answer_cache.py backend/tests/test_diff_cache.py
git commit -m "feat: add diff_cache table; add missing tests for answer_cache and diff_cache schema"
```

---

### Task 2: Tests for the existing dual-layer `answer_cache.py` module

**This is a test-retrofit task, not new implementation.** `backend/app/core/answer_cache.py`
already implements everything described below (see "Pre-existing code" above); it shipped
with zero test coverage. Write the tests the module should have had from the start. If a
test reveals an actual bug, fix the minimum needed to make it pass and note the fix in the
commit message — do not rewrite or restructure working code beyond that.

**Files:**
- Test only: `backend/tests/test_answer_cache.py` (no modification to `answer_cache.py`
  expected unless a test finds a real bug)

**Interfaces (already implemented, exercise via tests):**
- `normalize_question(question: str) -> str`
- `lookup_answer_cache(conn, question, query_vec, superseded_filter, authority_filter, threshold=None) -> dict | None` — returns `{"result", "cache_layer", "match_mode", "similarity", "matched_question", "cache_id"}` or `None`. Called with `query_vec=None` for a Redis-only probe (see below); called again with a real vector to also check Postgres L2 and backfill Redis.
- `store_answer_cache(conn, question, query_vec, superseded_filter, authority_filter, result) -> None` — no-ops on `result["abstained"]`; writes Postgres then Redis, both best-effort.
- `decorate_cached_result(hit: dict, run_id: str | None) -> dict`

- [ ] **Step 1: Failing tests**

```python
def test_redis_only_probe_returns_none_without_embedding(conn, redis_conn):
    # lookup_answer_cache(conn, question, query_vec=None, ...) on an empty cache -> None,
    # and must not require/attempt any embedding computation
    ...

def test_redis_exact_hit_after_store(conn, redis_conn):
    # store_answer_cache(...) with a real-shaped answered result, then
    # lookup_answer_cache(conn, same_question, query_vec=None, same filters) -> hit,
    # cache_layer == "redis", match_mode == MATCH_EXACT
    ...

def test_postgres_semantic_hit_backfills_redis(conn, redis_conn, fixed_vec):
    # insert an answer_cache row directly via store_answer_cache with a known embedding
    # (bypass Redis first by flushing/using a different question string so Redis misses,
    # or by testing lookup_answer_cache's Postgres path directly against a near-duplicate
    # vector) -> cache_layer == "postgres", match_mode == MATCH_SEMANTIC, similarity >= threshold
    # then confirm a second lookup_answer_cache(..., query_vec=None) now hits Redis
    ...

def test_lookup_miss_different_authority_filter(conn):
    # same embedding/question, authority_filter "DHA" vs "DoH" -> miss
    ...

def test_lookup_miss_below_threshold(conn):
    # dissimilar vector -> None
    ...

def test_store_answer_cache_skips_abstained_results(conn, redis_conn):
    # store_answer_cache(..., result={"abstained": True, ...}) writes nothing to either layer
    ...
```

Use a fixed fake unit vector in tests when possible to avoid OpenAI calls (construct
`vector` literals / pass precomputed lists). Prefer not calling live OpenAI in unit tests.
If a real Redis isn't guaranteed in the test environment, use `fakeredis` (add to
`backend/requirements.txt` as a dev/test dependency) — coordinate with Task 3, which needs
the same fixture.

- [ ] **Step 2: Run — FAIL** (module exists, but nothing exercises it yet)

- [ ] **Step 3: Make them pass** — against the existing implementation. Expected: most pass
unmodified. Where the docstring's contract ("`query_vec` may be `None` only when Redis
hits... callers that miss Redis must pass an embedding for L2") doesn't match actual
behavior, that's a real bug — fix `answer_cache.py` minimally, not the test.

- [ ] **Step 4: Tests PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_answer_cache.py backend/app/core/answer_cache.py
git commit -m "test: cover the existing dual-layer answer cache module"
```

---

### Task 3: Unconditional boot warm-load — Postgres → Redis, every process start

**Why on top of the existing TTL/backfill logic:** `answer_cache.py` already keeps Redis
warm reactively (a `store_answer_cache` write hits Redis immediately; a Postgres L2 hit
backfills Redis). That alone already guarantees zero LLM calls for anything already in
Postgres — but it only warms *the questions someone happens to ask this session*, on a
first-ask latency cost (one embed + one Postgres cosine query). Given the interviewer's
Redis instance's real lifecycle — live for a few minutes per interview, idle for days or
weeks between them, `CACHE_REDIS_TTL_SECONDS` (7 days) can itself expire keys in a long
gap — this task adds an **eager, unconditional, total** load of everything already in
Postgres into Redis at every process boot, so the very first request of an interview is
already a Redis hit for every pre-baked question, not just the second time each one is
asked.

**Files:**
- Create: `backend/app/core/cache_warmup.py`
- Modify: `backend/app/main.py` (existing `@app.on_event("startup")` hook at line ~39)
- Test: `backend/tests/test_cache_warmup.py`

**Interfaces:**
- Produces: `warm_all_caches(conn) -> dict` — reads **every** row of `answer_cache` (no
  `LIMIT`, no recency filter — this loads all of it, not a sample) and calls the same
  Redis-write path `answer_cache.py` already uses on a live store (reuse the existing
  private setter rather than reimplementing key construction — see Step 3), so a
  warm-loaded row is indistinguishable from a freshly-stored one to a later lookup.
  Returns `{"ask_rows_loaded": int, "diff_rows_loaded": int}` — `diff_rows_loaded` is
  stubbed at `0` in this task. The `diff_cache` table already exists by this point (Task
  1 created it) but holds no rows yet, and more importantly `diff_cache.py` — which owns
  the Redis-write path for a diff row, per Task 6 — doesn't exist yet either; do not read
  `diff_cache` here or duplicate its future key format. Task 6 comes back and extends this
  function once its module exists. Never raises — a Redis outage at boot must not prevent
  the app from serving traffic on Postgres alone.

- [ ] **Step 1: Failing tests**

```python
def test_warm_all_caches_loads_every_answer_cache_row(conn, redis_conn):
    # insert several answer_cache rows directly (more than one -- the point is "every
    # row", not "the first row"), call warm_all_caches(conn), then confirm each one is
    # retrievable via lookup_answer_cache(conn, question_raw, query_vec=None, ...)
    # (i.e. a Redis-only hit, no embedding needed) and the returned count matches
    ...

def test_warm_all_caches_survives_redis_unavailable(conn, monkeypatch):
    # point REDIS_URL at nothing / force get-redis-equivalent to fail; warm_all_caches
    # must return without raising
    ...
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement `cache_warmup.py`**

Import `answer_cache.py`'s internals needed to write a row into Redis under its real key
(reuse rather than reimplement — if the existing module doesn't expose its Redis setter
publicly, add a minimal public wrapper to `answer_cache.py` rather than duplicating the
key-hashing logic here). `SELECT id, question_raw, superseded_filter, authority_filter,
result_json FROM answer_cache` (unfiltered), one Redis write per row. Wrap the whole
function body in `try/except` at the top level in addition to any inner best-effort
guards already present in the reused setter, so a total Redis outage degrades to "loaded
0 into Redis" rather than a startup crash. Log the counts.

- [ ] **Step 4: Wire into `app/main.py`'s existing startup hook**

`_run_schema_migrations` (line ~39) already runs at startup with a DB connection
available — call `warm_all_caches(conn)` from there (or immediately after, same event
handler) and log `f"Warmed cache: {result['ask_rows_loaded']} ask rows, "
f"{result['diff_rows_loaded']} diff rows"` so it's visible in Render's boot logs. This
call is unconditional — no "only if Redis looks empty" check, since that check can't be
trusted given the bursty Redis lifecycle described above.

- [ ] **Step 5: Tests PASS**

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/cache_warmup.py backend/app/main.py backend/tests/test_cache_warmup.py
git commit -m "feat: unconditional boot warm-load of Postgres cache rows into Redis"
```

---

### Task 4: Wire the existing cache module into retrieval (skip LLM on hit)

**Files:**
- Modify: `backend/app/core/retrieval.py` (already imports `decorate_cached_result,
  lookup_answer_cache, store_answer_cache` at line 14 — nothing calls them yet; this task
  is that wiring)
- Test: `backend/tests/test_answer_cache.py` (integration with mocked `generate_answer` /
  chat, or spy)

**Interfaces:**
- Consumes: Task 2's `lookup_answer_cache` / `store_answer_cache` / `decorate_cached_result`
  (already imported); existing `embed()`; existing `normalize_question` from
  `answer_cache.py` if needed for the LangSmith metadata
- Produces: result dict may include `cache_hit: bool`, `cache_layer: "redis" | "postgres" |
  None`, `cache_similarity: float | None`, `cache_match_mode: str | None` — these are
  exactly the fields `decorate_cached_result` already attaches, so this task's job is
  calling it at the right points, not inventing the shape.

- [ ] **Step 1: Confirm `CACHE_HIT_THRESHOLD`** — already imported into `retrieval.py` from
`config.py` (line ~16, currently re-exported as a same-named local alias); leave as-is
unless it collides with something, in which case simplify to just using
`config.CACHE_HIT_THRESHOLD` directly at call sites.

- [ ] **Step 2: Add `_flag_cache_event(...)` parallel to `_flag_low_confidence`**

```python
def _flag_cache_event(
    *,
    hit: bool,
    layer: str | None = None,       # "redis" | "postgres" | None
    match_mode: str | None = None,
    similarity: float | None = None,
    superseded_filter: bool,
    authority_filter: str | None,
    matched_question: str | None = None,
) -> None:
    run = get_current_run_tree()
    if not run:
        return
    run.add_tags(["cache-hit" if hit else "cache-miss"])
    run.add_metadata({
        "cache_hit": hit,
        "cache_layer": layer,
        "cache_match_mode": match_mode,  # answer_cache.MATCH_EXACT | MATCH_SEMANTIC | null
        "cache_similarity": similarity,
        "cache_superseded_filter": superseded_filter,
        "cache_authority_filter": authority_filter,
        "cache_matched_question": matched_question,
    })
```

LangSmith filter tip: tag `cache-hit` vs `cache-miss`; open metadata to see layer + mode + filters + similarity.

- [ ] **Step 3: Gate both `answer_question` and `answer_question_stream`**

Pseudocode order (stream) — note this calls `lookup_answer_cache` **twice** in the miss
path by design, once cheaply (Redis-only) and once fully (with the embedding), matching
the function's existing contract:

1. `_name_run_by_model(...)`
2. If `history`: treat as cache-ineligible → `_flag_cache_event(hit=False, ...)` with metadata `cache_skipped_reason: "history"` optional; fall through to existing pipeline.
3. Else: `yield {"step": "checking_cache"}`
4. **Redis-only probe first — no embedding call.** `hit = lookup_answer_cache(conn, question, None, superseded_filter, authority_filter)`. This is deliberately the fastest possible path (zero OpenAI calls) since it's the path every pre-baked demo question should take.
5. If `hit`:
   - `_flag_cache_event(hit=True, layer=hit["cache_layer"], match_mode=hit["match_mode"], similarity=hit["similarity"], matched_question=hit["matched_question"], ...)`
   - `result = decorate_cached_result(hit, _current_run_id())`
   - `yield {"step": "cache_hit", "detail": "exact"}`
   - `yield {"step": "done", "result": result}`
   - **return** (no embedding, no semantic_search, no LLM)
6. Else: `query_vec = embed(question)`, `hit = lookup_answer_cache(conn, question, query_vec, superseded_filter, authority_filter)` — this re-checks Redis (harmless, already established `None` above unless something changed concurrently) and, on a Redis miss, checks Postgres L2 and backfills Redis itself if it hits.
7. If `hit` (now `hit["cache_layer"] == "postgres"` in practice):
   - `_flag_cache_event(hit=True, layer="postgres", match_mode=hit["match_mode"], similarity=hit["similarity"], ...)`
   - `result = decorate_cached_result(hit, _current_run_id())`
   - `yield {"step": "cache_hit", "detail": f"{hit['similarity']:.3f}"}`
   - `yield {"step": "done", "result": result}`
   - **return** (no semantic_search, no LLM)
8. Else:
   - `_flag_cache_event(hit=False, ...)`
   - `yield {"step": "cache_miss"}` (optional but useful for UI/LangSmith parity)
   - continue existing steps; **reuse** `query_vec` already computed (do not double-embed)
9. After successful non-abstain answer: `store_answer_cache(conn, question, query_vec, superseded_filter, authority_filter, result)` (best-effort inside the existing function; wrap the call site too so a raised exception here never fails the answer, same spirit as `enrich_result`)

Mirror the same gate in non-streaming `answer_question` without yields.

- [ ] **Step 4: Test** — mock/spy `generate_answer` / OpenAI chat must **not** be called on a Redis hit or a Postgres hit; must be called on a full miss. Filter mismatch must call the LLM path. A Postgres hit must result in the question being retrievable via a Redis-only probe afterward (confirms the existing backfill actually ran).

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/retrieval.py backend/tests/test_answer_cache.py
git commit -m "feat: short-circuit ask pipeline on Redis or Postgres cache hit"
```

---

### Task 5: Frontend — types, trace labels, tiny cache-hit cue

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/components/ThinkingSteps.tsx`
- Modify: `frontend/src/components/AssistantMessage.tsx` (or adjacent small component)
- Optional CSS in existing stylesheet / tokens

**Interfaces:**
- Consumes: SSE `cache_hit` / `cache_miss` / `checking_cache`; `AskResponse` fields
- Produces: visible tiny log

- [ ] **Step 1: Extend types**

```ts
// on Answered (and optionally Abstained if ever cached later):
cache_hit?: boolean;
cache_layer?: "redis" | "postgres" | null;
cache_similarity?: number | null;
cache_match_mode?: "exact_key_plus_filters" | "query_plus_filters" | null;
```

- [ ] **Step 2: ThinkingSteps labels**

```ts
checking_cache: () => "Checking answer cache",
cache_hit: (s) => `Cache hit (${s.detail ?? "cached"})`,
cache_miss: () => "Cache miss — retrieving",
```

- [ ] **Step 3: Persistent tiny footer on finished assistant message**

When `message.response` is answered and `cache_hit === true`, render a single muted line under the answer / under thinking steps, e.g. `Served from cache` — use existing secondary text color tokens, no card, no badge cluster, and do not surface `cache_layer` in the UI text (it's plumbing for LangSmith/debugging, not a user-facing distinction — keeps this tiny per Global Constraints).

- [ ] **Step 4: Manual check** — `npm run lint` / `npx tsc --noEmit` in `frontend/`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/api.ts frontend/src/components/ThinkingSteps.tsx frontend/src/components/AssistantMessage.tsx
git commit -m "feat: show cache hit in reasoning trace and answer footer"
```

---

### Task 6: Diff-followup caching (`/diff-followup`)

**Files:**
- Create: `backend/app/core/diff_cache.py`
- Modify: `backend/app/api/routers/diff.py`
- Modify: `backend/app/core/cache_warmup.py` (Task 3) — extend `warm_all_caches` to also load `diff_cache` rows now that this table and module exist; Task 3 shipped only the `answer_cache` half since `diff_cache` didn't exist yet at that point
- Test: `backend/tests/test_diff_cache.py`, extend `backend/tests/test_cache_warmup.py`

**Interfaces:**
- Consumes: Task 1's `diff_cache` table; `answer_cache.py`'s `normalize_question` (import it — do not fork the normalization logic into a second copy)
- Produces:
  - `lookup_diff_cache(conn, current_document_id: int, previous_document_id: int, question: str) -> dict | None` — exact match only (`question_normalized` + both document ids), returns `{"result": ..., "question_raw": ...}` or `None`
  - `store_diff_cache(conn, current_document_id: int, previous_document_id: int, question: str, result: dict) -> None`

Build this as **the same shape of module as the existing `answer_cache.py`** (see "Pre-existing code" above) — a lazy module-scoped Redis client with connect-failure caching, a local key function, best-effort get/set wrapped in `try/except`, Postgres as the backing store. Mirroring `answer_cache.py`'s private `_get_redis()` pattern here (rather than factoring it into a shared module for one function) matches this codebase's existing one-file-per-cache convention and avoids refactoring already-shipped, now-tested code from Task 2 just to save a few duplicated lines.

- [ ] **Step 1: Failing tests**

```python
def test_diff_lookup_hit_same_docs_and_question(conn, redis_conn):
    # store one row; look up with same (current_id, previous_id, normalized question) → hit
    ...

def test_diff_lookup_miss_different_question(conn):
    # same doc ids, different question text → miss
    ...

def test_diff_followup_route_skips_llm_on_cache_hit(client, monkeypatch):
    # spy/mock chat_completion; first call misses and calls it; second identical call hits and does not
    ...
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement `diff_cache.py`**

`INSERT ... ON CONFLICT (current_document_id, previous_document_id, question_normalized) DO UPDATE` using the `UNIQUE` constraint from Task 1 (or a plain `SELECT`-then-`INSERT`/`UPDATE` if simpler — either is fine at this scale). `store_diff_cache` writes Postgres then Redis, both best-effort, mirroring `store_answer_cache`.

- [ ] **Step 4: Wire into `diff_followup` (`backend/app/api/routers/diff.py`)**

Order, right after resolving `prev` (so an "unavailable — no earlier version" response is never cached, matching the "only cache `available: true`" decision):

1. `hit = lookup_diff_cache(conn, req.current_document_id, prev["id"], req.question)` — this module's own lookup already checks Redis first (exact key) then Postgres (exact match) and backfills Redis on a Postgres hit, mirroring `lookup_answer_cache`'s two-layer behavior.
2. If `hit`: return `hit["result"]` directly — no `load_full_document_text`, no `chat_completion` call at all.
3. Else: run the existing full pipeline (`load_full_document_text` ×2, `chat_completion`), then on success `store_diff_cache(...)` (best-effort, writes both layers).

Do **not** call `increment_daily_usage` on a cache hit — that counter tracks OpenAI calls, and a cache hit makes none. Move the cache check before the existing `increment_daily_usage(conn)` call.

- [ ] **Step 5: Extend Task 3's `warm_all_caches`** to also `SELECT` every `diff_cache` row and write it into Redis via this module's setter, updating the returned `diff_rows_loaded` count (Task 3 stubbed this at `0` since the table didn't exist yet). Add a test to `test_cache_warmup.py` covering a `diff_cache` row surviving warm-load the same way the `answer_cache` one already does.

- [ ] **Step 6: Tests PASS**

- [ ] **Step 7: Commit**

```bash
git add backend/app/core/diff_cache.py backend/app/api/routers/diff.py backend/app/core/cache_warmup.py backend/tests/test_diff_cache.py backend/tests/test_cache_warmup.py
git commit -m "feat: cache diff-followup responses and warm-load them at boot too"
```

---

### Task 7: Seed script — pre-bake the full demo question set into Postgres

**Why this task exists:** the interview's realistic question universe is small and knowable
in advance (on the order of ten phrasings, per the Amendment). Rather than relying on the
*first* live ask of each one being slow, this script runs the real pipeline for every
question once, ahead of time, so the very first request in a live interview is already a
cache hit — Postgres gets "padded with everything," and the next boot's warm-load (Task 3)
carries all of it into Redis automatically.

**Files:**
- Create: `backend/scripts/seed_answer_cache.py`
- Create: `backend/scripts/demo_questions.json` (config the human maintains, not hardcoded logic)

**Interfaces:**
- Consumes: `answer_question()`, `store_answer_cache()` (Task 2), the `diff_followup` logic path or `store_diff_cache()` directly (Task 6)
- Produces: a CLI script, run manually and deliberately (never in CI, never on deploy — it makes real, billed LLM calls)

- [ ] **Step 1: Define the question-set config format**

`demo_questions.json` — a **flat list, not capped to any specific count**; add real entries
for however many question variations the demo actually uses (the plan does not know this
project's exact demo script, and should not guess or truncate it — this is a config file
for the human to fill in with their real list, on the order of ten entries, not fewer by
default):

```json
[
  {
    "question": "What happens if a healthcare professional's license application is rejected?",
    "superseded_filter": true,
    "authority_filter": null,
    "diff_followup": true
  }
]
```

`diff_followup: true` means: after seeding the `/ask` answer, also resolve its cited
document's previous version (reusing `find_previous_version`) and seed a `diff_cache` row
for the same question against that doc pair — so "compare with last year" is pre-baked too,
not just the initial answer. Seed the file with the one real question already used in
`cli/demo.py` (`QUESTION` constant) as a working example; the file should be trivially easy
to extend with the rest of the real demo list before running the script.

- [ ] **Step 2: Implement `seed_answer_cache.py`**

For each entry: call `answer_question(conn, question, superseded_filter=..., authority_filter=...)` for real (this is the one place in the whole feature where a cache-path test being "slow" is correct — it's supposed to actually run the pipeline). On a non-abstained result, `store_answer_cache(...)`. If `diff_followup` is true and the result wasn't abstained, resolve the previous version via `find_previous_version` and run the same explanation-generation logic `diff_followup` uses (import and call it directly, or factor its core into a plain function `diff.py` can share — implementer's judgment, whichever avoids duplicating the prompt text) to get an `available: true` result, then `store_diff_cache(...)`.

Print a summary: how many questions seeded, how many diff follow-ups seeded, how many were skipped (abstained, or no previous version to diff against).

Note: `store_answer_cache` / `store_diff_cache` already write Redis immediately as part of a normal store (see "Pre-existing code"), so Redis is warm right after this script runs even before the next reboot — the boot warm-load (Task 3) exists for the case Redis gets wiped or replaced *between* now and the actual interview, not as the only way data reaches it. No extra call needed here.

- [ ] **Step 3: Run it**

```bash
cd backend && python -m scripts.seed_answer_cache
```

Confirm row counts in `answer_cache` / `diff_cache` match the config file's entries (minus any abstained/no-previous-version skips).

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/seed_answer_cache.py backend/scripts/demo_questions.json
git commit -m "feat: seed script to pre-bake the demo question set into the answer/diff cache"
```

---

### Task 8: Local verification + deploy notes

**Files:**
- Optionally update `RUN.md` with 5–10 lines on cache + `REDIS_URL` / `DATABASE_URL` persistence (only if you want ops discoverability; keep short)

- [ ] **Step 1: Local** — `docker compose up` (brings up the new `redis` service too), ask the same question twice with same filters; second response shows cache hit, faster, no generation step; change authority filter → miss. Click "compare with last year" twice on the same question → second call shows no generation delay (check server logs / LangSmith for absence of a `chat_completion` call — the diff UI itself has no cache-hit cue per this plan, see Out of scope).

- [ ] **Step 2: LangSmith** — confirm `/ask` run tags `cache-hit` / `cache-miss` and metadata `cache_layer` + `cache_match_mode` (`exact_key_plus_filters` on Redis hit, `query_plus_filters` on Postgres hit).

- [ ] **Step 3: Production DB** — If Render `DATABASE_URL` already points at durable Postgres with pgvector, no host move. If DB is wiped when the service sleeps or is missing after sleep, create a **Supabase** (or Neon) project, enable `vector`, run app once so `SCHEMA_SQL` applies (or run migrations), set Render `DATABASE_URL`, redeploy. Re-ingest corpus if this is a new empty DB (`ingestion/*` order in CLAUDE.md).

- [ ] **Step 4: Production Redis (Amendment)** — Create a free Upstash Redis database, set Render env `REDIS_URL` to its connection string, redeploy. Update the now-outdated comment in `docker-compose.yml`'s `redis` service block (currently says *"do NOT run Redis on the sleeping web dyno"*, written under the pre-Amendment plan) to reflect that Redis now does run in prod, on Upstash, per the Amendment. Confirm via Render logs that the startup warm-load line from Task 3 (`Warmed cache: N ask rows, M diff rows`) appears on boot.

- [ ] **Step 5: Run the seed script (Task 7) against the production database** — either locally with `DATABASE_URL` pointed at the prod Postgres, or via a one-off Render shell command. Then redeploy (or just wait for the next natural boot) so the warm-load picks up the seeded rows. Ask a pre-baked demo question against the live Render URL and confirm the response is fast with `cache_hit: true` — this is the actual interview-facing payoff, verify it against the real deployment, not just locally.

- [ ] **Step 6: Commit docs only if `RUN.md` changed**

```bash
git add RUN.md
git commit -m "docs: note Redis + durable Postgres for answer/diff cache across Render sleep"
```

---

## Out of scope (v1)

- Caching abstentions (`/ask`) and "unavailable" diff results
- Caching when `history` is non-empty
- Cache invalidation on corpus re-ingest (manual `TRUNCATE answer_cache, diff_cache` or future hook in `rechunk.py`) — re-run Task 7's seed script after a re-ingest if the seeded answers would now cite stale content
- Per-model cache partitioning
- MongoDB
- Moving the FastAPI process off Render
- Surfacing a "served from cache" UI cue on the diff-followup panel itself (Task 6 only removes the LLM call and latency; Task 5's visible cue is `/ask`-only per the plan's "tiny, not a new dashboard" constraint — revisit only if the interview demo shows the missing cue is confusing)
- Automatically re-running the seed script in CI/CD (it makes real LLM calls; stays a manual, deliberate step)

## Self-review (plan vs decisions)

| Requirement | Task |
| --- | --- |
| Semantic hit then present result | 2, 4 |
| Redis required in prod, unconditional boot warm-load from Postgres | 3 |
| Tiny UI log cache hit | 5 |
| Append on miss (both layers, both endpoints) | 4 store path, 6 store path |
| No LLM on hit (`/ask` and `/diff-followup`) | 4 early return, 6 early return |
| Persist across Render sleep | Postgres external (8 step 3); Redis on Upstash (8 step 4); boot warm-load (3) is the belt-and-suspenders |
| LangSmith hit + layer + mode | 4 `_flag_cache_event` |
| Diff-followup cached | 1 (`diff_cache` table), 6 |
| Postgres pre-baked with the full demo question set ("everything," not organically grown) | 7 |
| Local first | 8 step 1 |
