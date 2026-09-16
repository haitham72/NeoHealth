"""Supersession resolution, shared by every path that writes parsed_documents.json.

This lived inline in ingest.py, which meant it only ever ran over the documents
ingest.py itself parsed. apply_manual_fixes.py appended its hand-written entries
straight onto the list and wrote the file, so whatever `superseded` was hardcoded in
MANUAL_FIXES was final -- hand-added documents sat permanently *outside* supersession
resolution.

That is not a cosmetic gap. The "Exclude outdated regulations" filter only hides
documents flagged `superseded = true`, so a stale document that was never flagged is
served as current *with the filter on, in its default state* -- no red styling, no
ledger warning, because the system believes it is in force. A silent wrong answer
about which version applies is precisely the failure this product exists to prevent.

Sharing this function means a hand-added document that shares a `doc_code` with an
existing one now does the right thing automatically, instead of depending on someone
guessing the right flag by hand and keeping every sibling's flag in sync with it.
"""
from __future__ import annotations

import re


def _version_key(version) -> tuple[int, ...]:
    """Numeric-aware version ordering: "1.10" sorts above "1.3", which plain string
    comparison gets backwards. Anything unparseable sorts lowest rather than raising --
    a weird version string must not be able to break an ingestion run."""
    numbers = re.findall(r"\d+", str(version or ""))
    return tuple(int(n) for n in numbers) if numbers else (-1,)


def _sort_key(doc: dict) -> tuple:
    # effective_date is an ISO "YYYY-MM-DD" string throughout the pipeline, so plain
    # string ordering is chronological. A missing date sorts oldest: an undated
    # document should never win the "currently in force" slot from a dated one.
    return (doc.get("effective_date") or "", _version_key(doc.get("version")))


def resolve_supersession(docs: list[dict]) -> dict[str, list[dict]]:
    """Flags every document in `docs`: newest `effective_date` per `doc_code` is in
    force, all older ones are `superseded = true`. Mutates in place and returns the
    documents grouped by `doc_code` (each group newest-first), which callers use for
    reporting.

    Version is a tiebreak within an identical effective_date -- two versions of one
    regulation dated the same day is unusual but not impossible, and without the
    tiebreak which one is "current" would depend on the order files happened to be
    read off disk.

    Call this over the COMPLETE document list every time it is written, never over a
    subset: which document is current is a property of the whole group, so resolving
    over a partial list can leave a superseded sibling still flagged as in force."""
    by_code: dict[str, list[dict]] = {}
    for doc in docs:
        by_code.setdefault(doc["doc_code"], []).append(doc)

    for group in by_code.values():
        group.sort(key=_sort_key, reverse=True)
        for i, doc in enumerate(group):
            doc["superseded"] = i != 0

    return by_code
