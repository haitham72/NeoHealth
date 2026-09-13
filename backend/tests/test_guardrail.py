"""Tests for the LLM relevance guardrail (app.core.guardrail) and its wiring into
answer_question / answer_question_stream.

Context: "What shoe size is Messi?" scores ~0.23 live -- above the 0.15 numeric
floor -- so tiering alone cannot catch it. These tests prove the LLM gate does:
OFF_TOPIC short-circuits before generation (no sources, no follow-up picker), while
RELEVANT, unparseable, and guardrail-down cases all fall through to the normal
numeric path (fail-open).
"""
from unittest.mock import MagicMock

import pytest

from app.core import guardrail, retrieval
from app.core.guardrail import build_offtopic_message, parse_verdict, validate_redirect
from tests.conftest import QUERY_VEC, seed_official_doc


OFF_TOPIC_VERDICT = """VERDICT: OFF_TOPIC
SUBJECT: a footballer's shoe size
REDIRECT: licensing of healthcare professionals
SUGGESTIONS: What are the requirements for licensing a healthcare professional in Dubai? | How long is a DHA license valid?"""

RELEVANT_VERDICT = """VERDICT: RELEVANT
SUBJECT: DHA telehealth licensing
REDIRECT: telehealth and telemedicine standards
SUGGESTIONS: none"""


def _mock_chat_completion(monkeypatch, text: str):
    """Stubs the guardrail's OpenAI path (deferred import inside check_relevance
    resolves the attribute on the retrieval module at call time, so patching there
    is what takes effect). Returns the spy."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=text))]
    spy = MagicMock(return_value=(mock_response, "gpt-4o-mini"))
    monkeypatch.setattr(retrieval, "chat_completion", spy)
    return spy


def _mock_generate_answer(monkeypatch, fail_message: str = "generate_answer must not be called"):
    """Stubs generation; defaults to raising so short-circuit tests prove the LLM
    generation call was never reached."""
    stub = MagicMock(side_effect=AssertionError(fail_message))
    monkeypatch.setattr(retrieval, "generate_answer", stub)
    return stub


@pytest.fixture(autouse=True)
def _clean_guardrail_state(conn):
    """Pipeline tests commit (store_answer_cache commits on every successful
    generation, which also persists the seeded docs). answer_cache has no FK into
    documents, so it needs an explicit truncate -- otherwise the three tests asking
    "What are the telehealth standards?" hit each other's rows. Truncate before AND
    after each test, same rationale as test_diff_cache.py's fixture."""
    def _reset():
        with conn.cursor() as cur:
            cur.execute("TRUNCATE answer_cache, documents RESTART IDENTITY CASCADE")
        conn.commit()

    _reset()
    yield
    _reset()


@pytest.fixture
def pipeline_env(conn, redis_conn, monkeypatch):
    """Seeded official doc (score 0.7 -> tier high, guardrail always reached) with
    retrieval's own OpenAI edges stubbed -- embed returns QUERY_VEC, generation
    raises if reached. Individual tests then stub the guardrail verdict."""
    seed_official_doc(conn, score=0.7)
    monkeypatch.setattr(retrieval, "embed", lambda text: QUERY_VEC)
    return _mock_generate_answer(monkeypatch)


# --- parse_verdict unit tests (no DB) -------------------------------------------


def test_parse_off_topic_verdict():
    parsed = parse_verdict(OFF_TOPIC_VERDICT)
    assert parsed["is_relevant"] is False
    assert parsed["subject"] == "a footballer's shoe size"
    assert parsed["redirect"] == "licensing of healthcare professionals"
    assert parsed["suggestions"] == [
        "What are the requirements for licensing a healthcare professional in Dubai?",
        "How long is a DHA license valid?",
    ]


def test_parse_relevant_verdict_with_none_suggestions():
    parsed = parse_verdict(RELEVANT_VERDICT)
    assert parsed["is_relevant"] is True
    assert parsed["suggestions"] == []


def test_parse_missing_verdict_fails_open_to_relevant():
    parsed = parse_verdict("SUBJECT: something\nREDIRECT: general\nSUGGESTIONS: none")
    assert parsed["is_relevant"] is True


def test_parse_lowercase_verdict_and_suggestion_cap():
    parsed = parse_verdict(
        "verdict: off_topic\nsubject: shoes\nredirect: general\n"
        "suggestions: one? | two? | three? | four?"
    )
    assert parsed["is_relevant"] is False
    assert len(parsed["suggestions"]) == 3


def test_parse_drops_overlong_suggestions():
    parsed = parse_verdict("VERDICT: OFF_TOPIC\nSUBJECT: x\nREDIRECT: general\nSUGGESTIONS: ok? | " + "y" * 250)
    assert parsed["suggestions"] == ["ok?"]


def test_validate_redirect_allowlist_and_fallback():
    assert validate_redirect("Telehealth and Telemedicine Standards") == "telehealth and telemedicine standards"
    assert validate_redirect("DHA shoe rules") == guardrail.GENERIC_REDIRECT
    assert validate_redirect("") == guardrail.GENERIC_REDIRECT
    assert validate_redirect("general") == guardrail.GENERIC_REDIRECT


