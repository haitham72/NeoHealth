"""Tests for the small-talk router (app.core.conversation) and its wiring into
answer_question / answer_question_stream.

The matching tests are the cheap half. The half that matters is the false-positive
suite: this router sits in front of the entire retrieval pipeline, so a phrase that
swallows a real regulation question doesn't degrade an answer, it replaces it with
"Hello." Every guard in the module (whole-string anchored match, word ceiling) exists
for that, and is asserted here.

No DB, no network: the router runs above the cache probe, so it never touches the
connection -- which these tests prove by passing a connection that raises on any use.
"""
from unittest.mock import MagicMock

import pytest

from app.core import conversation, retrieval


@pytest.fixture
def hostile_conn():
    """A connection that fails loudly on any use at all. Small talk must never reach
    Postgres -- not for the cache probe, not for suggestions, not for anything."""
    conn = MagicMock()
    conn.cursor.side_effect = AssertionError("small talk must not touch the database")
    conn.commit.side_effect = AssertionError("small talk must not touch the database")
    return conn


# --- matching --------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("hi", "greeting"),
        ("Hello!", "greeting"),
        ("HEY", "greeting"),
        ("Good morning", "greeting"),
        ("assalamu alaikum", "greeting"),
        ("marhaba", "greeting"),
        ("hi how are you", "greeting"),
        ("how are you doing?", "greeting"),
        ("what's up", "greeting"),
        ("thanks", "thanks"),
        ("Thank you!", "thanks"),
        ("shukran", "thanks"),
        ("bye", "farewell"),
        ("see you later", "farewell"),
        ("what is this", "about"),
        ("What is this app?", "about"),
        ("what do you do", "about"),
        ("what can you answer", "capability"),
        ("What topics do you cover?", "capability"),
        ("what model are you", "model"),
        ("which llm", "model"),
        ("are you chatgpt?", "model"),
        ("help", "help"),
        ("How does this work?", "help"),
        ("who are you", "identity"),
        ("Who built this?", "identity"),
        ("ok", "affirmation"),
        ("Got it.", "affirmation"),
    ],
)
def test_sample_inputs_route_to_expected_intent(text, expected):
    assert conversation.match_intent(text) == expected


def test_every_intent_has_at_least_one_routing_phrase():
    """Guards against an intent being defined but unreachable (e.g. every one of its
    phrases colliding with another intent's, or being normalized away)."""
    reached = {conversation.match_intent(p) for spec in conversation.INTENTS.values() for p in spec["phrases"]}
    assert reached == set(conversation.INTENTS)


def test_punctuation_diacritics_and_spacing_are_normalized_away():
    assert conversation.normalize("  Hi!!  ") == "hi"
    assert conversation.normalize("Thank you.") == "thank you"
    assert conversation.match_intent("...hello???") == "greeting"


# --- false positives: the part that actually matters -----------------------------


@pytest.mark.parametrize(
    "question",
    [
        # Each of these CONTAINS a routable phrase. None may route: the match is
        # anchored to the whole normalized string, not a substring.
        "hi, what are DHA licensing fees?",
        "what is this document about?",
        "who are you required to notify?",
        "help with facility inspection requirements",
        "hello, how long is a DHA licence valid?",
        "what model are you required to use for risk scoring?",
        "thanks -- and what about the renewal window?",
        "ok so which authority licenses a home healthcare provider?",
        "what can you do if an inspection finds a breach?",
        "what is this standard's effective date?",
        "who built this facility's accreditation requirement?",
        "bye-laws for private health facilities in Dubai",
        "how are you licensed to practice as a nurse?",
    ],
)
def test_real_questions_never_route(question):
    assert conversation.match_intent(question) is None


def test_word_ceiling_blocks_long_inputs():
    over = " ".join(["hi"] * (conversation.MAX_SMALLTALK_WORDS + 1))
    assert conversation.match_intent(over) is None
    # And the ceiling is checked before the phrase lookup, so no long input can route
    # regardless of content.
    assert conversation.match_intent("thanks " * 20) is None


def test_blank_input_does_not_route():
    assert conversation.match_intent("") is None
    assert conversation.match_intent("   ") is None
    assert conversation.route("") is None


