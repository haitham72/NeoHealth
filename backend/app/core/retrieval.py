"""
Hybrid retrieval (pgvector cosine + Postgres full-text) with RRF fusion, supersession
filtering, and mandatory-citation-or-abstain answering. Shared by ask.py (CLI) and
demo.py (naive-vs-ReguLense side-by-side).
"""
import os
import re
import time

from openai import OpenAI
from langsmith import get_current_run_tree, traceable
from langsmith.wrappers import wrap_openai

EMBED_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4o-mini"
RRF_K = 60  # RRF k parameter
CANDIDATES_PER_METHOD = 15  # 15 candidates per method (semantic + full-text)
TOP_N_FOR_ANSWER = 7  # Top chunks for answer that is passed to the LLM

# Confidence tiers on fused[0]'s semantic_score (cosine similarity, 0-1 -- see
# rrf_fuse()). Calibrated against 10 real queries run through embed()/semantic_search()
# directly (no LLM call): on-topic questions scored 0.40-0.83 (weakest real match was
# "Can a nurse practice independently without physician supervision?" at 0.402),
# off-topic controls ("What is the capital of France?", "Write me a poem about the
# ocean.") clustered tightly at 0.09-0.123. The old single ABSTAIN_THRESHOLD=0.35 sat
# uncomfortably close to that real 0.40 floor; these three bands are a reasoned first
# split of the observed on-topic range, not a validated operating curve -- revisit with
# a real eval harness (ARCHITECTURE.md's own stated caveat) rather than trusting them
# blindly as usage grows.
CONFIDENCE_HIGH = 0.55    # comfortably inside the observed on-topic range
CONFIDENCE_MEDIUM = 0.35  # captures weaker-but-real matches down to the observed floor
CONFIDENCE_LOW = 0.15     # abstain below this -- sits above the observed 0.123 off-topic ceiling

# Generation-only local model support: retrieval/embeddings always stay on OpenAI
# (the corpus is already embedded at 1536-dim; a local embedding model would need a
# different dimension and a full re-embed -- a separate, bigger task). Only the final
# answer-generation call can be routed to a local LM Studio model instead, via its
# OpenAI-compatible endpoint. Privacy tradeoff, stated plainly: the question text and
# retrieved excerpts still went to OpenAI once already, to compute the query embedding
# and find these chunks -- switching generation to local keeps the actual answer
# synthesis on-machine, it does not make retrieval itself local.
LOCAL_BASE_URL = "http://localhost:1234/v1"
DEFAULT_LOCAL_MODEL = "qwen/qwen3.5-9b"

# wrap_openai traces chat.completions.create() to LangSmith (project set via
# LANGSMITH_PROJECT env var) -- no LangChain needed, just this wrapper around the same
# client. It does NOT cover embeddings.create() (checked against the installed
# package's source -- that wrapper only patches the chat/completions endpoints), so
# embed() below is separately traced with @traceable. If LANGSMITH_TRACING isn't set,
# both are harmless passthroughs -- calls behave identically, nothing is sent anywhere.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
client = wrap_openai(OpenAI(api_key=OPENAI_API_KEY))
# LM Studio ignores the API key entirely but the SDK requires a non-empty string.
local_client = wrap_openai(OpenAI(base_url=LOCAL_BASE_URL, api_key="lm-studio"))

# NaraRouter as a chat-generation fallback only -- embeddings always stay on OpenAI (see
# LOCAL_BASE_URL comment above; NaraRouter has no embedding models). Not a primary/
# secondary split: OpenAI is used whenever it's healthy, this exists purely so an
# OpenAI outage or rate limit degrades to a different model instead of surfacing an
# error to the user.
NARAROUTER_API_KEY = os.environ.get("NARAROUTER_API_KEY", "")
NARAROUTER_MODEL = "laguna-s-2.1"
nararouter_client = (
    wrap_openai(OpenAI(api_key=NARAROUTER_API_KEY, base_url="https://router.bynara.id/v1"))
    if NARAROUTER_API_KEY else None
)

# An OpenAI chat failure is almost always an account-wide rate limit, not a one-off
# blip -- so every other in-flight question (this user's follow-ups, or anyone else's)
# would fail the exact same way for the next little while. A failure trips this cooldown
# so subsequent calls skip straight to NaraRouter instead of re-hitting an
# already-rate-limited OpenAI; the next call after it lapses tries OpenAI again to see
# if it has recovered.
OPENAI_COOLDOWN_SECONDS = 60
_openai_degraded_until = 0.0


