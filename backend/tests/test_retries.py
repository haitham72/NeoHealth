"""Tests for the tenacity retry layer around the OpenAI attempt (Part C).

The whole risk in this change is that a retry fights the fallback that was already
there. It must not: a retry is for a blip that a second attempt fixes, the
NaraRouter fallback is for OpenAI being unavailable for a while, and the retry sits
strictly inside the existing try/except so the fallback's behaviour -- including the
60s cooldown and the per-IP soft cap -- is byte-for-byte what it was. So these tests
are mostly about what must NOT change: one _mark_openai_degraded() call, not three;
no retry at all on errors that can never succeed; NaraRouter itself never retried.

No DB and no network.
"""
import httpx
import pytest
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)
from tenacity import wait_none
from unittest.mock import MagicMock

from app.core import retrieval


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _response(status: int) -> httpx.Response:
    return httpx.Response(status_code=status, request=_request())


def rate_limit_error() -> RateLimitError:
    return RateLimitError("rate limited", response=_response(429), body=None)


def server_error(status: int = 503) -> APIStatusError:
    return APIStatusError("upstream down", response=_response(status), body=None)


def auth_error() -> AuthenticationError:
    return AuthenticationError("bad key", response=_response(401), body=None)


def bad_request_error() -> BadRequestError:
    return BadRequestError("nope", response=_response(400), body=None)


@pytest.fixture(autouse=True)
def _fast_retries_and_clean_cooldown(monkeypatch):
    """Retries really do sleep, so the waits are zeroed here -- the wait policy's
    actual values are asserted separately below rather than by stopwatch. Also clears
    the failure cooldown, which is module-global state that would otherwise leak
    between tests (and into the rest of the suite)."""
    monkeypatch.setattr(retrieval._openai_chat_create.retry, "wait", wait_none())
    monkeypatch.setattr(retrieval._openai_embed_create.retry, "wait", wait_none())
    monkeypatch.setattr(retrieval, "_openai_degraded_until", 0.0)
    yield
    retrieval._openai_degraded_until = 0.0


@pytest.fixture
def degraded_spy(monkeypatch):
    """Counts _mark_openai_degraded() calls while still performing the real mark."""
    real = retrieval._mark_openai_degraded
    spy = MagicMock(side_effect=real)
    monkeypatch.setattr(retrieval, "_mark_openai_degraded", spy)
    return spy


def _fake_openai(monkeypatch, *side_effects):
    """Installs a fake OpenAI client whose chat.completions.create walks through
    `side_effects` (exceptions are raised, anything else returned)."""
    fake = MagicMock()
    fake.chat.completions.create.side_effect = list(side_effects)
    monkeypatch.setattr(retrieval, "client", fake)
    return fake.chat.completions.create


def _fake_nararouter(monkeypatch):
    fake = MagicMock()
    fake.chat.completions.create.return_value = MagicMock(name="nara-response")
    monkeypatch.setattr(retrieval, "nararouter_client", fake)
    return fake.chat.completions.create


# --- what the retry is for -------------------------------------------------------


@pytest.mark.parametrize("error", [rate_limit_error(), server_error(503), server_error(500)])
def test_transient_error_retries_then_succeeds_without_degrading(monkeypatch, degraded_spy, error):
    ok = MagicMock(name="openai-response")
    create = _fake_openai(monkeypatch, error, ok)
    nara = _fake_nararouter(monkeypatch)

    resp, model = retrieval.chat_completion([{"role": "user", "content": "hi"}])

    assert resp is ok
    assert model == retrieval.CHAT_MODEL
    assert create.call_count == 2
    # The blip never reached the fallback, so no cooldown was tripped: the next
    # question still tries OpenAI first.
    degraded_spy.assert_not_called()
    assert retrieval._openai_is_degraded() is False
    nara.assert_not_called()


def test_connection_and_timeout_errors_are_retried(monkeypatch, degraded_spy):
    ok = MagicMock(name="openai-response")
    create = _fake_openai(
        monkeypatch, APIConnectionError(request=_request()), APITimeoutError(_request()), ok)
    _fake_nararouter(monkeypatch)

    # Three attempts total is the cap, so two transient failures is exactly enough to
    # still succeed on the third.
    resp, _ = retrieval.chat_completion([{"role": "user", "content": "hi"}])

    assert resp is ok
    assert create.call_count == retrieval.OPENAI_RETRY_ATTEMPTS
    degraded_spy.assert_not_called()


