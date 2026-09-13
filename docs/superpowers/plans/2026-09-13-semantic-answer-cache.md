# Semantic Answer Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On each ask, check a durable semantic cache of prior Q&A; on hit return the stored answer with zero LLM/chat calls and show a tiny UI “cache hit” cue; on miss run the normal pipeline and store the result. Persist across Render free-tier sleep.

**Architecture:** Store cached answers in the existing Postgres+pgvector database (new `answer_cache` table). Lookup embeds the question once, then cosine-searches prior query embeddings filtered by exact `superseded_filter` + `authority_filter`. Hit → short-circuit before retrieval and `generate_answer` / streaming LLM. Miss → full pipeline, then insert. LangSmith tags/metadata record hit vs miss and the match mode. Frontend shows a bottom-of-trace “Cache hit” line from a new SSE step + `cache_hit` on the result.

**Tech Stack:** Postgres + pgvector (already in repo), OpenAI `text-embedding-3-small` (already used), FastAPI SSE `/ask-stream`, React `ThinkingSteps` / `AssistantMessage`, LangSmith `@traceable` + `get_current_run_tree()`.

**Spec:** Decisions locked in this plan (no separate design doc). See “Locked decisions” and “Why not Redis/Mongo” below.

## Locked decisions (updated)

| Topic | Decision |
| --- | --- |
| Cache key / hit scope | **Query + all ask filters that are not LLM choice:** `superseded_filter` + `authority_filter`. **Provider/model never part of the key.** |
| LLM on hit | **No chat/LLM call.** Embedding still runs when checking Postgres semantic L2 (or after Redis exact miss). |
| Dual store | **L1 Redis (exact key)** → **L2 Postgres/Supabase (`answer_cache` semantic)**. Miss both → answer → **write both**. Postgres hit → **backfill Redis**. |
| Redis persistence | Sleeping the **Render web** does **not** wipe Redis **if** Redis is a **separate** managed service (Upstash / paid Render Redis) with persistence. Redis **is** empty after restart if it lived on the ephemeral free dyno or had no volume/AOF. For the demo: local Redis with a Docker volume; prod `REDIS_URL` → Upstash (or omit Redis and L2-only still works). **Postgres is always the durable source of truth.** |
| History | **Only cache when `history` is empty/None.** |
| What to store | Successful **answered** payloads only (not abstentions) for v1. |
| Similarity floor | **0.92** cosine on Postgres L2 (`CACHE_HIT_THRESHOLD`). |
| LangSmith | Tags `cache-hit` / `cache-miss`; metadata: `cache_layer` (`redis` \| `postgres` \| null), `cache_match_mode` (`exact_key_plus_filters` \| `query_plus_filters`), similarity, filters. |

### Redis vs sleep (plain English)

| Setup | Web sleeps 15m | Data still there? |
| --- | --- | --- |
| Redis inside the free web container / no volume | Dyno gone | **No — reset** |
| Docker Redis + named volume (local) | n/a | **Yes** across `compose down/up` if volume kept |
| Upstash / managed Redis + `REDIS_URL` | Web sleeps; Redis stays up | **Yes** |
| Postgres on Supabase (`answer_cache`) | Web sleeps | **Yes — always the backup** |

So: dual cache is proper — Redis for fast exact hits; Supabase/Postgres so nothing important depends on Redis surviving.

## Global Constraints

- Dual cache: Redis L1 (optional via `REDIS_URL`) + Postgres L2 (required durable store). No MongoDB.
- Postgres must survive Render free web sleep; Redis may be empty after a bad boot — L2 backfills L1.
- On cache hit: zero `chat.completions` / `generate_answer` / NaraRouter calls.
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
| `backend/app/core/db.py` | `answer_cache` table DDL in `SCHEMA_SQL` |
| `backend/app/core/answer_cache.py` | lookup / store helpers (cosine + filter match) |
| `backend/app/core/retrieval.py` | gate at start of `answer_question` / `answer_question_stream`; LangSmith flags; store on miss |
| `backend/app/core/config.py` | optional `CACHE_HIT_THRESHOLD` / enable flag if needed |
| `backend/tests/test_answer_cache.py` | hit/miss/filter mismatch/history skip |
| `frontend/src/types/api.ts` | `cache_hit`, `cache_similarity`, stream step types |
| `frontend/src/components/ThinkingSteps.tsx` | label for `cache_hit` / `cache_miss` steps |
| `frontend/src/components/AssistantMessage.tsx` (or tiny footer) | persistent “Served from cache” when `response.cache_hit` |
| `frontend/src/index.css` / tokens | minimal style for the cue (reuse brass/ink tokens) |

---

### Task 1: Schema — `answer_cache` table

**Files:**
- Modify: `backend/app/core/db.py` (`SCHEMA_SQL`)
- Test: `backend/tests/test_answer_cache.py` (schema presence via existing conftest DB)

