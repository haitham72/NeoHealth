# ReguLense — Interview Mini-Guide

A one-page mental model, a 5-minute demo script, and the answers to the questions
you'll actually get asked. Everything here is measurable in the repo; numbers were
verified against the live database on 2026-09-13.

---

## 0. The 30-second pitch

> ReguLense is hybrid-retrieval Q&A over UAE health regulation (DHA / DoH / MOHAP).
> Standard RAG treats a superseded regulation as just another chunk — so it happily
> cites a 2022 licensing manual that's been dead for three years. ReguLense tracks
> regulation versions and jurisdictions, tiers its own confidence, and will not
> answer without a citation — it abstains rather than guess. It's live on Render,
> and every answer carries its document code, version, effective date, and
> a link to the exact highlighted passage in the source PDF.

**One-line architecture:** hybrid search (pgvector + Postgres FTS) → RRF → confidence
tiering → LLM guardrail → grounded generation, with two cache layers and a mined
follow-up-question system on top.

---

## 1. The sketch

```
   INGEST (offline)                         SERVE (every question)
   ────────────────                         ──────────────────────
   PDFs (DHA/DoH/MOHAP + research)
        │
        ▼  parse metadata from the document's own footer
   doc_code · version · effective_date · authority
        │
        ▼  supersession: newest effective_date per doc_code wins
   older versions → superseded = true
        │
        ▼  Docling HybridChunker (+ merge tiny runs, + semantic re-split)
   chunks + normalized bboxes + embeddings + tsvector
        │
        ▼
   Postgres + pgvector  ◄────────────────────────────┐
                                                     │
   question ──▶ embed (text-embedding-3-small)       │
        │                                            │
        ├─▶ cache L1: Redis exact key ───────────────┤ zero-LLM fast path
        ├─▶ cache L2: Postgres cosine ≥ 0.92 ────────┤ (paraphrase hits)
        │                                            │
        ▼                                            │
   hybrid search: pgvector cosine ‖ Postgres full-text
        │             (both filtered: in-force + authority)
        ▼
   Reciprocal Rank Fusion (k=60, 15 candidates each, top 7)
        │
        ▼
   confidence tier on top semantic score
        high ≥ 0.55 · medium ≥ 0.35 · low ≥ 0.15 · else ABSTAIN (free)
        │
        ▼
   guardrail (always gpt-4o-mini): question + top-3 excerpts
        ├─ OFF_TOPIC → fixed sentence + mined suggestions, no generation
        └─ RELEVANT → generate (gpt-4o-mini, NaraRouter fallback, or local LM Studio)
                        │
                        ▼
                  answer + citation + version ledger + source panel
                  + "Continue exploring" (mined questions, semantic match)
```

Repo map: `backend/app/core/retrieval.py` (the pipeline), `backend/app/core/guardrail.py`,
`backend/ingestion/` (five-step corpus build + mining), `frontend/src/` (React 19 chat UI).

---

## 2. The corpus and the pipeline, with real numbers

| Thing | Value |
|---|---|
| Documents | 40 PDFs total, **36 in force**, 4 superseded |
| Chunks | **860** structure-aware chunks (Docling) |
| Authorities | DHA, DoH Abu Dhabi, MOHAP, + 3 research papers |
| Embeddings | OpenAI `text-embedding-3-small`, 1536-dim, stored in pgvector |
| Lexical | Postgres `tsvector` (English + Arabic) |
| Fused candidates | RRF k=60, 15 per method, top 7 passed to the LLM |
| Confidence | high ≥ 0.55 · medium ≥ 0.35 · low ≥ 0.15 · **below 0.15 = abstain** |
| Semantic cache | Postgres cosine ≥ **0.92** (per-cache key includes filters, never provider) |
| Mined suggestions | **248** rows (33 docs mined, 3 flagged sensitive → skipped) |
| Tests | **116 backend** (pytest), **13 frontend** (vitest) |

---

## 3. Five design decisions that carry the project

**1. Metadata comes from the document's own footer — never the filename or URL.**
One corpus PDF lives at a URL that implies 2023; the document itself is Issue 4,
effective November 2025. Parsing the printed metadata is the only trustworthy source.
Documents that can't be parsed land in `needs_manual.json` and get hand-written entries.

