"""
Clause-level diff between two versions of the same regulation document.

Read-only prototype, isolated from the live demo pipeline: reads full per-page
text straight from parsed_documents.json (produced by ingest.py) -- never
touches Postgres, retrieval.py, api.py's existing routes, or demo.py.

Rationale: ReguLense's `superseded` flag is document-level, all-or-nothing. It
can tell you a whole document is outdated, but not which specific clause
changed. Most of any manual's text is identical across versions (shared
boilerplate); this tool finds the clauses that actually differ, using the
manual's own X.Y./X.Y.Z. numbering as the natural clause boundary -- a plain
line-by-line diff is too noisy here because pdfplumber's line wrapping shifts
by a word or two between re-typeset versions even when the sentence is
unchanged.

Usage (from backend/):
  python -m cli.diff_versions "DHA/HRS/HLD/MA-2"
  python -m cli.diff_versions "DHA/HRS/HLD/MA-2" --versions 1.2 1.3
"""
import argparse
import difflib
import json
import re

from app.core.config import PARSED_DOCUMENTS_FILE

# Matches manual clause numbering like "5.1.", "10.14.1.", "13.10.2." at the
# start of a sentence -- these are the natural clause boundaries in DHA/DoH
# manuals, more reliable than PDF line breaks (which wrap mid-sentence and
# shift between re-typeset versions even when the content is unchanged).
CLAUSE_RE = re.compile(r"(?<!\d)(\d{1,3}\.\d{1,3}(?:\.\d{1,3})?\.)\s")

MIN_CLAUSE_LEN = 25  # drop fragments too short to be a real clause (noise floor)

# The repeating page footer ("Manual for Licensing Healthcare Professionals Code:
# DHA/HRS/HLD/MA-2 Issue Nu: version 1.3 Issue Date: 18/07/2025 Effective Date:
# 18/07/2025 Revision Date: 18/07/2030 Page 11 of 54") glues onto whatever clause
# ends near a page break. Its date/page-count fields differ by version even when
# the adjacent clause is identical, which was producing false "changed" hits on
# every clause near a page boundary. Strip it before splitting into clauses.
FOOTER_RE = re.compile(
    r"Manual for Licensing [\w /]+ Code:\s*[\w/-]+\s*Issue Nu:\s*version\s*[\d.]+\s*"
    r"Issue Date:\s*[\d/]+\s*Effective Date:\s*[\d/]+\s*Revision Date:\s*[\d/]+\s*Page\s*\d+\s*of\s*\d+"
)


def load_versions(doc_code: str) -> list[dict]:
    docs = json.loads(PARSED_DOCUMENTS_FILE.read_text(encoding="utf-8"))
    matches = [d for d in docs if d["doc_code"] == doc_code]
    if not matches:
        raise SystemExit(f"No documents found for doc_code={doc_code!r} in {PARSED_DOCUMENTS_FILE.name}")
    return sorted(matches, key=lambda d: d["effective_date"])


def page_text_normalized(pages: list[str]) -> list[str]:
    """One normalized (whitespace-collapsed) string per page -- undoes PDF line
    wrapping so clause boundaries aren't split across newlines, and strips the
    per-page footer so it doesn't corrupt whichever clause sits at a page break."""
    out = []
    for p in pages:
        collapsed = re.sub(r"\s+", " ", p).strip()
        out.append(FOOTER_RE.sub("", collapsed).strip())
    return out


def split_clauses(pages_norm: list[str]) -> list[tuple[str, int]]:
    """Split a document's normalized pages into (clause_text, page_number) pairs,
    using CLAUSE_RE as the boundary. page_number is 1-indexed, matching what the
    UI/citation block already shows elsewhere in ReguLense."""
    clauses: list[tuple[str, int]] = []
    for page_idx, text in enumerate(pages_norm):
        page_num = page_idx + 1
        marks = list(CLAUSE_RE.finditer(text))
        if not marks:
            continue
        for i, m in enumerate(marks):
            start = m.start()
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            clause = text[start:end].strip()
            if len(clause) >= MIN_CLAUSE_LEN:
                clauses.append((clause, page_num))
    return clauses


def diff_clause_lists(old: list[tuple[str, int]], new: list[tuple[str, int]]):
    old_text = [c for c, _ in old]
    new_text = [c for c, _ in new]
    sm = difflib.SequenceMatcher(a=old_text, b=new_text, autojunk=False)

    results = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        old_block = old[i1:i2]
        new_block = new[j1:j2]
        results.append((tag, old_block, new_block))
    return results


def clause_number(clause: str) -> str:
    m = CLAUSE_RE.match(clause + " ")
    return m.group(1) if m else "?"


def print_diff(doc_code: str, old_v: dict, new_v: dict):
    old_clauses = split_clauses(page_text_normalized(old_v["pages"]))
    new_clauses = split_clauses(page_text_normalized(new_v["pages"]))

    print(f"\n{'=' * 70}")
    print(f"{doc_code}  --  v{old_v['version']} ({old_v['effective_date']})  ->  v{new_v['version']} ({new_v['effective_date']})")
    print(f"{'=' * 70}")

    changes = diff_clause_lists(old_clauses, new_clauses)
    if not changes:
        print("No clause-level differences found (versions may only differ in whitespace/formatting).")
        return

    added = removed = changed = 0
    for tag, old_block, new_block in changes:
        if tag == "delete":
            removed += len(old_block)
            for clause, page in old_block:
                print(f"\n[REMOVED]  clause {clause_number(clause)}  (was p.{page} in v{old_v['version']})")
                print(f"  - {clause[:300]}")
        elif tag == "insert":
            added += len(new_block)
            for clause, page in new_block:
                print(f"\n[ADDED]  clause {clause_number(clause)}  (p.{page} in v{new_v['version']})")
                print(f"  + {clause[:300]}")
        elif tag == "replace":
            changed += max(len(old_block), len(new_block))
            print(f"\n[CHANGED]  around clause {clause_number((old_block or new_block)[0][0])}")
            for clause, page in old_block:
                print(f"  - (v{old_v['version']} p.{page}) {clause[:300]}")
            for clause, page in new_block:
                print(f"  + (v{new_v['version']} p.{page}) {clause[:300]}")

    print(f"\n{'-' * 70}")
    print(f"Summary: {added} clause(s) added, {removed} removed, {changed} changed/replaced.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("doc_code", help='e.g. "DHA/HRS/HLD/MA-2"')
    parser.add_argument(
        "--versions", nargs=2, metavar=("OLD", "NEW"),
        help="Compare two specific versions instead of oldest-vs-current, e.g. --versions 1.2 1.3",
    )
    args = parser.parse_args()

    versions = load_versions(args.doc_code)
    if len(versions) < 2:
        raise SystemExit(f"Only {len(versions)} version(s) found for {args.doc_code!r} -- nothing to diff.")

    if args.versions:
        old_ver, new_ver = args.versions
        old_v = next((v for v in versions if v["version"] == old_ver), None)
        new_v = next((v for v in versions if v["version"] == new_ver), None)
        if not old_v or not new_v:
            available = ", ".join(v["version"] for v in versions)
            raise SystemExit(f"Version not found. Available for {args.doc_code}: {available}")
        print_diff(args.doc_code, old_v, new_v)
    else:
        # default: oldest vs current (superseded=false)
        current = next((v for v in versions if not v["superseded"]), versions[-1])
        oldest = versions[0]
        if oldest is current:
            raise SystemExit("Only one version resolves as non-superseded; nothing to diff against.")
        print_diff(args.doc_code, oldest, current)


if __name__ == "__main__":
    main()