**Interfaces:**
- Produces: table `answer_cache` usable by Task 2

- [ ] **Step 1: Write failing test that `answer_cache` exists**

```python
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

- [ ] **Step 2: Run test — expect FAIL**

Run: `cd backend && pytest tests/test_answer_cache.py::test_answer_cache_table_exists -v`

- [ ] **Step 3: Add DDL to `SCHEMA_SQL`**

Append (idempotent):

```sql
CREATE TABLE IF NOT EXISTS answer_cache (
    id SERIAL PRIMARY KEY,
    question_normalized TEXT NOT NULL,
    question_raw TEXT NOT NULL,
    superseded_filter BOOLEAN NOT NULL,
    authority_filter TEXT,  -- NULL means "no authority filter"
    query_embedding vector(1536) NOT NULL,
    result_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    hit_count INTEGER NOT NULL DEFAULT 0,
    last_hit_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS answer_cache_filters_idx
    ON answer_cache (superseded_filter, authority_filter);
```

Normalize questions as: `question.strip().lower()` (document in helper docstring). No HNSW required at demo scale (same rationale as `chunks`).

- [ ] **Step 4: Ensure tests apply schema (reuse `conftest` init)** — run test PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/db.py backend/tests/test_answer_cache.py
git commit -m "feat: add answer_cache table for semantic Q&A cache"
```

---

### Task 2: Cache lookup / store module

**Files:**
- Create: `backend/app/core/answer_cache.py`
- Test: `backend/tests/test_answer_cache.py`

**Interfaces:**
- Consumes: `conn`, query embedding `list[float]`, filters, result dict
- Produces:
  - `normalize_question(q: str) -> str`
  - `lookup_answer_cache(conn, question: str, query_vec, superseded_filter: bool, authority_filter: str | None, threshold: float) -> dict | None`
    - Returns `{"id", "similarity", "result", "question_raw", "match_mode": "query_plus_filters"}` or `None`
  - `store_answer_cache(conn, question: str, query_vec, superseded_filter: bool, authority_filter: str | None, result: dict) -> int`
  - `CACHE_MATCH_MODE = "query_plus_filters"` constant for LangSmith

- [ ] **Step 1: Failing tests**

```python
def test_lookup_hit_same_filters(conn, embed_fn):
    # insert one row with known embedding; lookup near-duplicate question + same filters → hit
    ...

def test_lookup_miss_different_authority_filter(conn, embed_fn):
    # same embedding/question, authority_filter "DHA" vs "DoH" → miss
    ...

def test_lookup_miss_below_threshold(conn):
    # dissimilar vector → None
    ...
```

Use a fixed fake unit vector in tests when possible to avoid OpenAI calls (construct `vector` literals / pass precomputed lists). Prefer not calling live OpenAI in unit tests.

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement `answer_cache.py`**

Lookup SQL sketch:

```sql
SELECT id, question_raw, result_json,
       1 - (query_embedding <=> %s::vector) AS similarity
FROM answer_cache
WHERE superseded_filter = %s
  AND authority_filter IS NOT DISTINCT FROM %s
ORDER BY query_embedding <=> %s::vector
LIMIT 1
```

Accept only if `similarity >= threshold`. On hit, bump `hit_count` / `last_hit_at`.

`store_answer_cache`: strip ephemeral fields from `result` before JSON if needed (`run_id` can stay or be nulled — prefer store without a stale `run_id`; new run gets fresh id). Strip any `cache_hit` keys. Do not store huge unnecessary blobs beyond what `answer_question` already returns.

- [ ] **Step 4: Tests PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/answer_cache.py backend/tests/test_answer_cache.py
git commit -m "feat: semantic answer cache lookup and store helpers"
```

---

### Task 3: Wire cache into retrieval (skip LLM on hit)

**Files:**
- Modify: `backend/app/core/retrieval.py`
- Test: `backend/tests/test_answer_cache.py` (integration with mocked generate / or spy)

**Interfaces:**
- Consumes: Task 2 helpers; existing `embed()`
- Produces: result dict may include `cache_hit: bool`, `cache_similarity: float | None`, `cache_match_mode: str | None`

- [ ] **Step 1: Add `CACHE_HIT_THRESHOLD = 0.92` near `CONFIDENCE_*`**

- [ ] **Step 2: Add `_flag_cache_event(...)` parallel to `_flag_low_confidence`**

```python
def _flag_cache_event(
    *,
    hit: bool,
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
        "cache_match_mode": match_mode,  # "query_plus_filters" on hit; null on miss
        "cache_similarity": similarity,
        "cache_superseded_filter": superseded_filter,
        "cache_authority_filter": authority_filter,
        "cache_matched_question": matched_question,
    })
