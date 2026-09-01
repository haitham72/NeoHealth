"""
Extract text + first-page metadata from the ReguLense corpus, resolve supersession.

CRITICAL: doc_code / version / effective_date are parsed from the document's own
footer text (present on nearly every content page as a "Code: ... Issue Nu: ...
Effective Date: ..." line), never from the filename or source URL. The filename lies
(see needs_manual.json entries and CLAUDE.md for the Telehealth trap).

Output: parsed_documents.json — one record per document with metadata, superseded
flag, and per-page text (used by load_db.py to chunk + embed).
Anything that can't be parsed automatically is written to needs_manual.json instead
of guessed at.
"""
import hashlib
import json
import re
from pathlib import Path

import pdfplumber

from app.core.config import CORPUS_URLS_FILE, DATASET_DIR, NEEDS_MANUAL_FILE, PARSED_DOCUMENTS_FILE
from app.core.urls import filename_from_url, load_urls

# footer line looks like (whitespace/punctuation around fields is inconsistent):
#   "Code: DHA/HRS/HLD/MA-2 Issue Nu: version 1.3 Issue Date: 18/07/2025
#    Effective Date: 18/07/2025 Revision Date: 18/07/2030 Page 2 of 54"
#   "Code: DHA/HRS/ HLD/MA-1 Issue Nu: 1 ..."
#   "Code: DHA/HRS/HPSD/ST-25 - Issue Nu: 2.1 - Issue Date: ..."
CODE_RE = re.compile(r"Code:\s*([A-Z]{2,6}(?:\s*/\s*[A-Z0-9-]{1,8}){2,4})")
ISSUE_NU_RE = re.compile(r"Issue\s*Nu:?\s*(?:version\s*)?([\d.]+)", re.IGNORECASE)
EFFECTIVE_DATE_RE = re.compile(r"Effective\s*Date:?\s*(\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE)
# the title is the line immediately before the "Code:" footer line
TITLE_RE = re.compile(r"([^\n]{4,120})\n\s*Code:\s*[A-Z]")

# DoH's newer (2025/2026) "Standard"/"Policy" template carries a structured metadata
# block on page 2 instead of DHA's per-page footer -- distinct enough to need its own
# patterns rather than trying to force one regex to cover both authorities:
#   "Document Ref. Number: DoH/SD/ED-ECC-SPHC/V2/2025 Version: V2
#    New / Revised: ...  Publication Date: October, 2025  Effective Date: December, 2025"
# Only ~60% of DoH docs use this template (older Jawda/coding-manual docs don't), so
# whatever this can't parse still correctly falls through to needs_manual.json.
DOH_REF_RE = re.compile(r"Document Ref\.?\s*Number:?\s*([A-Za-z]{2,6}(?:\s*/\s*[A-Za-z0-9-]+){2,6})")
DOH_VERSION_RE = re.compile(r"Version:?\s*V?\s*([\d.]+)", re.IGNORECASE)
# date shows up as "April, 2026", "September2025" (space dropped by extraction), or
# "June 11, 2025" -- one pattern, tolerant of an optional day and optional separators.
DOH_EFFECTIVE_RE = re.compile(r"Effective Date:?\s*\n?\s*([A-Za-z]+\.?\s*\d{0,2},?\s*\d{4})")

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _month_number(word: str) -> int:
    word = word.lower()
    if word in _MONTH_NAMES:
        return _MONTH_NAMES[word]
    # tolerate abbreviations like "Sept" or "Sep"
    matches = [n for full, n in _MONTH_NAMES.items() if full.startswith(word)]
    if len(matches) == 1:
        return matches[0]
    raise ValueError(f"unrecognized month: {word!r}")


def parse_date_monthname(s: str) -> str:
    m = re.match(r"([A-Za-z]+)\.?\s*(\d{1,2})?,?\s*(\d{4})", s.strip())
    month = _month_number(m.group(1))
    day = int(m.group(2)) if m.group(2) else 1
    return f"{m.group(3)}-{month:02d}-{day:02d}"


def parse_metadata_doh(pages: list[str]) -> dict:
    """Fallback for DoH's structured-metadata template (see patterns above). Title comes
    from page 1's own heading text (usually a clean, short cover-page title) rather than
    the "Document Title:" field, whose value can appear before its own label once
    pdfplumber linearizes the source's two-column layout."""
    search_text = "\n".join(pages[:3])

    ref_match = DOH_REF_RE.search(search_text)
    version_match = DOH_VERSION_RE.search(search_text)
    effective_match = DOH_EFFECTIVE_RE.search(search_text)

    result = {}
    if ref_match:
        code = normalize_code(ref_match.group(1))
        result["doc_code"] = code
        result["authority"] = "Department of Health - Abu Dhabi"
        if not version_match:
            # the ref itself often ends in a version segment, e.g. ".../V2/2025"
            v_in_code = re.search(r"/V(\d+(?:\.\d+)?)/", code, re.IGNORECASE)
            if v_in_code:
                result["version"] = v_in_code.group(1)
    if version_match:
        result["version"] = version_match.group(1)
    if effective_match:
        try:
            result["effective_date"] = parse_date_monthname(effective_match.group(1))
        except (ValueError, KeyError):
            pass  # unrecognized month text -- leave effective_date missing, not guessed

    title = " ".join(line.strip() for line in pages[0].split("\n") if line.strip())
    if 8 <= len(title) <= 100:
        result["title"] = title

    return result


AUTHORITY_BY_PREFIX = {
    "DHA": "Dubai Health Authority",
    "DOH": "Department of Health - Abu Dhabi",
    "HAAD": "Department of Health - Abu Dhabi (HAAD)",
    "MOHAP": "Ministry of Health and Prevention",
}


def safe(s: str) -> str:
    """Windows' cp1252 console can't print non-Latin-1 filenames (e.g. the Arabic-titled
    DHA PDF) -- made ASCII-safe unconditionally so a print() doesn't abort mid-run."""
    return s.encode("ascii", "backslashreplace").decode("ascii")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def text_sha256_of(pages: list[str]) -> str:
    """Hash of normalized extracted text, not raw bytes -- catches the same document
    re-served under a different filename/URL (new PDF metadata/timestamp embedded,
    identical content), which a byte-level sha256 alone would miss."""
    normalized = re.sub(r"\s+", " ", "".join(pages)).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_code(raw: str) -> str:
    return "/".join(part.strip() for part in raw.split("/"))


def parse_date_ddmmyyyy(s: str) -> str:
    d, m, y = s.split("/")
    return f"{y}-{m.zfill(2)}-{d.zfill(2)}"


def extract_pages(pdf_path: Path) -> list[str]:
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return pages


def parse_metadata(pages: list[str]) -> dict:
    """Search the first few content pages for the footer metadata line."""
    search_text = "\n".join(pages[:4])

    code_match = CODE_RE.search(search_text)
    issue_match = ISSUE_NU_RE.search(search_text)
    date_match = EFFECTIVE_DATE_RE.search(search_text)
    title_match = TITLE_RE.search(search_text)

    result = {}
    if code_match:
        result["doc_code"] = normalize_code(code_match.group(1))
        prefix = result["doc_code"].split("/")[0]
        result["authority"] = AUTHORITY_BY_PREFIX.get(prefix, prefix)
    if issue_match:
        result["version"] = issue_match.group(1)
    if date_match:
        result["effective_date"] = parse_date_ddmmyyyy(date_match.group(1))
    if title_match:
        result["title"] = title_match.group(1).strip()

    return result


def main():
    pdf_paths = sorted(DATASET_DIR.glob("*.pdf"))
    print(f"Found {len(pdf_paths)} PDFs to ingest")

    url_by_filename = {filename_from_url(u): u for u in load_urls(CORPUS_URLS_FILE)}

    parsed_documents = []
    needs_manual = []
    duplicates = []
    seen_text_hashes: dict[str, str] = {}

    for pdf_path in pdf_paths:
        print(f"  parsing {safe(pdf_path.name)} ...", end=" ")
        try:
            pages = extract_pages(pdf_path)
        except Exception as e:
            print(f"FAILED to read: {e}")
            needs_manual.append({"filename": pdf_path.name, "error": f"unreadable: {e}"})
            continue

        meta = parse_metadata(pages)
        required = ("doc_code", "version", "effective_date", "title")
        missing = [f for f in required if f not in meta]

        if missing:
            doh_meta = parse_metadata_doh(pages)
            meta = {**doh_meta, **meta}  # DHA-style match (if any) always wins
            missing = [f for f in required if f not in meta]

        if missing:
            print(f"NEEDS MANUAL (missing: {', '.join(missing)})")
            needs_manual.append(
                {
                    "filename": pdf_path.name,
                    "sha256": sha256_of(pdf_path),
                    "parsed_so_far": meta,
                    "missing_fields": missing,
                    "first_page_preview": (pages[0] if pages else "")[:400],
                }
            )
            continue

        text_hash = text_sha256_of(pages)
        if text_hash in seen_text_hashes:
            print(f"DUPLICATE of {safe(seen_text_hashes[text_hash])} -- skipped")
            duplicates.append({"filename": pdf_path.name, "duplicate_of": seen_text_hashes[text_hash]})
            continue
        seen_text_hashes[text_hash] = pdf_path.name

        print(f"OK  {meta['doc_code']}  v{meta['version']}  eff. {meta['effective_date']}")
        parsed_documents.append(
            {
                "filename": pdf_path.name,
                "sha256": sha256_of(pdf_path),
                "title": meta["title"],
                "doc_code": meta["doc_code"],
                "version": meta["version"],
                "effective_date": meta["effective_date"],
                "authority": meta["authority"],
                "source_url": url_by_filename.get(pdf_path.name),
                "pages": pages,
            }
        )

    # --- supersession: group by doc_code, newest effective_date wins ---
    by_code: dict[str, list[dict]] = {}
    for doc in parsed_documents:
        by_code.setdefault(doc["doc_code"], []).append(doc)

    for code, docs in by_code.items():
        docs.sort(key=lambda d: d["effective_date"], reverse=True)
        for i, doc in enumerate(docs):
            doc["superseded"] = i != 0

    # strip page text before writing needs_manual (parsed_documents keeps it for chunking)
    PARSED_DOCUMENTS_FILE.write_text(
        json.dumps(parsed_documents, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    NEEDS_MANUAL_FILE.write_text(
        json.dumps(needs_manual, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\nParsed OK: {len(parsed_documents)}  ->  {PARSED_DOCUMENTS_FILE.name}")
    print(f"Needs manual fix: {len(needs_manual)}  ->  {NEEDS_MANUAL_FILE.name}")
    if duplicates:
        print(f"Duplicates skipped (identical text to another file): {len(duplicates)}")
        for d in duplicates:
            print(f"  {safe(d['filename'])}  ==  {safe(d['duplicate_of'])}")

    # --- milestone check ---
    print("\n--- MILESTONE: DHA/HRS/HLD/MA-2 supersession ---")
    ma2 = by_code.get("DHA/HRS/HLD/MA-2", [])
    if not ma2:
        print("  FAIL: no DHA/HRS/HLD/MA-2 documents parsed at all.")
    else:
        for doc in ma2:
            status = "IN FORCE" if not doc["superseded"] else "superseded"
            print(f"  v{doc['version']:>5}  eff. {doc['effective_date']}  -> {status}")
        in_force = [d for d in ma2 if not d["superseded"]]
        ok = (
            len(ma2) == 3
            and len(in_force) == 1
            and in_force[0]["version"] == "1.3"
            and in_force[0]["effective_date"] == "2025-07-18"
        )
        print("  MILESTONE PASSED" if ok else "  MILESTONE FAILED - fix parsing before continuing")


if __name__ == "__main__":
    main()