def test_build_offtopic_message_with_and_without_subject():
    msg = build_offtopic_message("a footballer's shoe size", "licensing of healthcare professionals")
    assert "a footballer's shoe size" in msg
    assert "out of scope" in msg
    assert "licensing of healthcare professionals" in msg
    assert build_offtopic_message("", "general topic").startswith("This question looks out of scope")


# --- pipeline wiring -------------------------------------------------------------


def test_off_topic_abstains_before_generation(pipeline_env, conn, monkeypatch):
    chat_spy = _mock_chat_completion(monkeypatch, OFF_TOPIC_VERDICT)

    result = retrieval.answer_question(conn, "What shoe size is Messi?", superseded_filter=True)

    assert result["abstained"] is True
    assert result["off_topic"] is True
    assert "a footballer's shoe size" in result["reason"]
    assert "out of scope" in result["reason"]
    assert result["suggested_questions"] == [
        "What are the requirements for licensing a healthcare professional in Dubai?",
        "How long is a DHA license valid?",
    ]
    assert "retrieved_chunks" not in result  # no sources leak into an off-topic stop
    pipeline_env.assert_not_called()  # generation never reached
    assert chat_spy.call_args.kwargs["max_tokens"] == guardrail.GUARDRAIL_MAX_TOKENS


def test_relevant_verdict_proceeds_to_generation(conn, redis_conn, monkeypatch):
    seed_official_doc(conn, score=0.7)
    monkeypatch.setattr(retrieval, "embed", lambda text: QUERY_VEC)
    gen = MagicMock(return_value=("Stub answer text.", retrieval.CHAT_MODEL))
    monkeypatch.setattr(retrieval, "generate_answer", gen)
    _mock_chat_completion(monkeypatch, RELEVANT_VERDICT)

    result = retrieval.answer_question(conn, "What are the telehealth standards?", superseded_filter=False)

    assert result["abstained"] is False
    gen.assert_called_once()


def test_guardrail_failure_fails_open(conn, redis_conn, monkeypatch):
    """LM Studio down / timeout / no fallback: the question still gets answered via
    the numeric path instead of erroring or abstaining."""
    seed_official_doc(conn, score=0.7)
    monkeypatch.setattr(retrieval, "embed", lambda text: QUERY_VEC)
    gen = MagicMock(return_value=("Stub answer text.", retrieval.CHAT_MODEL))
    monkeypatch.setattr(retrieval, "generate_answer", gen)
    monkeypatch.setattr(retrieval, "chat_completion", MagicMock(side_effect=RuntimeError("boom")))

    result = retrieval.answer_question(conn, "What are the telehealth standards?", superseded_filter=False)

    assert result["abstained"] is False
    gen.assert_called_once()


def test_off_topic_abstains_in_stream(conn, redis_conn, monkeypatch):
    seed_official_doc(conn, score=0.7)
    monkeypatch.setattr(retrieval, "embed", lambda text: QUERY_VEC)
    gen = _mock_generate_answer(monkeypatch)
    _mock_chat_completion(monkeypatch, OFF_TOPIC_VERDICT)

    events = list(retrieval.answer_question_stream(conn, "What shoe size is Messi?", superseded_filter=True))
    steps = [e["step"] for e in events]

    assert "checking_relevance" in steps
    done = events[-1]
    assert done["step"] == "done"
    assert done["result"]["abstained"] is True
    assert done["result"]["off_topic"] is True
    assert "out of scope" in done["result"]["reason"]
    assert len(done["result"]["suggested_questions"]) == 2
    assert "answer_delta" not in steps
    gen.assert_not_called()


def test_guardrail_always_uses_gpt_even_for_local_provider(conn, redis_conn, monkeypatch):
    """Judge quality beats provider parity: a locally-generated answer is still
    gated by the OpenAI->NaraRouter chain, never the local model -- measured live,
    local qwen 4B misjudged specific operational questions that gpt-4o-mini accepted
    with identical retrieved evidence."""
    seed_official_doc(conn, score=0.7)
    monkeypatch.setattr(retrieval, "embed", lambda text: QUERY_VEC)
    gen = MagicMock(return_value=("Stub answer text.", "test-local-model"))
    monkeypatch.setattr(retrieval, "generate_answer", gen)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=RELEVANT_VERDICT))]
    chat_spy = MagicMock(return_value=(mock_response, "gpt-4o-mini"))
    monkeypatch.setattr(retrieval, "chat_completion", chat_spy)
    fake_local = MagicMock()
    fake_local.chat.completions.create.side_effect = AssertionError(
        "the local model must never judge; generation is its only role here")
    monkeypatch.setattr(retrieval, "local_client", fake_local)

    result = retrieval.answer_question(
        conn, "What are the telehealth standards?", superseded_filter=False,
        provider="local", model="test-local-model",
    )

    assert result["abstained"] is False
    chat_spy.assert_called_once()  # the guardrail verdict
    fake_local.chat.completions.create.assert_not_called()
    gen.assert_called_once()