```

LangSmith filter tip: tag `cache-hit` vs `cache-miss`; open metadata to see mode + filters + similarity.

- [ ] **Step 3: Gate both `answer_question` and `answer_question_stream`**

Pseudocode order (stream):

1. `_name_run_by_model(...)`
2. If `history`: treat as cache-ineligible → `_flag_cache_event(hit=False, ...)` with metadata `cache_skipped_reason: "history"` optional; fall through to existing pipeline (still tag miss/skip).
3. Else: `yield {"step": "checking_cache"}` then `query_vec = embed(question)`
4. `hit = lookup_answer_cache(...)`
5. If hit:
   - `_flag_cache_event(hit=True, match_mode="query_plus_filters", ...)`
   - `result = {**hit["result"], "cache_hit": True, "cache_similarity": hit["similarity"], "cache_match_mode": "query_plus_filters", "run_id": _current_run_id()}`
   - `yield {"step": "cache_hit", "detail": f"{hit['similarity']:.3f}"}`
   - `yield {"step": "done", "result": result}`
   - **return** (no semantic_search, no LLM)
6. Else:
   - `_flag_cache_event(hit=False, ...)`
   - `yield {"step": "cache_miss"}` (optional but useful for UI/LangSmith parity)
   - continue existing steps; **reuse** `query_vec` already computed (do not double-embed)
7. After successful non-abstain answer: `store_answer_cache(...)` (best-effort try/except so cache write never fails the answer — same spirit as `enrich_result`)

Mirror the same gate in non-streaming `answer_question` without yields.

- [ ] **Step 4: Test** — mock/spy `generate_answer` / OpenAI chat must **not** be called on hit; must be called on miss. Filter mismatch must call LLM path.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/retrieval.py backend/tests/test_answer_cache.py
git commit -m "feat: short-circuit ask pipeline on semantic cache hit"
```

---

### Task 4: Frontend — types, trace labels, tiny cache-hit cue

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
cache_similarity?: number | null;
cache_match_mode?: "query_plus_filters" | null;
```

- [ ] **Step 2: ThinkingSteps labels**

```ts
checking_cache: () => "Checking answer cache",
cache_hit: (s) => `Cache hit (${s.detail ?? "semantic"})`,
cache_miss: () => "Cache miss — retrieving",
```

- [ ] **Step 3: Persistent tiny footer on finished assistant message**

When `message.response` is answered and `cache_hit === true`, render a single muted line under the answer / under thinking steps, e.g. `Served from cache · query + filters` — use existing secondary text color tokens, no card, no badge cluster.

- [ ] **Step 4: Manual check** — `npm run lint` / `npx tsc --noEmit` in `frontend/`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/api.ts frontend/src/components/ThinkingSteps.tsx frontend/src/components/AssistantMessage.tsx
git commit -m "feat: show cache hit in reasoning trace and answer footer"
```

---

### Task 5: Local verification + deploy notes

**Files:**
- Optionally update `RUN.md` with 5–10 lines on cache + `DATABASE_URL` persistence (only if you want ops discoverability; keep short)

- [ ] **Step 1: Local** — `docker compose up` (or existing Postgres), ask the same question twice with same filters; second response shows cache hit, faster, no generation step; change authority filter → miss.

- [ ] **Step 2: LangSmith** — confirm run tags `cache-hit` / `cache-miss` and metadata `cache_match_mode=query_plus_filters`.

- [ ] **Step 3: Production DB** — If Render `DATABASE_URL` already points at durable Postgres with pgvector, no host move. If DB is wiped when the service sleeps or is missing after sleep, create a **Supabase** (or Neon) project, enable `vector`, run app once so `SCHEMA_SQL` applies (or run migrations), set Render `DATABASE_URL`, redeploy. Re-ingest corpus if this is a new empty DB (`ingestion/*` order in CLAUDE.md).

- [ ] **Step 4: Do not add Redis on Render for this feature.**

- [ ] **Step 5: Commit docs only if `RUN.md` changed**

```bash
git add RUN.md
git commit -m "docs: note durable Postgres for answer cache across Render sleep"
```

---

## Out of scope (v1)

- Caching abstentions
- Caching when `history` is non-empty
- Cache invalidation on corpus re-ingest (manual `TRUNCATE answer_cache` or future hook in `rechunk.py`)
- Per-model cache partitioning
- Redis / Mongo / Upstash
- Moving the FastAPI process off Render

## Self-review (plan vs decisions)

| Requirement | Task |
| --- | --- |
| Semantic hit then present result | 2, 3 |
| Tiny UI log cache hit | 4 |
| Append on miss | 3 store path |
| No LLM on hit | 3 early return |
| Persist across Render sleep | Postgres external; Task 5 |
| LangSmith hit + mode (query+filters) | 3 `_flag_cache_event` |
| Local first | Task 5 step 1 |
