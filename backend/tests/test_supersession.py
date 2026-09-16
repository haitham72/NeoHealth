"""Tests for shared supersession resolution (ingestion.supersession).

The bug this guards against is silent by construction: the "Exclude outdated
regulations" filter hides documents *flagged* superseded, so a stale document that was
never flagged is served as current with the filter on, with no red styling and no
ledger warning -- the system believes it is in force. Before this was shared,
apply_manual_fixes.py appended hand-written entries without ever re-resolving, so any
hardcoded `superseded: False` was final.

No DB, no PDF parsing: this is pure list logic over the same dicts that go into
parsed_documents.json.
"""
from ingestion.supersession import resolve_supersession


def doc(code, version, date, superseded=False, **extra):
    return {"doc_code": code, "version": version, "effective_date": date,
            "superseded": superseded, **extra}


def test_newest_effective_date_wins_within_a_doc_code():
    docs = [
        doc("DHA/HRS/HLD/MA-2", "1.1", "2022-03-01"),
        doc("DHA/HRS/HLD/MA-2", "1.3", "2025-07-18"),
        doc("DHA/HRS/HLD/MA-2", "1.2", "2023-09-12"),
    ]

    resolve_supersession(docs)

    by_version = {d["version"]: d["superseded"] for d in docs}
    assert by_version == {"1.3": False, "1.2": True, "1.1": True}


def test_manual_doc_flips_an_older_existing_doc_to_superseded():
    """The whole point of sharing the resolver: apply_manual_fixes.py appends a
    hand-written entry and re-resolves, so the existing document it replaces stops
    being served as current -- without anyone having to remember to edit its flag."""
    existing = doc("DOH/STD/TELEHEALTH", "1", "2021-01-01", superseded=False)
    hand_added = doc("DOH/STD/TELEHEALTH", "2", "2025-06-01", superseded=False)

    resolve_supersession([existing, hand_added])

    assert existing["superseded"] is True
    assert hand_added["superseded"] is False


def test_a_hardcoded_superseded_flag_is_corrected_in_both_directions():
    """MANUAL_FIXES entries carry a hand-written `superseded`; resolution overrides it
    rather than trusting it -- a wrong guess in either direction is repaired."""
    wrongly_current = doc("X/Y/Z", "1", "2019-01-01", superseded=False)
    wrongly_superseded = doc("X/Y/Z", "2", "2024-01-01", superseded=True)

    resolve_supersession([wrongly_current, wrongly_superseded])

    assert wrongly_current["superseded"] is True
    assert wrongly_superseded["superseded"] is False


def test_unique_doc_codes_are_always_in_force():
    """Research papers (RESEARCH/ELHAYEK-0N) and every one-off document: alone in its
    group, so always current."""
    docs = [
        doc("RESEARCH/ELHAYEK-01", "1", "2020-06-16"),
        doc("RESEARCH/ELHAYEK-02", "1", "2020-09-23"),
        doc("MOHAP/HR/2018", "2", "2018-01-01"),
    ]

    resolve_supersession(docs)

    assert all(d["superseded"] is False for d in docs)


def test_version_breaks_an_effective_date_tie_numerically():
    """Two versions dated the same day would otherwise be resolved by whatever order
    the files came off disk. "1.10" is newer than "1.3", which string ordering gets
    backwards."""
    older = doc("A/B/C", "1.3", "2025-01-01")
    newer = doc("A/B/C", "1.10", "2025-01-01")

    resolve_supersession([older, newer])

    assert newer["superseded"] is False
    assert older["superseded"] is True


def test_missing_effective_date_never_outranks_a_dated_document():
    undated = doc("A/B/C", "2", None)
    dated = doc("A/B/C", "1", "2020-01-01")

    resolve_supersession([undated, dated])

    assert dated["superseded"] is False
    assert undated["superseded"] is True


def test_unparseable_version_does_not_raise():
    docs = [doc("A/B/C", "draft", "2025-01-01"), doc("A/B/C", None, "2025-01-01")]

    resolve_supersession(docs)

    assert sum(1 for d in docs if not d["superseded"]) == 1


def test_returns_groups_newest_first_for_reporting():
    docs = [doc("A/B/C", "1", "2020-01-01"), doc("A/B/C", "2", "2024-01-01"), doc("D/E/F", "1", "2021-01-01")]

    by_code = resolve_supersession(docs)

    assert set(by_code) == {"A/B/C", "D/E/F"}
    assert [d["version"] for d in by_code["A/B/C"]] == ["2", "1"]


def test_the_ma2_milestone_still_resolves_as_documented():
    """ingest.py's own milestone check: three DHA/HRS/HLD/MA-2 versions, with the
    July 2025 one in force. This is the regression that would surface first if a new
    doc_code ever collided with it."""
    docs = [
        doc("DHA/HRS/HLD/MA-2", "1", "2017-03-15"),
        doc("DHA/HRS/HLD/MA-2", "1.2", "2021-11-01"),
        doc("DHA/HRS/HLD/MA-2", "1.3", "2025-07-18"),
    ]

    resolve_supersession(docs)

    in_force = [d for d in docs if not d["superseded"]]
    assert len(in_force) == 1
    assert in_force[0]["version"] == "1.3"
    assert in_force[0]["effective_date"] == "2025-07-18"
