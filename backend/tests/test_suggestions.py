"""Tests for mined follow-up suggestions: the JSONL loader
(ingestion/load_suggestions.py), runtime matching (app/services/suggestions.py),
and their wiring into answer_question / answer_question_stream.

Convention notes: loader store paths commit, so an autouse TRUNCATE (not rollback)
isolates tests; answer-path tests take redis_conn because the answer-cache probe
would otherwise read the shared real Redis (same trap documented in
test_guardrail.py).
"""
from unittest.mock import MagicMock

import pytest

from app.core import retrieval
from app.core.answer_cache import normalize_question
from app.services.suggestions import find_suggested_followups
from ingestion.load_suggestions import load_suggestions_file
from tests.conftest import QUERY_VEC, orthogonal_vec, seed_chunk, seed_document, vec

RELEVANT_VERDICT = "VERDICT: RELEVANT\nSUBJECT: t\nREDIRECT: general\nSUGGESTIONS: none"

OFF_TOPIC_VERDICT = (
    "VERDICT: OFF_TOPIC\nSUBJECT: a footballer's shoe size\n"
    "REDIRECT: patient safety and quality standards\n"
    "SUGGESTIONS: What are the licensing requirements? | How long is a license valid?"
)


@pytest.fixture(autouse=True)
def _clean_suggestion_state(conn):
    def _reset():
        with conn.cursor() as cur:
            cur.execute("TRUNCATE answer_cache, suggested_questions, documents RESTART IDENTITY CASCADE")
        conn.commit()

    _reset()
    yield
    _reset()


def _write(tmp_path, name, lines):
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _good_line(doc_code="DHA/TEST/01", anchor="licenses are valid for two years"):
    return (
        '{"question": "How long is the license valid?", '
        f'"doc_code": "{doc_code}", "authority": "Dubai Health Authority", '
        '"tier": "official", "pages": [3], "section": "Validity", '
        f'"anchor_quote": "{anchor}", "form": "how-long"}}'
    )


def _seed_doc_with_text(conn, doc_code="DHA/TEST/01", text="Professional licenses are valid for two years."):
    doc_id = seed_document(conn, doc_code=doc_code)
    seed_chunk(conn, doc_id, page=3, text=text)
    return doc_id


def _insert_suggestion(conn, question, embedding, doc_id, doc_code="DHA/TEST/01",
                       authority="Dubai Health Authority"):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO suggested_questions (
                question, question_normalized, question_embedding,
                doc_code, document_id, chunk_ids, pages, section, authority, tier, mined_by
            )
            VALUES (%s, %s, %s::vector, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (question, normalize_question(question), embedding, doc_code, doc_id,
             [1], [3], "Validity", authority, "official", "test"),
        )
    conn.commit()


# --- loader ---------------------------------------------------------------


def test_loader_loads_valid_line(conn, tmp_path):
    doc_id = _seed_doc_with_text(conn)
    path = _write(tmp_path, "s.a.jsonl", [_good_line()])

    stats = load_suggestions_file(conn, path, "worker-a", embed_fn=lambda t: QUERY_VEC)

    assert stats == {"loaded": 1, "skipped_docs": 0, "rejected": 0}
    with conn.cursor() as cur:
        cur.execute("SELECT question, document_id, pages, chunk_ids FROM suggested_questions")
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "How long is the license valid?"
    assert rows[0][1] == doc_id
    assert rows[0][2] == [3]
    assert rows[0][3] != []


def test_loader_rejects_garbage(conn, tmp_path):
    _seed_doc_with_text(conn)
    path = _write(tmp_path, "s.b.jsonl", [
        "{not json",
        '{"question": "Missing fields"}',
        _good_line().replace('"how-long"', '"essay"'),
        _good_line("DHA/NOPE/99"),
        _good_line().replace("licenses are valid for two years", "invented text here"),
        "SKIP: cover pages only",
        "",
    ])

    stats = load_suggestions_file(conn, path, "worker-b", embed_fn=lambda t: QUERY_VEC)

    assert stats == {"loaded": 0, "skipped_docs": 1, "rejected": 5}
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM suggested_questions")
        assert cur.fetchone()[0] == 0