def _openai_is_degraded() -> bool:
    return time.time() < _openai_degraded_until


def _mark_openai_degraded() -> None:
    global _openai_degraded_until
    _openai_degraded_until = time.time() + OPENAI_COOLDOWN_SECONDS


# Separate from the failure-triggered cooldown above: a single client hammering OpenAI
# with back-to-back questions burns through real spend even when OpenAI is perfectly
# healthy. After OPENAI_PER_IP_LIMIT calls from the same IP within the trailing
# OPENAI_PER_IP_WINDOW_SECONDS, that IP's further calls in the window route straight to
# NaraRouter instead -- a soft cap that degrades service rather than hard-blocking with
# a 429. Single uvicorn worker (see render.yaml), so plain in-memory state is safe --
# same reasoning as the connection pool in db.py.
OPENAI_PER_IP_LIMIT = 3
OPENAI_PER_IP_WINDOW_SECONDS = 60
_openai_calls_by_ip: dict[str, list[float]] = {}


def _openai_ip_limit_exceeded(client_ip: str) -> bool:
    now = time.time()
    recent = [t for t in _openai_calls_by_ip.get(client_ip, []) if now - t < OPENAI_PER_IP_WINDOW_SECONDS]
    _openai_calls_by_ip[client_ip] = recent
    return len(recent) >= OPENAI_PER_IP_LIMIT


def _record_openai_call(client_ip: str) -> None:
    _openai_calls_by_ip.setdefault(client_ip, []).append(time.time())


def chat_completion(messages: list[dict], client_ip: str | None = None, stream: bool = False):
    """OpenAI first, NaraRouter fallback on failure or per-IP overuse -- see the
    comments above. Shared by generate_answer() and the on-demand /diff-followup and
    /cross-check-regulation routers, which call this directly since they build their own
    one-off prompts rather than going through _build_messages(). Returns
    (response, active_model_actually_used)."""
    ip_limited = client_ip is not None and _openai_ip_limit_exceeded(client_ip)
    if not _openai_is_degraded() and not ip_limited:
        try:
            resp = client.chat.completions.create(model=CHAT_MODEL, messages=messages, temperature=0, stream=stream)
            if client_ip is not None:
                _record_openai_call(client_ip)
            return resp, CHAT_MODEL
        except Exception:
            _mark_openai_degraded()
    if nararouter_client is None:
        raise RuntimeError("OpenAI is unavailable and NARAROUTER_API_KEY is not configured -- no fallback available")
    resp = nararouter_client.chat.completions.create(model=NARAROUTER_MODEL, messages=messages, temperature=0, stream=stream)
    return resp, NARAROUTER_MODEL


def resolve_model(provider: str, model: str | None) -> str:
    """Shared by generate_answer() and the stream's trace event, so the reasoning-trace
    UI can show the model actually in use instead of a hardcoded name."""
    return model or (DEFAULT_LOCAL_MODEL if provider == "local" else CHAT_MODEL)


def _without_conn(inputs: dict) -> dict:
    """process_inputs hook: LangSmith stores multi-key inputs alphabetically
    (model, provider, question, superseded_filter) and its compact Input preview grabs
    the first non-null scalar from that order -- with model usually null, that grabbed
    provider ("openai") instead of the actual question. Logging only the question makes
    the Input unambiguous; provider/model are already visible via the run name
    (_name_run_by_model)."""
    return {"question": inputs.get("question")}


def _name_run_by_model(provider: str, model: str | None) -> None:
    """Renames the current trace from the static "answer_question"/"answer_question_
    stream" to the model that actually answered (e.g. "gpt-4o-mini" or "qwen/qwen3-4b-
    2507"). This is a low-cardinality label (a handful of possible models) glanceable in
    a run list -- unlike the per-query semantic title tried earlier and reverted, it
    doesn't restate Input's content, so it doesn't duplicate it."""
    run = get_current_run_tree()
    if run:
        run.name = resolve_model(provider, model)