# --- what the retry must never do ------------------------------------------------


@pytest.mark.parametrize("error", [auth_error(), bad_request_error()])
def test_permanent_errors_are_not_retried_and_fall_straight_to_nararouter(
    monkeypatch, degraded_spy, error
):
    """A bad key or a malformed request will never succeed on a second attempt;
    retrying only adds latency in front of an inevitable fallback."""
    create = _fake_openai(monkeypatch, error)
    nara = _fake_nararouter(monkeypatch)

    resp, model = retrieval.chat_completion([{"role": "user", "content": "hi"}])

    assert create.call_count == 1  # no retry
    assert model == retrieval.NARAROUTER_MODEL
    assert resp is nara.return_value
    degraded_spy.assert_called_once()


def test_exhausted_retries_hit_the_existing_fallback_exactly_once(monkeypatch, degraded_spy):
    create = _fake_openai(monkeypatch, rate_limit_error(), rate_limit_error(), rate_limit_error())
    nara = _fake_nararouter(monkeypatch)

    resp, model = retrieval.chat_completion([{"role": "user", "content": "hi"}])

    assert create.call_count == retrieval.OPENAI_RETRY_ATTEMPTS
    assert model == retrieval.NARAROUTER_MODEL
    assert resp is nara.return_value
    # One cooldown for the whole episode -- not one per attempt.
    degraded_spy.assert_called_once()
    assert retrieval._openai_is_degraded() is True


def test_nararouter_is_never_retried(monkeypatch, degraded_spy):
    """The retry wraps the OpenAI attempt only. A NaraRouter failure is the end of the
    chain and must surface, not multiply."""
    _fake_openai(monkeypatch, auth_error())
    fake = MagicMock()
    fake.chat.completions.create.side_effect = RuntimeError("nara down")
    monkeypatch.setattr(retrieval, "nararouter_client", fake)

    with pytest.raises(RuntimeError, match="nara down"):
        retrieval.chat_completion([{"role": "user", "content": "hi"}])

    assert fake.chat.completions.create.call_count == 1


def test_degraded_cooldown_skips_openai_entirely(monkeypatch, degraded_spy):
    """Retries must not resurrect a call the cooldown already decided to skip."""
    create = _fake_openai(monkeypatch, rate_limit_error())
    nara = _fake_nararouter(monkeypatch)
    retrieval._mark_openai_degraded()
    degraded_spy.reset_mock()

    _, model = retrieval.chat_completion([{"role": "user", "content": "hi"}])

    assert model == retrieval.NARAROUTER_MODEL
    create.assert_not_called()
    nara.assert_called_once()


# --- embeddings ------------------------------------------------------------------


def test_embed_retries_transient_failures(monkeypatch):
    """Embeddings have no second provider (NaraRouter has none, and the corpus is
    embedded at 1536-dim), so a transient failure here would fail the whole question."""
    vec = [0.1] * 1536
    ok = MagicMock()
    ok.data = [MagicMock(embedding=vec)]
    fake = MagicMock()
    fake.embeddings.create.side_effect = [rate_limit_error(), ok]
    monkeypatch.setattr(retrieval, "client", fake)

    assert retrieval.embed("a question") == vec
    assert fake.embeddings.create.call_count == 2


def test_embed_does_not_retry_permanent_failures(monkeypatch):
    fake = MagicMock()
    fake.embeddings.create.side_effect = auth_error()
    monkeypatch.setattr(retrieval, "client", fake)

    with pytest.raises(AuthenticationError):
        retrieval.embed("a question")
    assert fake.embeddings.create.call_count == 1


# --- the latency budget ----------------------------------------------------------


def test_retry_latency_stays_inside_the_heartbeat_budget():
    """Added latency must stay under the ~5s budget, which is itself under the 10s SSE
    heartbeat cadence -- otherwise a retried call looks like a dead connection to the
    frontend. Asserted against the configured policy (the autouse fixture zeroes only
    the per-function copy, so this still reads the real values)."""
    waits = [
        retrieval.OPENAI_RETRY_WAIT(MagicMock(attempt_number=n))
        for n in range(1, retrieval.OPENAI_RETRY_ATTEMPTS)
    ]

    assert waits == [1.0, 2.0]  # exponential, bounded at min=1s / max=4s
    assert sum(waits) <= 5.0
    assert sum(waits) < retrieval.STREAM_HEARTBEAT_SECONDS
