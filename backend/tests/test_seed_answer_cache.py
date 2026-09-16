"""Tests for scripts.seed_answer_cache's per-entry orchestration logic.

Fully mocked at the module boundary (answer_question and diff_followup are patched on
the scripts.seed_answer_cache module, where they were imported by name) -- this
script's whole point is to make real, billed OpenAI calls and write real cache rows
when run for real, which is exactly what must NOT happen in the automated test suite.
`conn` is a plain sentinel object throughout: every function that would touch it is
mocked, so nothing here needs a real Postgres connection.

Note there is no store_answer_cache mock here: the script deliberately never calls it
directly (see seed_one()'s comment) -- answer_question() already writes the
answer_cache row itself as part of its own contract, and an extra explicit call from
this script was confirmed, empirically, to insert a second duplicate row every run
(answer_cache has no uniqueness constraint to upsert against the way diff_cache does).
Likewise store_diff_cache is never called directly -- diff_followup() already owns
that write.
"""
import pytest

from scripts import seed_answer_cache as seed


@pytest.fixture
def conn():
    """A connection is threaded through seed_one()/run_all() but every call that would
    actually use it (answer_question, diff_followup) is mocked in every test below --
    a sentinel object is enough to prove it's the same value being passed through,
    without needing a real database."""
    return object()


def _fake_answer(**overrides) -> dict:
    result = {
        "abstained": False,
        "answer": "Some answer.",
        "document": {"id": 7, "doc_code": "DHA/HRS/HPSD/ST-14"},
        "page": 12,
    }
    result.update(overrides)
    return result


# --- seed_one(): normal seed, no diff_followup requested --------------------------

def test_seed_one_normal_seed_no_diff_followup(conn, monkeypatch):
    answer_result = _fake_answer()
    answer_question_calls = []

    monkeypatch.setattr(seed, "answer_question", lambda c, q, superseded_filter, authority_filter: (
        answer_question_calls.append((c, q, superseded_filter, authority_filter)) or answer_result
    ))
    monkeypatch.setattr(seed, "diff_followup", lambda *a, **kw: pytest.fail("diff_followup must not be called"))

    entry = {
        "question": "What happens if a license application is rejected?",
        "superseded_filter": True,
        "authority_filter": None,
        "diff_followup": False,
    }

    outcome = seed.seed_one(conn, entry)

    assert outcome == seed.SEEDED
    assert answer_question_calls == [(conn, entry["question"], True, None)]


# --- seed_one(): abstained answers are skipped, never stored -----------------------

def test_seed_one_abstained_is_skipped(conn, monkeypatch):
    monkeypatch.setattr(seed, "answer_question", lambda *a, **kw: {
        "abstained": True, "reason": "below retrieval confidence threshold",
    })
    monkeypatch.setattr(seed, "diff_followup", lambda *a, **kw: pytest.fail("diff_followup must not be called on abstain"))

    entry = {"question": "Some obscure question", "superseded_filter": True, "authority_filter": None, "diff_followup": True}

    outcome = seed.seed_one(conn, entry)

    assert outcome == seed.ABSTAINED


# --- seed_one(): diff_followup: false entries never attempt a diff seed -----------

def test_seed_one_diff_followup_false_skips_diff_entirely(conn, monkeypatch):
    monkeypatch.setattr(seed, "answer_question", lambda *a, **kw: _fake_answer())
    monkeypatch.setattr(seed, "diff_followup", lambda *a, **kw: pytest.fail("diff_followup must not be called"))

    entry = {"question": "Q", "superseded_filter": False, "authority_filter": "DHA"}  # no diff_followup key at all

    outcome = seed.seed_one(conn, entry)

    assert outcome == seed.SEEDED


# --- seed_one(): diff_followup requested and available -> full diff seed ----------

def test_seed_one_diff_followup_available_seeds_diff(conn, monkeypatch):
    answer_result = _fake_answer()
    diff_calls = []

    monkeypatch.setattr(seed, "answer_question", lambda *a, **kw: answer_result)

    def _fake_diff_followup(request, req):
        diff_calls.append(req)
        return {"available": True, "explanation": "It changed."}

    monkeypatch.setattr(seed, "diff_followup", _fake_diff_followup)

    entry = {"question": "What changed?", "superseded_filter": True, "authority_filter": None, "diff_followup": True}

    outcome = seed.seed_one(conn, entry)

    assert outcome == seed.SEEDED_WITH_DIFF
    assert len(diff_calls) == 1
    req = diff_calls[0]
    assert req.doc_code == answer_result["document"]["doc_code"]
    assert req.current_document_id == answer_result["document"]["id"]
    assert req.cited_page == answer_result["page"]
    assert req.question == entry["question"]
    assert req.cited_text == ""


# --- seed_one(): diff_followup requested but unavailable -> counted as skip -------

def test_seed_one_diff_followup_unavailable_is_skip_not_error(conn, monkeypatch):
    """No previous version (or no indexed text) is a legitimate, expected outcome --
    must be reported as a skip, not raise or be conflated with an error."""
    monkeypatch.setattr(seed, "answer_question", lambda *a, **kw: _fake_answer())
    monkeypatch.setattr(seed, "diff_followup", lambda *a, **kw: {
        "available": False, "reason": "no earlier version of this document exists",
    })

    entry = {"question": "What changed?", "superseded_filter": True, "authority_filter": None, "diff_followup": True}

    outcome = seed.seed_one(conn, entry)

    assert outcome == seed.DIFF_UNAVAILABLE


# --- run_all(): tallies outcomes across multiple entries ---------------------------

def test_run_all_tallies_outcomes_across_entries(conn, monkeypatch):
    outcomes = iter([seed.SEEDED, seed.ABSTAINED, seed.SEEDED_WITH_DIFF, seed.DIFF_UNAVAILABLE])
    monkeypatch.setattr(seed, "seed_one", lambda c, e: next(outcomes))

    entries = [{"question": f"q{i}"} for i in range(4)]

    counts = seed.run_all(conn, entries)

    assert counts == {
        seed.SEEDED: 1,
        seed.SEEDED_WITH_DIFF: 1,
        seed.ABSTAINED: 1,
        seed.DIFF_UNAVAILABLE: 1,
    }


def test_run_all_empty_entries_returns_zero_counts(conn):
    counts = seed.run_all(conn, [])

    assert counts == {seed.SEEDED: 0, seed.SEEDED_WITH_DIFF: 0, seed.ABSTAINED: 0, seed.DIFF_UNAVAILABLE: 0}


# --- load_questions(): the shipped config is valid and matches cli/demo.py's QUESTION

def test_shipped_config_has_the_real_demo_question():
    from cli.demo import QUESTION

    entries = seed.load_questions()

    assert len(entries) >= 1
    assert entries[0]["question"] == QUESTION
    assert entries[0]["diff_followup"] is True
