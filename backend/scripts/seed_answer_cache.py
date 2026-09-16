"""One-time, manually-run script that pre-bakes a known set of demo questions into
the answer cache (and, for entries that ask for it, the diff-followup cache) ahead of
an interview -- see Task 7 of the semantic-answer-cache plan.

Why: the interview's realistic question universe is small and knowable in advance (on
the order of ten phrasings). Rather than relying on the *first* live ask of each one
being a slow cache miss, this script runs the real pipeline for every question once,
so the very first request in a live interview is already a cache hit. Postgres gets
"padded with everything," and the next boot's warm-load (app/core/cache_warmup.py)
carries all of it into Redis automatically -- and store_answer_cache/store_diff_cache
also write Redis immediately as part of a normal store, so Redis is warm right after
this script runs too, even before any reboot.

Config: scripts/demo_questions.json -- a flat list of
    {"question": str, "superseded_filter": bool, "authority_filter": str|null,
     "diff_followup": bool}
edited by a human, not this script. It ships with exactly one real entry (the
question already used in cli/demo.py's QUESTION constant) as a worked example; add
the rest of the real demo list before running this for real. `diff_followup: true`
means: after seeding the /ask answer, also pre-bake a diff_cache row comparing the
cited document against its previous version (so "compare with last year" is warm
too), by resolving that pair and running the same explanation-generation logic
/diff-followup uses -- reusing it directly (see _run_diff_followup below) rather than
duplicating its prompt text.

Never run this in CI or on deploy: every entry makes at least one real, billed OpenAI
call (an embedding, plus a chat completion unless the question is already cached from
a previous run), and a diff_followup entry makes one more call if the document has a
previous version to compare against.

Run from backend/ with the venv active:
    python -m scripts.seed_answer_cache
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from starlette.requests import Request

from app.api.routers.diff import diff_followup
from app.api.schemas.diff import DiffFollowupRequest
from app.core.db import get_connection, release_connection
from app.core.retrieval import answer_question

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "demo_questions.json"

# Outcome labels returned by seed_one() -- kept as plain strings rather than an enum
# since this is a one-shot script, not a library (see the task's "don't over-engineer
# a script into abstractions" guidance).
SEEDED = "seeded"                # /ask answer cached; no diff_followup requested for this entry
SEEDED_WITH_DIFF = "seeded_with_diff"  # /ask answer cached AND a diff_cache row seeded
ABSTAINED = "abstained"          # answer_question() abstained; nothing to cache
DIFF_UNAVAILABLE = "diff_unavailable"  # /ask cached, but diff_followup came back unavailable
                                        # (no previous version, or no indexed text for one side)


def _fake_request() -> Request:
    """A minimal real Request, so slowapi's @limiter.limit isinstance check on
    diff_followup's `request` argument passes when this script calls it directly as a
    plain function outside a real ASGI request. Mirrors tests/conftest.py's
    fake_request() exactly, but duplicated here on purpose rather than imported --
    that helper lives in test-only code, and a scripts/ module reaching back into
    tests/ for a production-adjacent (if manually-run) script would read oddly to a
    future maintainer, for a ~3-line helper that's trivial to keep in sync."""
    return Request(
        scope={
            "type": "http", "method": "GET", "path": "/",
            "query_string": b"", "headers": [], "client": ("seed-script", 0),
        }
    )


def load_questions(path: Path = CONFIG_PATH) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def _run_diff_followup(question: str, document: dict, cited_page: int) -> dict:
    """Seeds diff_cache for one already-answered question by calling the real
    /diff-followup route function directly (not over HTTP) -- it already does its own
    find_previous_version + cache-check + generation + store_diff_cache internally
    (see app/api/routers/diff.py), so this call itself IS the seeding action for the
    diff cache. cited_text is passed as "" since diff_followup() never reads that
    field (confirmed by reading its body) -- it's part of the request schema but
    unused in the route's logic."""
    req = DiffFollowupRequest(
        doc_code=document["doc_code"],
        current_document_id=document["id"],
        cited_text="",
        cited_page=cited_page,
        question=question,
    )
    return diff_followup(_fake_request(), req)


def seed_one(conn, entry: dict) -> str:
    """Runs the real pipeline for one demo_questions.json entry. Returns one of the
    outcome labels defined above."""
    question = entry["question"]
    superseded_filter = entry.get("superseded_filter", True)
    authority_filter = entry.get("authority_filter")

    result = answer_question(
        conn, question, superseded_filter=superseded_filter, authority_filter=authority_filter,
    )
    if result.get("abstained"):
        logger.info("ABSTAINED (%s): %r", result.get("reason"), question)
        return ABSTAINED

    # answer_question() already writes this exact result into answer_cache (Postgres +
    # Redis) itself, on every fresh, non-abstained, cache-eligible answer -- see
    # retrieval.py's answer_question(), the *only* call site of store_answer_cache
    # anywhere in this codebase (confirmed: no router or CLI calls it directly either).
    # So reaching this line already means the row is seeded; a second, explicit call to
    # store_answer_cache here would not be a harmless no-op -- confirmed empirically
    # against the local dev DB while building this script: answer_cache has no
    # uniqueness constraint on (question, filters) to upsert against the way diff_cache
    # does, so a second call inserts a second, fully duplicate row every single run.
    # On a cache-hit re-run (question already seeded from a previous run) an explicit
    # call would duplicate it too, since a hit returns before answer_question() reaches
    # its own store call. Either way, there is no scenario where an extra explicit call
    # here helps -- so, deliberately, there isn't one.
    logger.info("SEEDED /ask: %r", question)

    if not entry.get("diff_followup"):
        return SEEDED

    diff_result = _run_diff_followup(question, result["document"], result["page"])
    if not diff_result.get("available"):
        logger.info("DIFF UNAVAILABLE (%s): %r", diff_result.get("reason"), question)
        return DIFF_UNAVAILABLE

    # diff_followup() stores the diff_cache row itself too -- store_diff_cache's only
    # call site outside diff_cache.py's own tests. Calling diff_followup() directly
    # above already performed the seeding; nothing left to do here.
    logger.info("SEEDED diff_cache: %r", question)
    return SEEDED_WITH_DIFF


def run_all(conn, entries: list[dict]) -> dict[str, int]:
    """Runs seed_one() for every config entry against one connection and tallies
    outcomes. Split out from main() so this orchestration loop -- the part with actual
    logic worth unit-testing -- doesn't require a real DB connection or process exit,
    unlike main() itself (which just wires this to get_connection()/print())."""
    counts = {SEEDED: 0, SEEDED_WITH_DIFF: 0, ABSTAINED: 0, DIFF_UNAVAILABLE: 0}
    for entry in entries:
        outcome = seed_one(conn, entry)
        counts[outcome] += 1
    return counts


def print_summary(entries_count: int, counts: dict[str, int]) -> None:
    answers_seeded = counts[SEEDED] + counts[SEEDED_WITH_DIFF]
    print()
    print(f"Questions in config:        {entries_count}")
    print(f"Answers seeded:             {answers_seeded}")
    print(f"Diff-followups seeded:      {counts[SEEDED_WITH_DIFF]}")
    print(f"Skipped (abstained):        {counts[ABSTAINED]}")
    print(f"Skipped (diff unavailable): {counts[DIFF_UNAVAILABLE]}")


def main() -> None:
    entries = load_questions()
    conn = get_connection()
    try:
        counts = run_all(conn, entries)
    finally:
        release_connection(conn)
    print_summary(len(entries), counts)


if __name__ == "__main__":
    main()