def _flag_low_confidence(top_score: float) -> None:
    """Tags the current trace so low-confidence answers are filterable for review in
    the LangSmith dashboard -- the "notify us about the findings" side of confidence
    tiering, reusing the existing tracing infra instead of adding new DB persistence
    (out of scope per ARCHITECTURE.md)."""
    run = get_current_run_tree()
    if run:
        run.add_tags(["low-confidence"])
        run.add_metadata({"top_score": top_score})


def _current_run_id() -> str | None:
    """The LangSmith run id for the current answer_question()/answer_question_stream()
    trace, so the frontend can later attach user feedback to this exact run via
    POST /report-answer (see api.py). None when LANGSMITH_TRACING isn't set -- same
    harmless-no-op contract as the rest of the LangSmith integration; the frontend
    simply doesn't render a report control when run_id is missing."""
    run = get_current_run_tree()
    return str(run.id) if run else None


@traceable(run_type="tool", name="embed_query")
def embed(text: str) -> list[float]:
    return client.embeddings.create(model=EMBED_MODEL, input=[text]).data[0].embedding

#   Semantic search using pgvector cosine similarity
def semantic_search(conn, query_vec, superseded_filter: bool, k: int, authority_filter: str | None = None):
    clause = "AND d.superseded = false" if superseded_filter else ""
    params = [query_vec]
    if authority_filter:
        clause += " AND d.authority = %s"
        params.append(authority_filter)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT c.id, c.document_id, c.page, c.page_end, c.heading_path, c.bboxes, c.text,
                   1 - (c.embedding <=> %s::vector) AS score
            FROM chunks c JOIN documents d ON d.id = c.document_id
            WHERE true {clause}
            ORDER BY c.embedding <=> %s::vector
            LIMIT %s
            """,
            [*params, query_vec, k],
        )
        return cur.fetchall()  # (chunk_id, document_id, page, page_end, heading_path, bboxes, text, score)

#   word-based/Lexical search using Postgres full-text search
def lexical_search(conn, query: str, superseded_filter: bool, k: int, authority_filter: str | None = None):
    clause = "AND d.superseded = false" if superseded_filter else ""
    params = [query, query]
    if authority_filter:
        clause += " AND d.authority = %s"
        params.append(authority_filter)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT c.id, c.document_id, c.page, c.page_end, c.heading_path, c.bboxes, c.text,
                   ts_rank(c.tsv, websearch_to_tsquery('english', %s)) AS score
            FROM chunks c JOIN documents d ON d.id = c.document_id
            WHERE c.tsv @@ websearch_to_tsquery('english', %s) {clause}
            ORDER BY score DESC
            LIMIT %s
            """,
            [*params, k],
        )
        return cur.fetchall()


def rrf_fuse(semantic_results, lexical_results, top_n: int):
    """Reciprocal Rank Fusion: combine two ranked lists into one, keyed by chunk id.
    Keeps the semantic score alongside (used for the abstention gate)."""
    fused: dict[int, dict] = {}

    for rank, row in enumerate(semantic_results):
        chunk_id, document_id, page, page_end, heading_path, bboxes, text, sem_score = row
        fused[chunk_id] = {
            "chunk_id": chunk_id,
            "document_id": document_id,
            "page": page,
            "page_end": page_end,
            "heading_path": heading_path or [],
            "bboxes": bboxes or [],
            "text": text,
            "semantic_score": sem_score,
            "rrf": 1.0 / (RRF_K + rank + 1),
        }

    for rank, row in enumerate(lexical_results):
        chunk_id, document_id, page, page_end, heading_path, bboxes, text, _lex_score = row
        if chunk_id in fused:
            fused[chunk_id]["rrf"] += 1.0 / (RRF_K + rank + 1)
        else:
            fused[chunk_id] = {
                "chunk_id": chunk_id,
                "document_id": document_id,
                "page": page,
                "page_end": page_end,
                "heading_path": heading_path or [],
                "bboxes": bboxes or [],
                "text": text,
                "semantic_score": 0.0,  # didn't place in the semantic top-k
                "rrf": 1.0 / (RRF_K + rank + 1),
            }

    ranked = sorted(fused.values(), key=lambda r: r["rrf"], reverse=True)
    return ranked[:top_n]