def test_loader_rerun_overwrites(conn, tmp_path):
    _seed_doc_with_text(conn)
    path = _write(tmp_path, "s.c.jsonl", [_good_line()])

    first = load_suggestions_file(conn, path, "w1", embed_fn=lambda t: QUERY_VEC)
    second = load_suggestions_file(conn, path, "w2", embed_fn=lambda t: QUERY_VEC)

    assert first["loaded"] == 1 and second["loaded"] == 1
    with conn.cursor() as cur:
        cur.execute("SELECT count(*), max(mined_by) FROM suggested_questions")
        count, mined_by = cur.fetchone()
    assert count == 1
    assert mined_by == "w2"


def test_loader_anchor_spanning_chunks_falls_back_to_pages(conn, tmp_path):
    """Anchor straddling a chunk boundary matches no single chunk -> chunks on the
    listed pages are used instead of rejecting the line."""
    doc_id = seed_document(conn, doc_code="DHA/TEST/02")
    seed_chunk(conn, doc_id, page=3, text="Professional licenses are")
    seed_chunk(conn, doc_id, page=3, text="valid for two years here.")
    path = _write(tmp_path, "s.d.jsonl", [_good_line("DHA/TEST/02", "licenses are valid for two years")])

    stats = load_suggestions_file(conn, path, "w", embed_fn=lambda t: QUERY_VEC)

    assert stats["loaded"] == 1
    with conn.cursor() as cur:
        cur.execute("SELECT chunk_ids FROM suggested_questions")
        assert len(cur.fetchone()[0]) == 2


# --- runtime matching ------------------------------------------------------


def test_match_prefers_same_authority_excludes_asked_and_limits(conn):
    dha = seed_document(conn, doc_code="DHA/A", authority="Dubai Health Authority")
    doh = seed_document(conn, doc_code="DOH/B", authority="Department of Health - Abu Dhabi")
    _insert_suggestion(conn, "What are the telehealth standards?", QUERY_VEC, dha)
    _insert_suggestion(conn, "DoH data rules?", QUERY_VEC, doh,
                       doc_code="DOH/B", authority="Department of Health - Abu Dhabi")
    _insert_suggestion(conn, "Far match?", orthogonal_vec(), dha)

    got = find_suggested_followups(
        conn, QUERY_VEC, "What are the telehealth standards?",
        authority="Dubai Health Authority", limit=2, min_similarity=0.0)

    questions = [g["question"] for g in got]
    assert "What are the telehealth standards?" not in questions  # asked => excluded
    assert len(got) == 2  # limit honored
    assert got[0]["question"] == "Far match?"  # same-authority first despite worse vector
    assert set(got[0]) == {"question", "doc_code", "document_id", "pages", "section"}


def test_match_returns_closest_by_default(conn):
    """No floor by default: the closest suggestions are returned even when the match
    is weak, so the conversation always has somewhere to go."""
    dha = seed_document(conn, doc_code="DHA/A", authority="Dubai Health Authority")
    _insert_suggestion(conn, "Strong match?", vec(0.75), dha)
    _insert_suggestion(conn, "Weak-but-closest fallback?", vec(0.58), dha)

    got = find_suggested_followups(conn, QUERY_VEC, "asked?", authority="Dubai Health Authority")

    assert [g["question"] for g in got] == ["Strong match?", "Weak-but-closest fallback?"]


def test_match_floor_filters_when_configured(conn):
    """The calibrated floor stays available via config/env for deployments that
    prefer silence over a weak match."""
    dha = seed_document(conn, doc_code="DHA/A", authority="Dubai Health Authority")
    _insert_suggestion(conn, "Strong match?", vec(0.75), dha)
    _insert_suggestion(conn, "Pandemic-style hard negative?", vec(0.58), dha)

    got = find_suggested_followups(
        conn, QUERY_VEC, "asked?", authority="Dubai Health Authority", min_similarity=0.62)

    assert [g["question"] for g in got] == ["Strong match?"]


