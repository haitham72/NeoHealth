"""
One-off: hand-fix documents that needs_manual.json flagged because they have no
DHA-style footer, so the automatic parser correctly refused to guess. The service
catalogue (health_regulation_5-12-19...) is dropped per spec: "if a PDF won't parse,
drop it".

Also carries the 3 Samer El Hayek (NeoHealth co-founder/CEO) research papers -- tier
"research", not "official". They're academic literature, not regulation: no doc_code
scheme applies, so RESEARCH/ELHAYEK-0N is a synthetic identifier for internal grouping
only, "authority" holds the journal name (AuthorityBadge shows it in the tooltip under
a "RESEARCH" badge, never dressed up as a regulator seal), and "superseded" is always
false since papers don't get superseded the way regulations do.

Run once after ingest.py. Appends these docs into parsed_documents.json.
"""
import json

import pdfplumber

from app.core.config import DATASET_DIR, PARSED_DOCUMENTS_FILE

MANUAL_FIXES = [
    {
        "filename": "A47D9907918943438EDB512BE9347AB8.ashx.pdf",
        "sha256": "a90882a96fb719a4489f8423b1c2b103e223ffdf0ea663f51c122e7d9e25d62c",
        "title": "Healthcare Professionals Manual",
        "doc_code": "DOH/HRM/PROF-01",
        "version": "2017",
        "effective_date": "2017-11-01",
        "authority": "Department of Health - Abu Dhabi",
        "source_url": "https://www.doh.gov.ae/-/media/A47D9907918943438EDB512BE9347AB8.ashx",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "B8AE2259EF7B4F819BE5F908EB8BB699.ashx.pdf",
        "sha256": "f955ec8b174fe819febbd46dbedf21a2ced8b2052782b1a4a3c6ee78b35df804",
        "title": "Healthcare Regulator Manual",
        "doc_code": "DOH/HRM/REG-01",
        "version": "2017",
        "effective_date": "2017-11-01",
        "authority": "Department of Health - Abu Dhabi",
        "source_url": "https://www.doh.gov.ae/-/media/B8AE2259EF7B4F819BE5F908EB8BB699.ashx",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "e2ed0e72-91a1-d918-b861-ce3a776f9060.pdf",
        "sha256": "77b996a6f93bbd598f960549ec29ce0412e809b08d88053eb4199e7eb164c6c2",
        "title": "Hospital Regulation",
        "doc_code": "MOHAP/HR/2018",
        "version": "2",
        "effective_date": "2018-01-01",  # cover/footer only say "Second version 2018" -- no month/day given anywhere in the document
        "authority": "Ministry of Health and Prevention",
        "source_url": "https://mohap.gov.ae/documents/20117/1212145/Hospital+Regulation.pdf/e2ed0e72-91a1-d918-b861-ce3a776f9060?t=1739112146473",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "a3e7be1d-4636-d554-278e-a230da823d14.pdf",
        "sha256": "f3ccee73b6c43ee4ff9869da65376370ba7135cde20f8ce7109a7f24676a8260",
        "title": "One Day Surgery Center Regulation",
        "doc_code": "MOHAP/ODSC/2018",
        "version": "2",
        "effective_date": "2018-01-01",  # cover/footer only say "Second version 2018" -- no month/day given anywhere in the document
        "authority": "Ministry of Health and Prevention",
        "source_url": "https://mohap.gov.ae/documents/20117/1212145/One+day+surgery+Center+Regulation-26.pdf/a3e7be1d-4636-d554-278e-a230da823d14?t=1739157308289",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "3cb3975d-64d0-6100-c4d6-fc3d45e05d24.pdf",
        "sha256": "e0ae352816842b2af586eaaaeb8ba1d91a330791177332acece70e145d71168f",
        "title": "Initial Approval for Licensing - Re-licensing Health Facility (User Guide)",
        "doc_code": "MOHAP/UG/LIC-2025",
        "version": "1",
        "effective_date": "2025-07-01",  # cover page states "July 2025", no specific day
        "authority": "Ministry of Health and Prevention",
        "source_url": "https://mohap.gov.ae/documents/20117/0/Initial+Approval+for+Licensing+-+Re-licensing+Health+Facility++-+ENG.pdf/3cb3975d-64d0-6100-c4d6-fc3d45e05d24?t=1756112314105",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "PMC7296313.pdf",
        "sha256": "98c998390bef586096004b74825c0bcb006510c9a1105905cdf9ca15a79f2fec",
        "title": "Telepsychiatry and Healthcare Access Inequities During the COVID-19 Pandemic",
        "doc_code": "RESEARCH/ELHAYEK-01",
        "version": "1",
        "effective_date": "2020-06-16",
        "authority": "Asian Journal of Psychiatry",
        "source_url": "https://europepmc.org/articles/PMC7296313?pdf=render",
        "superseded": False,
        "tier": "research",
    },
    {
        "filename": "PMC7538900.pdf",
        "sha256": "fa3d5ad5f6a782fa64cac2015b1952b3ece34b0b0dc32648b44c2261984519d0",
        "title": "Telepsychiatry During the COVID-19 Pandemic: Development of a Protocol for Telemental Health Care",
        "doc_code": "RESEARCH/ELHAYEK-02",
        "version": "1",
        "effective_date": "2020-09-23",
        "authority": "Frontiers in Psychiatry",
        "source_url": "https://europepmc.org/articles/PMC7538900?pdf=render",
        "superseded": False,
        "tier": "research",
    },
    {
        "filename": "PMC7682595.pdf",
        "sha256": "74d5a198dd48b7997f5790944b7b5f60b0d6e28ef9479b9a40a7289084cc6464",
        "title": "Telepsychiatry in the Arab World: A Viewpoint Before and During COVID-19",
        "doc_code": "RESEARCH/ELHAYEK-03",
        "version": "1",
        "effective_date": "2020-11-19",
        "authority": "Neuropsychiatric Disease and Treatment",
        "source_url": "https://europepmc.org/articles/PMC7682595?pdf=render",
        "superseded": False,
        "tier": "research",
    },
]


def main():
    parsed = json.loads(PARSED_DOCUMENTS_FILE.read_text(encoding="utf-8"))
    existing_filenames = {d["filename"] for d in parsed}

    for fix in MANUAL_FIXES:
        if fix["filename"] in existing_filenames:
            print(f"  already present, skipping: {fix['filename']}")
            continue
        with pdfplumber.open(DATASET_DIR / fix["filename"]) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
        doc = dict(fix)
        doc["pages"] = pages
        parsed.append(doc)
        print(f"  added: {fix['filename']} -> {fix['doc_code']}")

    PARSED_DOCUMENTS_FILE.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nparsed_documents.json now has {len(parsed)} documents")


if __name__ == "__main__":
    main()
