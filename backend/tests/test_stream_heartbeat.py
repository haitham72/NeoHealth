"""Stream keep-alive during blocking (non-streaming) generations.

Local LM Studio generation and the NaraRouter fallback are single blocking calls
with no token stream. Before heartbeats, the SSE response was silent for the whole
call: anything slower than the frontend's 60s idle timeout looked like an eternal
spinner even though LM Studio was still working (reproduced live: a 95s local call
followed by a 400). answer_question_stream now yields {"step": "heartbeat"} frames
while the call runs on a worker thread; the /ask-stream router turns them into SSE
comment lines so client parsers ignore them but idle timers/proxies stay alive.
"""
import time
from unittest.mock import MagicMock

import pytest

from app.core import retrieval
from tests.conftest import QUERY_VEC, seed_official_doc

RELEVANT_VERDICT = "VERDICT: RELEVANT\nSUBJECT: t\nREDIRECT: general\nSUGGESTIONS: none"


@pytest.fixture(autouse=True)
def _clean_stream_state(conn):
    """Committed answer_cache rows from other test files would turn these asks into
    cache hits before generation is ever reached; truncate around each test (same
    rationale as test_guardrail.py's fixture)."""
    def _reset():
        with conn.cursor() as cur:
            cur.execute("TRUNCATE answer_cache, documents RESTART IDENTITY CASCADE")
        conn.commit()

    _reset()
    yield
    _reset()


def _mock_relevant_guardrail(monkeypatch):
    """provider='local' routes BOTH the guardrail and generation through
    local_client, so the guardrail's call must be faked here too -- otherwise the
    test leaks out to a real LM Studio on :1234 (slow, flaky, and it would decide
    the verdict instead of the test)."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=RELEVANT_VERDICT))]
    fake_local = MagicMock()
    fake_local.chat.completions.create.return_value = mock_response
    monkeypatch.setattr(retrieval, "local_client", fake_local)
    monkeypatch.setattr(retrieval, "chat_completion",
                        MagicMock(return_value=(mock_response, "gpt-4o-mini")))


def test_local_generation_emits_heartbeats_until_done(conn, redis_conn, monkeypatch):
    seed_official_doc(conn, score=0.7)
    monkeypatch.setattr(retrieval, "embed", lambda text: QUERY_VEC)
    monkeypatch.setattr(retrieval, "STREAM_HEARTBEAT_SECONDS", 0.05)

    def _slow_generate_answer(*args, **kwargs):
        time.sleep(0.22)  # several heartbeat intervals
        return ("Stub local answer.", "test-local-model")

    monkeypatch.setattr(retrieval, "generate_answer", _slow_generate_answer)
    _mock_relevant_guardrail(monkeypatch)

    events = list(retrieval.answer_question_stream(
        conn, "What are the telehealth standards?", superseded_filter=False, provider="local"))

    steps = [e["step"] for e in events]
    assert steps.count("heartbeat") >= 1
    assert steps.index("heartbeat") < steps.index("done")
    assert events[-1]["step"] == "done"
    assert events[-1]["result"]["answer"] == "Stub local answer."
    assert events[-1]["result"]["model_used"] == "test-local-model"


def test_generation_failure_still_propagates_through_heartbeats(conn, redis_conn, monkeypatch):
    """Heartbeats must change liveness only -- an LM Studio failure has to reach the
    router's except-block exactly as the plain blocking call's exception would."""
    seed_official_doc(conn, score=0.7)
    monkeypatch.setattr(retrieval, "embed", lambda text: QUERY_VEC)
    monkeypatch.setattr(retrieval, "STREAM_HEARTBEAT_SECONDS", 0.05)

    def _boom(*args, **kwargs):
        time.sleep(0.12)
        raise RuntimeError("Model unloaded.")

    monkeypatch.setattr(retrieval, "generate_answer", _boom)
    _mock_relevant_guardrail(monkeypatch)

    raised = None
    try:
        for _ in retrieval.answer_question_stream(
            conn, "What are the telehealth standards?", superseded_filter=False, provider="local"
        ):
            pass
    except RuntimeError as e:
        raised = e
    assert raised is not None and "Model unloaded." in str(raised)