# --- response bank ---------------------------------------------------------------


def test_model_response_is_composed_from_config_not_hardcoded():
    """The honest answer is the whole fallback chain, and it has to be read from the
    constants so it can never drift from what chat_completion() actually does."""
    text = conversation.response_for("model")
    assert retrieval.CHAT_MODEL in text
    assert retrieval.NARAROUTER_MODEL in text
    assert retrieval.DEFAULT_LOCAL_MODEL in text
    assert retrieval.EMBED_MODEL in text


def test_model_response_tracks_a_changed_constant(monkeypatch):
    monkeypatch.setattr(retrieval, "CHAT_MODEL", "gpt-test-9")
    assert "gpt-test-9" in conversation.response_for("model")


def test_every_intent_has_fixed_nonempty_text_and_starters():
    for intent in conversation.INTENTS:
        assert conversation.response_for(intent).strip()
        assert conversation.INTENTS[intent]["starters"]


# --- result shape and pipeline wiring --------------------------------------------


def test_route_returns_additive_abstention_shape():
    result = conversation.route("hi")
    assert result["abstained"] is True
    assert result["smalltalk"] is True
    assert result["intent"] == "greeting"
    assert result["reason"] == conversation.response_for("greeting")
    assert [s["question"] for s in result["suggested_followups"]] == conversation.DEFAULT_STARTERS
    # No retrieval keys leak into a conversational reply.
    assert "retrieved_chunks" not in result
    assert "document" not in result


def test_answer_question_routes_small_talk_with_no_db_embedding_or_llm(hostile_conn, monkeypatch):
    embed_spy = MagicMock(side_effect=AssertionError("small talk must not embed"))
    gen_spy = MagicMock(side_effect=AssertionError("small talk must not generate"))
    chat_spy = MagicMock(side_effect=AssertionError("small talk must not call an LLM"))
    lookup_spy = MagicMock(side_effect=AssertionError("small talk must not probe the cache"))
    store_spy = MagicMock(side_effect=AssertionError("small talk must not enter the cache"))
    monkeypatch.setattr(retrieval, "embed", embed_spy)
    monkeypatch.setattr(retrieval, "generate_answer", gen_spy)
    monkeypatch.setattr(retrieval, "chat_completion", chat_spy)
    monkeypatch.setattr(retrieval, "lookup_answer_cache", lookup_spy)
    monkeypatch.setattr(retrieval, "store_answer_cache", store_spy)

    result = retrieval.answer_question(hostile_conn, "hi", superseded_filter=True)

    assert result["smalltalk"] is True
    assert result["intent"] == "greeting"
    embed_spy.assert_not_called()
    gen_spy.assert_not_called()
    chat_spy.assert_not_called()
    lookup_spy.assert_not_called()
    store_spy.assert_not_called()


def test_stream_yields_a_single_done_event_for_small_talk(hostile_conn, monkeypatch):
    monkeypatch.setattr(retrieval, "embed", MagicMock(side_effect=AssertionError("must not embed")))

    events = list(retrieval.answer_question_stream(hostile_conn, "thanks!", superseded_filter=True))

    assert [e["step"] for e in events] == ["done"]
    assert events[0]["result"]["intent"] == "thanks"
    # The trace showing no embedding/search/relevance steps is the user-visible proof
    # that a pleasantry cost nothing.
    assert events[0]["result"]["abstained"] is True


def test_real_question_still_falls_through_to_the_pipeline(hostile_conn, monkeypatch):
    """The anchored match must not swallow a greeting-prefixed real question: this one
    has to reach embed(), which is as far as it gets here before the fake conn stops it."""
    reached = MagicMock(side_effect=RuntimeError("reached the real pipeline"))
    monkeypatch.setattr(retrieval, "embed", reached)
    monkeypatch.setattr(retrieval, "lookup_answer_cache", MagicMock(return_value=None))

    with pytest.raises(RuntimeError, match="reached the real pipeline"):
        retrieval.answer_question(hostile_conn, "hi, what are the DHA licensing fees?", superseded_filter=True)