def confidence_tier(score: float) -> str | None:
    """Buckets a semantic_score into "high"/"medium"/"low", or None below the abstain
    floor. Shared by both answer paths so the three constants only need reading once."""
    if score >= CONFIDENCE_HIGH:
        return "high"
    if score >= CONFIDENCE_MEDIUM:
        return "medium"
    if score >= CONFIDENCE_LOW:
        return "low"
    return None


def filter_weak_chunks(fused: list[dict]) -> list[dict]:
    """Drops chunks below CONFIDENCE_LOW from what's fed to the LLM -- the actual fix
    for force-feeding irrelevant excerpts into the prompt. Exempts semantic_score==0.0
    chunks (lexical-only hits, see rrf_fuse()): that's a "not scored by this method"
    sentinel, not a real low-relevance judgment, so it must never be treated as weak."""
    return [c for c in fused if c["semantic_score"] == 0.0 or c["semantic_score"] >= CONFIDENCE_LOW]


def get_document(conn, document_id: int) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, doc_code, version, effective_date, authority,
                   source_url, superseded, tier
            FROM documents WHERE id = %s
            """,
            (document_id,),
        )
        row = cur.fetchone()
    return {
        "id": row[0],
        "title": row[1],
        "doc_code": row[2],
        "version": row[3],
        "effective_date": row[4],
        "authority": row[5],
        "source_url": row[6],
        "superseded": row[7],
        "tier": row[8],
    }


def count_superseded_siblings(conn, doc_code: str, current_document_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM documents
            WHERE doc_code = %s AND superseded = true AND id != %s
            """,
            (doc_code, current_document_id),
        )
        return cur.fetchone()[0]


# Belt-and-suspenders for the ASCII-only constraint below: prompting an LLM not to use
# smart quotes/em dashes reduces but doesn't guarantee it won't (contractions like
# "applicant's" are exactly where models tend to slip in a curly apostrophe anyway).
# ask.py/demo.py print this same string on a Windows cp1252 console, where an
# un-caught unicode char is a crash, not a cosmetic glitch -- so it's translated to
# plain ASCII unconditionally, regardless of how well the model followed instructions.
_UNICODE_TO_ASCII = str.maketrans({
    "‘": "'", "’": "'",
    "“": '"', "”": '"',
    "–": "-", "—": "-",
    "•": "-",
    "…": "...",
    " ": " ",
})

# Not every local model that LM Studio can "force thinking" on actually shares Qwen3's
# trained <think>...</think> delimiter convention, which is the only format LM Studio's
# OpenAI-compat server knows how to split into reasoning_content. A mismatched model
# (observed with Gemma) just emits it inline in content instead -- strip it as a safety
# net so a leak degrades to "missing the reasoning" rather than a garbled visible answer.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)