def test_match_excludes_questions_asked_earlier(conn):
    """Clicking a suggestion must not rotate the same questions back in: every prior
    user question in the conversation is excluded, not just the current one."""
    dha = seed_document(conn, doc_code="DHA/A", authority="Dubai Health Authority")
    _insert_suggestion(conn, "First suggestion?", vec(0.8), dha)
    _insert_suggestion(conn, "Second suggestion?", vec(0.7), dha)
    _insert_suggestion(conn, "Third suggestion?", vec(0.6), dha)

    got = find_suggested_followups(
        conn, QUERY_VEC, "current?", authority="Dubai Health Authority",
        exclude_questions=["First suggestion?", "Second suggestion?"])

    assert [g["question"] for g in got] == ["Third suggestion?"]


def test_match_failure_returns_empty():
    assert find_suggested_followups(None, QUERY_VEC, "q") == []


# --- answer-path integration -----------------------------------------------


def _mock_relevant_guardrail(monkeypatch):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=RELEVANT_VERDICT))]
    monkeypatch.setattr(retrieval, "chat_completion",
                        MagicMock(return_value=(mock_response, "gpt-4o-mini")))


def _mock_off_topic_guardrail(monkeypatch):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=OFF_TOPIC_VERDICT))]
    monkeypatch.setattr(retrieval, "chat_completion",
                        MagicMock(return_value=(mock_response, "gpt-4o-mini")))


def test_off_topic_carries_mined_suggestions_not_guard_inventions(conn, redis_conn, monkeypatch):
    """The off-topic panel must show mined, floor-checked questions -- not the
    guardrail's own unvalidated inventions (which once recommended a question the
    same guard then rejected)."""
    from tests.conftest import seed_official_doc

    seed_official_doc(conn, score=0.7)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM documents LIMIT 1")
        doc_id = cur.fetchone()[0]
    _insert_suggestion(conn, "How long is the DHA license valid?", QUERY_VEC, doc_id)
    monkeypatch.setattr(retrieval, "embed", lambda text: QUERY_VEC)
    gen = MagicMock(side_effect=AssertionError("generation must not run"))
    monkeypatch.setattr(retrieval, "generate_answer", gen)
    _mock_off_topic_guardrail(monkeypatch)

    result = retrieval.answer_question(conn, "What shoe size is Messi?", superseded_filter=True)

    assert result["abstained"] is True and result["off_topic"] is True
    assert [s["question"] for s in result["suggested_followups"]] == [
        "How long is the DHA license valid?"]
    # Legacy field stays in the payload for API compatibility; the UI no longer renders it.
    assert result["suggested_questions"] == [
        "What are the licensing requirements?", "How long is a license valid?"]
    gen.assert_not_called()


def test_off_topic_skips_questions_already_asked_in_history(conn, redis_conn, monkeypatch):
    """History exclusion is wired end-to-end: a suggestion the user already asked
    earlier in the conversation never comes back, even on off-topic answers."""
    from tests.conftest import seed_official_doc

    seed_official_doc(conn, score=0.7)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM documents LIMIT 1")
        doc_id = cur.fetchone()[0]
    _insert_suggestion(conn, "Already asked question?", vec(0.8), doc_id)
    _insert_suggestion(conn, "Fresh question?", vec(0.7), doc_id)
    monkeypatch.setattr(retrieval, "embed", lambda text: QUERY_VEC)
    monkeypatch.setattr(
        retrieval, "generate_answer",
        MagicMock(side_effect=AssertionError("generation must not run")),
    )
    _mock_off_topic_guardrail(monkeypatch)

    result = retrieval.answer_question(
        conn, "What shoe size is Messi?", superseded_filter=True,
        history=[
            {"role": "user", "content": "Already asked question?"},
            {"role": "assistant", "content": "Stub answer."},
        ],
    )

    assert [s["question"] for s in result["suggested_followups"]] == ["Fresh question?"]