**2. Supersession is a single boolean, resolved by effective_date.**
Newest `effective_date` per `doc_code` wins; older siblings are flagged
`superseded = true` and excluded from retrieval by default. The version ledger in the
UI shows *all* versions and marks which one was actually cited — and the toggle lets
you reproduce the naive-RAG failure side by side.

**3. Citation-or-abstain, with tiered confidence in between.**
Below 0.15: abstain, no LLM spend. High: answer normally. Medium/low: answer but with
an explicit `Certainty:` line. This came from real query sampling — off-topic
questions top out around 0.123 while genuine matches start higher, so 0.15 separates
them. There is no "answer anyway" path.

**4. The guardrail exists because embeddings lie.**
"What shoe size is Messi?" scored **0.23** — above the abstain floor, because "size"
embeds near "burn size estimation" plus cover-page template noise. No threshold fixes
that; only reading the text does. So a capped 4-line LLM verdict runs on the question
+ top-3 excerpts *before* the expensive generation call. Two hard-won corrections:
- It **always uses gpt-4o-mini**, even when the answer is generated locally. Measured
  live: local qwen 4B rejected a real DHA question ("one school nurse per 750
  students") that gpt-4o-mini accepted with identical retrieved evidence.
- Questions that exactly match a **pre-vetted mined suggestion skip the judge
  entirely** — they're corpus-grounded by construction, so clicking "Continue
  exploring" can never come back off-topic.
- Fail-open everywhere: judge exception / unparseable verdict → falls through to the
  numeric path, never blocks a real question.

**5. Cache the answer; recompute anything derived from growing tables.**
Two layers: Redis exact-key (L1) and Postgres semantic (L2, ≥0.92). Provider/model are
deliberately *not* in any cache key. The mistake that taught the rule: suggestions used
to be frozen into cached answers, so repeats served pre-crawl questions ("same questions
over and over"). Now `suggested_followups` is stripped before storage and re-attached at
serve time — on a cache hit the stored `query_embedding` is read from Postgres, so it's
still zero LLM calls. A light-red **"Remove from cache"** control evicts *only the
served entry*: the hit mints an HMAC-signed, ~1h token (`cache_evict.py`), and
`POST /cache/evict` deletes the Redis key plus its Postgres row (including the matched
`cache_id` on a semantic hit) — never a purge.

---

## 4. "Continue exploring" — offline mining, online semantic matching

The follow-up questions under an answer are not generated live:

1. **Mine offline** — one document per call, sourcing text from the exact DB chunks the
   loader will validate against (mining from the PDFs' pdfplumber text was the original
   bug: quotes valid there didn't exist in Docling's `chunks.text`). The miner
   self-verifies every `anchor_quote` and page before writing, retries once with the
   failing quote as feedback, caps at 1–2 questions per doc, and records terminal state
   in a resume file (finished docs are never re-mined; sensitive docs are skipped, not
   retried).
2. **Load** — `load_suggestions.py` rejects any line whose quote isn't verbatim in the
   document's chunks, resolves anchors to real `chunk_ids`, embeds, upserts idempotently.
3. **Serve** — cosine-match the already-computed `query_vec` against stored suggestion
   embeddings; same authority first; exclude every question already asked in the
   conversation (not just the current one — otherwise clicking rotates the same three
   forever). A similarity floor exists but defaults to **0**: the product always wants
   the 3 closest, no matter how weak. Off-topic answers use the same mined suggestions
   (the guardrail's own invented list is still in the payload for API compatibility but
   is no longer rendered — it once recommended a question the same guard then rejected).

---

## 5. Live failure stories you can tell

- **The superseded manual (`DHA/HRS/HLD/MA-2`)**: three versions on the public site —
  2022, May 2025, July 2025. Plain RAG cites the wrong one with full confidence. This
  is the demo that sells the project.
- **Messi (0.23)**: embeddings fail on playful off-topic; the guardrail catches it.
- **The pandemic suggestion**: the guardrail invented "how does DHA handle staff
  shortages during a pandemic", and when clicked, the same guard rejected it as
  off-topic. Fix: off-topic alternatives now come from the verified mined table, and
  recommend-ability is guaranteed.
- **The small model judge**: local qwen 4B rejected a legitimate school-nurse question;
  gpt-4o-mini accepted it with the same evidence — judges must be good.
- **The lost buffer**: killing a mining run lost a completed doc because the JSONL was
  buffered; state said done, file said empty. Fix: flush before recording state.
- **Single deployment**: Render builds the frontend into `backend/static` and serves
  same-origin; there is no second frontend host and no cross-origin path, so
  `ALLOWED_ORIGINS` only needs local dev origins.
  `render.yaml` isn't Blueprint-synced — verify dashboard config, don't trust the file.

---

## 6. Five-minute demo script

1. **Ask a licensing question** → point out the citation block (doc code, version,
   effective date, page), the version ledger, and the source panel showing which
   chunks cleared the confidence floor.
2. **Toggle "Exclude outdated regulations" off**, ask the same question → it now cites
   the superseded version. Side-by-side naive-vs-RegLense failure, in one click.
3. **Click a citation → View in PDF** → exact highlighted passage (bounding boxes
   stored at ingest, no client-side text matching).
4. **Ask "What shoe size is Messi?"** → abstains with the fixed sentence + 3 mined
   suggestions. Click one → it answers, because mined questions skip the guardrail.
5. **Show the cache note** → "Remove from cache" → the entry is gone (Redis + Postgres,
   that one entry only).
6. **"See what changed"** on a cited document that has a previous version → plain-language
   diff of the two full texts, scoped to the question.

---

## 7. Likely questions, crisp answers

**Why not fine-tune a model on the regulations?**
Regulations change and correctness is citation-bound. Fine-tuning gives no provenance,
no version tracking, and stale weights the moment a new version drops. Retrieval keeps
the answer inspectable and updatable.

**Why RRF instead of weighted score fusion?**
Cosine similarity and `ts_rank` aren't on comparable scales; any weighting is arbitrary
and corpus-dependent. RRF only uses rank positions, so it's robust without tuning.

**Why Postgres + pgvector instead of a dedicated vector DB?**
860 chunks — an ANN index is unnecessary (a sequential scan is fast). One database gives
me vector search, full-text search, relational filtering (supersession/authority), and
transactions in the same query plan, with zero extra ops.

**How do you prevent hallucination?**
Layered: retrieval grounded in real chunks → numeric abstain floor (free) → LLM guardrail
before generation → a prompt that forbids unsupported claims → mandatory citation →
`Certainty:` caveats at medium/low confidence. If sources don't support it, the answer
says so or the system abstains.

**How were the thresholds chosen?**
Real query sampling, not guesses. Off-topic questions physically maxed out at 0.123
(playful questions score surprisingly high due to template noise); genuine matches start
above. 0.15 sits between them. Same approach for the suggestion floor (in-scope 0.64–0.79
vs hard off-topic 0.58, so 0.62 separates if you want filtering).

**What's the hardest part of this domain?**
Version and jurisdiction correctness. The expensive failure isn't "I don't know" — it's
a confident answer grounded in a regulation that's out of force or from the wrong emirate.

**What about multi-turn conversations?**
History is passed as phrasing context but never used to select or cite chunks — the same
question + filters always retrieves the same facts, which is also why caching is safe.
It *is* used to exclude already-asked questions from suggestions. Multi-turn query
rewriting is the next step.

**What would you do with more time?**
An answerability gate for vague questions, an eval harness (golden Q/A set with
retrieval + faithfulness metrics), ANN index if the corpus grows, and re-mining the
3 sensitive docs through a compliant local path.

---

## 8. Repository pointers for the deep dive

| Topic | File |
|---|---|
| Full technical walkthrough, every decision + why | `ARCHITECTURE.md` |
| Working rules for AI-assisted development | `CLAUDE.md` |
| Corpus build (download → parse → load → chunk) | `backend/ingestion/` |
| Retrieval, tiering, guardrail wiring, caches | `backend/app/core/retrieval.py` |
| Relevance judge + off-topic contract | `backend/app/core/guardrail.py` |
| Mining prompt + worker protocol | `backend/ingestion/SUGGESTION_MINING_PROMPT.md` |
| Suggestion matching at serve time | `backend/app/services/suggestions.py` |
| Per-result cache eviction | `backend/app/core/cache_evict.py`, `backend/app/api/routers/cache.py` |
| Local run + env vars + troubleshooting | `RUN.md` |