def _build_messages(
    question: str, chunks: list[dict], tier: str = "high", history: list[dict] | None = None,
) -> list[dict]:
    """Shared by generate_answer() (blocking) and answer_question_stream()'s OpenAI-only
    token-streaming path, so the system prompt lives in exactly one place. `chunks` is
    already filtered to what actually cleared CONFIDENCE_LOW (see filter_weak_chunks()),
    so "use every excerpt" no longer means "use every excerpt regardless of relevance".
    `history` (if given) is prior conversation turns, capped to the last 10 -- it's for
    conversational context only; retrieval itself always runs on the latest question."""
    context = "\n\n".join(
        f"[Source {i+1}, page {c['page']}]\n{c['text']}" for i, c in enumerate(chunks)
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You are ReguLense, a compliance assistant for UAE health regulation. "
                "Answer using ONLY the provided source excerpts -- never invent facts not "
                "present in them. Prior conversation turns, if any, are for context on "
                "what's already been discussed -- do not re-answer them, and do not treat "
                "them as a source of facts about the regulation itself.\n\n"
                "You are given up to 7 source excerpts, already filtered to ones judged "
                "relevant to the question -- often several different pages of the same "
                "manual. Skim all of them before writing; don't stop at the first one that "
                "contains the literal answer if others add genuinely distinct relevant "
                "detail. But every one of them already cleared a relevance bar to reach you, "
                "not every excerpt earns a bullet -- write one bullet per excerpt that "
                "actually adds a distinct relevant fact, and no more. A short, direct "
                "question with a short, complete answer should get a short answer.\n\n"
                "Format the answer as:\n"
                "1. One direct sentence in **bold** that answers the question.\n"
                "2. A line reading exactly 'Findings:' on its own.\n"
                "3. Then '-' bullet points, each 2 lines or fewer. Use as many as the "
                "excerpts genuinely support -- do not treat any specific number as a target "
                "or a ceiling, in either direction. If only 1 excerpt actually bears on the "
                "question, write 1 bullet; if 3 do, write 3; if 8 do, write 8. Each bullet "
                "should draw on a DIFFERENT one of the provided excerpts where they actually "
                "contain distinct relevant facts -- do not paraphrase the same excerpt "
                "several times, and do not manufacture a bullet from an excerpt that only "
                "tangentially relates to the question just to use it. When directly quoting "
                "an excerpt verbatim (rather than paraphrasing it) because the exact wording "
                "matters -- a defined term, a specific obligation, a precise condition -- "
                "wrap that quotation in a Markdown blockquote line starting with '> '. Do NOT "
                "add quotation marks of your own around it -- no straight quotes, no curly "
                "quotes -- the interface renders the quotation marks itself.\n"
                "4. A line reading exactly 'Summary:' followed by ONE sentence that "
                "synthesizes the findings above into a bottom-line takeaway -- do not just "
                "repeat the opening bold sentence in different words; add the 'so what'.\n"
                "5. If the excerpts only partially cover the question, end with a line "
                "starting 'Not covered:' naming what's missing.\n"
                f"6. Retrieval confidence for this query has been assessed as '{tier}'. If "
                "and only if it is 'medium' or 'low', end with a line reading exactly "
                "'Certainty:' followed by ONE honest sentence: for 'medium', say the answer "
                "is grounded in fewer or less directly on-topic sources than usual; for "
                "'low', say plainly that the findings are inconclusive given what's "
                "available. If confidence is 'high', omit this line entirely -- do not "
                "invent a certainty statement that wasn't asked for.\n\n"
                "Keep the total answer under 300 words unless the question genuinely "
                "requires more.\n\n"
                "CITATION FORMAT -- read carefully, this is checked: after every sentence "
                "or bullet that draws on a specific source excerpt, add an inline citation "
                "marker matching that excerpt's number from the 'Sources' section below, "
                "in square brackets immediately after the sentence with no space before it "
                "-- e.g. 'Patient consent is mandatory.[2]'. If a sentence draws on more "
                "than one excerpt, stack the markers: '...must use approved platforms.[1][3]'. "
                "Only use excerpt numbers that actually appear in the Sources section -- "
                "never invent a number higher than the count of excerpts given, and never "
                "cite an excerpt for a fact it doesn't actually support. Use no other "
                "citation form -- no 'Source 1', no '(see excerpt 2)', no footnotes -- only "
                "the bracketed-number form.\n\n"
                "Use plain ASCII characters only: a hyphen '-', never an em dash; straight "
                "quotes, never curly ones; no bullet characters (use '-'); no unicode "
                "punctuation of any kind."
            ),
        },
    ]
    for turn in (history or [])[-10:]:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": f"Question: {question}\n\nSources:\n{context}"})
    return messages


def _finish_answer_text(text: str, provider: str, active_model: str) -> str:
    """Shared post-processing for both the blocking and streaming generation paths."""
    if provider == "local":
        text = _THINK_BLOCK.sub("", text)
    text = text.strip()
    if not text:
        raise RuntimeError(
            f"'{active_model}' returned no answer text -- likely spent its full response "
            "budget on unparsed reasoning. Try disabling thinking or picking a different "
            "model in LM Studio."
        )
    return text.translate(_UNICODE_TO_ASCII)


def generate_answer(
    question: str, chunks: list[dict], provider: str = "openai", model: str | None = None,
    tier: str = "high", history: list[dict] | None = None, client_ip: str | None = None,
) -> tuple[str, str]:
    """Returns (answer_text, active_model_actually_used) -- the latter can differ from
    the requested model when the OpenAI->NaraRouter fallback kicked in, which callers
    surface to the frontend so it can show which model actually answered."""
    messages = _build_messages(question, chunks, tier, history)
    if provider != "openai":
        active_model = resolve_model(provider, model)
        resp = local_client.chat.completions.create(model=active_model, messages=messages, temperature=0)
        text = resp.choices[0].message.content or ""
        return _finish_answer_text(text, provider, active_model), active_model

    resp, active_model = chat_completion(messages, client_ip=client_ip)
    text = resp.choices[0].message.content or ""
    return _finish_answer_text(text, provider, active_model), active_model