def test_answer_includes_mined_followups(conn, redis_conn, monkeypatch):
    from tests.conftest import seed_official_doc

    seed_official_doc(conn, score=0.7)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM documents LIMIT 1")
        doc_id = cur.fetchone()[0]
    _insert_suggestion(conn, "How long is the DHA license valid?", QUERY_VEC, doc_id)
    monkeypatch.setattr(retrieval, "embed", lambda text: QUERY_VEC)
    monkeypatch.setattr(retrieval, "generate_answer", lambda *a, **kw: ("Stub answer.", "gpt-4o-mini"))
    _mock_relevant_guardrail(monkeypatch)

    result = retrieval.answer_question(conn, "What are the telehealth standards?", superseded_filter=False)

    assert result["abstained"] is False
    assert result["suggested_followups"] == [{
        "question": "How long is the DHA license valid?", "doc_code": "DHA/TEST/01",
        "document_id": doc_id, "pages": [3], "section": "Validity",
    }]


def test_stream_includes_mined_followups(conn, redis_conn, monkeypatch):
    from tests.conftest import seed_official_doc

    seed_official_doc(conn, score=0.7)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM documents LIMIT 1")
        doc_id = cur.fetchone()[0]
    _insert_suggestion(conn, "How long is the DHA license valid?", QUERY_VEC, doc_id)
    monkeypatch.setattr(retrieval, "embed", lambda text: QUERY_VEC)
    monkeypatch.setattr(retrieval, "generate_answer", lambda *a, **kw: ("Stub answer.", "gpt-4o-mini"))
    _mock_relevant_guardrail(monkeypatch)

    events = list(retrieval.answer_question_stream(
        conn, "What are the telehealth standards?", superseded_filter=False))
    done = events[-1]

    assert done["step"] == "done"
    assert [s["question"] for s in done["result"]["suggested_followups"]] == [
        "How long is the DHA license valid?"]


# --- suggestions are live, never frozen into the cache ---------------------
#
# Regression for "the frontend shows the same questions over and over": the
# cached answer used to store suggested_followups at generation time, so repeats
# served whatever existed (or didn't) back then, falling back to the static bank.


def test_cache_stores_no_suggestions_and_repeat_recomputes_them(conn, redis_conn, monkeypatch):
    from tests.conftest import seed_official_doc

    seed_official_doc(conn, score=0.7)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM documents LIMIT 1")
        doc_id = cur.fetchone()[0]
    _insert_suggestion(conn, "Old suggestion?", QUERY_VEC, doc_id)
    embed_spy = MagicMock(return_value=QUERY_VEC)
    monkeypatch.setattr(retrieval, "embed", embed_spy)
    monkeypatch.setattr(retrieval, "generate_answer", lambda *a, **kw: ("Stub answer.", "gpt-4o-mini"))
    _mock_relevant_guardrail(monkeypatch)
    question = "What are the telehealth standards?"

    first = retrieval.answer_question(conn, question, superseded_filter=False)
    assert first["abstained"] is False
    assert [s["question"] for s in first["suggested_followups"]] == ["Old suggestion?"]
    assert embed_spy.call_count == 1

    # The stored payload must NOT carry suggestions (they are live annotations).
    with conn.cursor() as cur:
        cur.execute(
            "SELECT result_json ? 'suggested_followups' FROM answer_cache ORDER BY id DESC LIMIT 1"
        )
        assert cur.fetchone()[0] is False

    # Corpus changes between asks: the mined set is replaced.
    with conn.cursor() as cur:
        cur.execute("DELETE FROM suggested_questions")
    _insert_suggestion(conn, "New suggestion!", QUERY_VEC, doc_id)

    repeat = retrieval.answer_question(conn, question, superseded_filter=False)

    assert repeat["cache_hit"] is True
    assert embed_spy.call_count == 1  # exact-key hit: stored embedding reused
    assert [s["question"] for s in repeat["suggested_followups"]] == ["New suggestion!"]