def _reduce_stream_output(chunks: list[dict]) -> dict:
    """reduce_fn: a traced generator logs its entire yielded sequence as Output by
    default -- for this function that's every progress step ("embedding_query",
    "searching_sources", ...), not the answer. Only the final "done" step carries the
    actual result, so that's the only thing worth logging."""
    for chunk in reversed(chunks):
        if chunk.get("step") == "done":
            return chunk.get("result", {})
    return {}


@traceable(
    run_type="chain",
    name="answer_question_stream",
    process_inputs=_without_conn,
    reduce_fn=_reduce_stream_output,
)
def answer_question_stream(
    conn, question: str, superseded_filter: bool, provider: str = "openai", model: str | None = None,
    authority_filter: str | None = None, history: list[dict] | None = None, client_ip: str | None = None,
):
    """Same pipeline as answer_question(), reusing the exact same helper functions in
    the exact same order, but yielding a step event between each stage so a caller can
    show live progress. Ends with {"step": "done", "result": <same dict answer_question()
    would return>}. answer_question() itself is untouched — this is purely additive
    instrumentation for the streaming UI, not a second implementation of the pipeline."""
    _name_run_by_model(provider, model)
    yield {"step": "embedding_query"}
    query_vec = embed(question)

    yield {"step": "searching_sources"}
    semantic_results = semantic_search(conn, query_vec, superseded_filter, CANDIDATES_PER_METHOD, authority_filter)
    lexical_results = lexical_search(conn, question, superseded_filter, CANDIDATES_PER_METHOD, authority_filter)

    yield {"step": "aggregating_results", "detail": f"{len(semantic_results) + len(lexical_results)} candidates"}
    fused = rrf_fuse(semantic_results, lexical_results, TOP_N_FOR_ANSWER)

    if not fused:
        yield {"step": "done", "result": {"abstained": True, "reason": "no matching documents in the corpus", "run_id": _current_run_id()}}
        return

    top_score = fused[0]["semantic_score"]
    tier = confidence_tier(top_score)
    if tier is None:
        yield {
            "step": "done",
            "result": {
                "abstained": True,
                "reason": "below retrieval confidence threshold",
                "top_score": top_score,
                "run_id": _current_run_id(),
            },
        }
        return
    if tier == "low":
        _flag_low_confidence(top_score)

    top_chunk = fused[0]
    document = get_document(conn, top_chunk["document_id"])
    filtered_chunks = filter_weak_chunks(fused)
    used_ids = {c["chunk_id"] for c in filtered_chunks}
    for c in fused:
        c["used_for_answer"] = c["chunk_id"] in used_ids

    yield {"step": "checking_supersession", "doc_code": document["doc_code"]}
    superseded_excluded = count_superseded_siblings(conn, document["doc_code"], document["id"])

    yield {"step": "citing_source", "title": document["title"], "doc_code": document["doc_code"], "version": document["version"]}

    active_model = resolve_model(provider, model)
    yield {"step": "generating_answer", "detail": active_model}
    if provider == "openai":
        messages = _build_messages(question, filtered_chunks, tier, history)
        use_nara = _openai_is_degraded() or (client_ip is not None and _openai_ip_limit_exceeded(client_ip))
        if not use_nara:
            try:
                # Token-level streaming for OpenAI only -- gpt-4o-mini never emits the
                # <think> blocks that make local-model streaming risky (see the else
                # branch's generate_answer() call, which strips those before streaming
                # would). NaraRouter's own streaming is unreliable in two different ways
                # confirmed by hand: it sometimes sends a trailing usage-stats chunk with
                # an empty choices list (crashes a naive .choices[0]), and sometimes
                # drops a mid-stream APIError after already sending a partial answer --
                # so it's never streamed here, see the branch below.
                stream_iter = client.chat.completions.create(model=CHAT_MODEL, messages=messages, temperature=0, stream=True)
                active_model = CHAT_MODEL
                answer_text = ""
                for stream_chunk in stream_iter:
                    if not stream_chunk.choices:
                        continue  # defensive: a usage-stats trailer chunk, not a real delta
                    delta = stream_chunk.choices[0].delta.content
                    if delta:
                        answer_text += delta
                        yield {"step": "answer_delta", "detail": delta}
                if client_ip is not None:
                    _record_openai_call(client_ip)
            except Exception:
                _mark_openai_degraded()
                use_nara = True
        if use_nara:
            if nararouter_client is None:
                raise RuntimeError("OpenAI is unavailable and NARAROUTER_API_KEY is not configured -- no fallback available")
            # A failure can land here after OpenAI already streamed part of an answer
            # (a rare mid-stream drop, not just an upfront rate limit) -- tell the
            # frontend to discard whatever it already has before NaraRouter's full,
            # independent answer arrives, or the two would run together as one
            # garbled response.
            yield {"step": "answer_reset"}
            reason = "OpenAI unavailable" if _openai_is_degraded() else "rate limit reached"
            yield {"step": "provider_fallback", "detail": f"{reason} -- switching to NaraRouter"}
            # Non-streaming: delivered as one instant chunk instead of token-by-token,
            # trading the typing effect for not depending on NaraRouter's flaky stream.
            resp = nararouter_client.chat.completions.create(model=NARAROUTER_MODEL, messages=messages, temperature=0, stream=False)
            active_model = NARAROUTER_MODEL
            answer_text = resp.choices[0].message.content or ""
            yield {"step": "answer_delta", "detail": answer_text}
        answer_text = _finish_answer_text(answer_text, provider, active_model)
    else:
        answer_text, active_model = generate_answer(question, filtered_chunks, provider, model, tier, history)

    yield {
        "step": "done",
        "result": {
            "abstained": False,
            "answer": answer_text,
            "model_used": active_model,
            "top_score": top_score,
            "confidence_tier": tier,
            "document": document,
            "page": top_chunk["page"],
            "page_end": top_chunk["page_end"],
            "heading_path": top_chunk["heading_path"],
            "bboxes": top_chunk["bboxes"],
            "superseded_excluded": superseded_excluded,
            "retrieved_chunks": fused,
            "run_id": _current_run_id(),
        },
    }