def test_stream_cache_hit_refreshes_suggestions(conn, redis_conn, monkeypatch):
    from tests.conftest import seed_official_doc

    seed_official_doc(conn, score=0.7)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM documents LIMIT 1")
        doc_id = cur.fetchone()[0]
    _insert_suggestion(conn, "First take?", QUERY_VEC, doc_id)
    monkeypatch.setattr(retrieval, "embed", lambda text: QUERY_VEC)
    monkeypatch.setattr(retrieval, "generate_answer", lambda *a, **kw: ("Stub answer.", "gpt-4o-mini"))
    _mock_relevant_guardrail(monkeypatch)
    question = "What are the telehealth standards?"

    list(retrieval.answer_question_stream(conn, question, superseded_filter=False))
    with conn.cursor() as cur:
        cur.execute("DELETE FROM suggested_questions")
    _insert_suggestion(conn, "Second take!", QUERY_VEC, doc_id)

    events = list(retrieval.answer_question_stream(conn, question, superseded_filter=False))
    result = events[-1]["result"]

    assert result["cache_hit"] is True
    assert [s["question"] for s in result["suggested_followups"]] == ["Second take!"]


def test_cache_hit_without_stored_embedding_drops_stale_suggestions(conn, redis_conn, monkeypatch):
    """If the embedding can't be recovered, stale frozen suggestions must not leak
    through -- the key is dropped so the frontend's static bank takes over."""
    from tests.conftest import seed_official_doc

    seed_official_doc(conn, score=0.7)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM documents LIMIT 1")
        doc_id = cur.fetchone()[0]
    _insert_suggestion(conn, "Fresh one?", QUERY_VEC, doc_id)
    monkeypatch.setattr(retrieval, "embed", lambda text: QUERY_VEC)
    monkeypatch.setattr(retrieval, "generate_answer", lambda *a, **kw: ("Stub answer.", "gpt-4o-mini"))
    _mock_relevant_guardrail(monkeypatch)

    def _boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(retrieval, "fetch_cached_query_embedding", _boom)
    first = retrieval.answer_question(conn, "What are the telehealth standards?", superseded_filter=False)

    # Fresh path had its own vector; the repeat needs the (now failing) fetch.
    repeat = retrieval.answer_question(conn, "What are the telehealth standards?", superseded_filter=False)

    assert first["suggested_followups"]
    assert repeat.get("cache_hit") is True
    assert "suggested_followups" not in repeat


def test_mined_question_skips_the_guardrail(conn, redis_conn, monkeypatch):
    """A question the product itself recommended is corpus-grounded by construction and
    must never be rejected by the judge -- measured live: local qwen 4B called a mined
    DHA school-nurse question out of scope while gpt-4o-mini accepted it with the same
    retrieved evidence. The guardrail must not even be called for it."""
    from tests.conftest import seed_official_doc

    seed_official_doc(conn, score=0.7)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM documents LIMIT 1")
        doc_id = cur.fetchone()[0]
    mined = "How long is the DHA license valid?"
    _insert_suggestion(conn, mined, QUERY_VEC, doc_id)
    monkeypatch.setattr(retrieval, "embed", lambda text: QUERY_VEC)
    gen = MagicMock(return_value=("Stub answer text.", retrieval.CHAT_MODEL))
    monkeypatch.setattr(retrieval, "generate_answer", gen)
    guard_spy = MagicMock(side_effect=AssertionError("guardrail must be skipped for mined questions"))
    monkeypatch.setattr(retrieval, "chat_completion", guard_spy)

    result = retrieval.answer_question(conn, mined, superseded_filter=True)

    assert result["abstained"] is False
    gen.assert_called_once()
    guard_spy.assert_not_called()