@traceable(run_type="chain", name="answer_question", process_inputs=_without_conn)
def answer_question(
    conn, question: str, superseded_filter: bool, provider: str = "openai", model: str | None = None,
    authority_filter: str | None = None, history: list[dict] | None = None, client_ip: str | None = None,
) -> dict:
    """Returns a dict describing either an abstention or a full answer with citation."""
    _name_run_by_model(provider, model)
    query_vec = embed(question)
    semantic_results = semantic_search(conn, query_vec, superseded_filter, CANDIDATES_PER_METHOD, authority_filter)
    lexical_results = lexical_search(conn, question, superseded_filter, CANDIDATES_PER_METHOD, authority_filter)
    fused = rrf_fuse(semantic_results, lexical_results, TOP_N_FOR_ANSWER)

    if not fused:
        return {"abstained": True, "reason": "no matching documents in the corpus", "run_id": _current_run_id()}

    top_score = fused[0]["semantic_score"]
    tier = confidence_tier(top_score)
    if tier is None:
        return {
            "abstained": True,
            "reason": "below retrieval confidence threshold",
            "top_score": top_score,
            "run_id": _current_run_id(),
        }
    if tier == "low":
        _flag_low_confidence(top_score)

    top_chunk = fused[0]
    document = get_document(conn, top_chunk["document_id"])
    superseded_excluded = count_superseded_siblings(conn, document["doc_code"], document["id"])
    filtered_chunks = filter_weak_chunks(fused)
    used_ids = {c["chunk_id"] for c in filtered_chunks}
    for c in fused:
        c["used_for_answer"] = c["chunk_id"] in used_ids

    answer_text, active_model = generate_answer(question, filtered_chunks, provider, model, tier, history, client_ip)

    return {
        "abstained": False,
        "answer": answer_text,
        "model_used": active_model,
        "top_score": top_score,
        "confidence_tier": tier,
        "document": document,
        "page": top_chunk["page"],
        "page_end": top_chunk["page_end"],
        "heading_path": top_chunk["heading_path"],
        "bboxes": top_chunk["bboxes"],
        "superseded_excluded": superseded_excluded,
        "retrieved_chunks": fused,
        "run_id": _current_run_id(),
    }
