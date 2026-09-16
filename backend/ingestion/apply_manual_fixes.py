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

Run once after ingest.py. Appends these docs into parsed_documents.json, then re-runs
supersession resolution over the merged list -- so the `superseded` value in each entry
below is a starting point, not the last word: a hand-added document that shares a
doc_code with an existing one is resolved against it by date like any other. (The
research papers are unaffected: RESEARCH/ELHAYEK-0N codes are unique, so each stays the
only, and therefore current, member of its group.)
"""
import json

import pdfplumber

from app.core.config import DATASET_DIR, PARSED_DOCUMENTS_FILE
from ingestion.supersession import resolve_supersession

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
    # --- OCR recovery batch, 2026-09-16: 24 records from ingestion/ocr_extraction.json
    # (3 DROP dispositions left out). 13 doc_codes used exactly as printed; 11 are the
    # pre-approved synthesised codes from ingestion/proposed_doc_codes.md. sha256 copied
    # from needs_manual.json, never recomputed. source_url cross-checked against
    # corpus_urls.txt. Unprinted versions default to "1" (same as MOHAP/UG/LIC-2025
    # above); every one of these codes is a single-member group so the value has no
    # supersession effect -- the shared resolver remains the last word.
    {
        "filename": "0BE585B5E6814D81913697DD6E644C02.ashx.pdf",
        "sha256": "75ec40052b1956035f052af7383e856be0e9c843a10cc8b0d450fb8ae2312e1d",
        "title": "Book 2: Health Insurance",
        "doc_code": "DOH/PUB/HEALTH-INSURANCE-BK2",
        "version": "1",
        "effective_date": "2005-09-10",
        "authority": "Department of Health - Abu Dhabi",
        "source_url": "https://www.doh.gov.ae/-/media/0BE585B5E6814D81913697DD6E644C02.ashx",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "130BB857CFE34C30B4905D76932F0168.ashx.pdf",
        "sha256": "207e61c6809e0b17010d6430bdf7ee26b8278b627f2cf8c11935247125da682a",
        "title": "Compliance with legislation and regulations related to direct billing insurance coverage for medical emergency, and ambulance services",
        "doc_code": "2025 / 622",
        "version": "1",
        "effective_date": "2025-04-25",
        "authority": "Department of Health - Abu Dhabi",
        "source_url": "https://www.doh.gov.ae/-/media/130BB857CFE34C30B4905D76932F0168.ashx",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "1e67953e-70f7-468e-a29f-8282a1c4aa62.pdf",
        "sha256": "cf7e77f5011d3e34ce1a898f8b1388dcd315cfa506eced6ed8def0c48273de0d",
        "title": "CONTINUING PROFESSIONAL DEVELOPMENT GUIDELINE",
        "doc_code": "HRD/RAS/PRU/003",
        "version": "1",
        "effective_date": "2014-01-01",
        "authority": "Dubai Health Authority",
        "source_url": "https://www.dha.gov.ae/uploads/112021/1e67953e-70f7-468e-a29f-8282a1c4aa62.pdf",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "20251002_Standard-for-Emergency-Departments-Urgent-Care-Centers.ashx.pdf",
        "sha256": "8606f567175431417d37923cf0aa4ece4acbd913168f79fc8abf1b7dc8a52011",
        "title": "Standard for Emergency Departments, Urgent Care Centers, and Select Primary Healthcare Centers in Abu Dhabi",
        "doc_code": "DoH/SD/ED-ECC-SPHC/V2/2025",
        "version": "V2",
        "effective_date": "2025-12-01",
        "authority": "Department of Health - Abu Dhabi",
        "source_url": "https://www.doh.gov.ae/-/media/Feature/Resources/Standards/2025/20251002_Standard-for-Emergency-Departments-Urgent-Care-Centers.ashx",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "5B5FD481D41347CE8F865347BB937A23.ashx.pdf",
        "sha256": "5f9dec9cc5ddc121baeca981029c94976d4505239ee61e6b001794a83b51205e",
        "title": "Reminder - Compliance with Patient Profiling as per DOH Standard for Managing Supply and Safe Use of Medications",
        "doc_code": "HAAD/MSSM/SD/1.0",
        "version": "1.0",
        "effective_date": "2016-12-20",
        "authority": "Department of Health - Abu Dhabi",
        "source_url": "https://www.doh.gov.ae/-/media/5B5FD481D41347CE8F865347BB937A23.ashx",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "721F474782574B779777D85118BE9FDD.ashx.pdf",
        "sha256": "c5dd9a44e72b7c7a6c162b6dbdad6458c036a38226c7ab6fe3b5df50c1416214",
        "title": "DOH THIQA COVERAGE POLICY ON LASER REFRACTIVE ERROR CORRECTION AND CATARACT (IOL) SURGERY",
        "doc_code": "DOH/CPL/LRS/0.9",
        "version": "1",
        "effective_date": "2021-09-01",
        "authority": "Department of Health - Abu Dhabi",
        "source_url": "https://www.doh.gov.ae/-/media/721F474782574B779777D85118BE9FDD.ashx",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "7EA6916C486A4D6BBA0036C8CD931B11.ashx.pdf",
        "sha256": "0c5c290f7b6a7365222ee8392179d07ce0a8843799849aeb0a07223dcd2c7697",
        "title": "HEALTHCARE PROVIDERS MANUAL",
        "doc_code": "DOH/HRM/PROV-01",
        "version": "1",
        "effective_date": "2017-11-01",
        "authority": "Department of Health - Abu Dhabi",
        "source_url": "https://www.doh.gov.ae/-/media/7EA6916C486A4D6BBA0036C8CD931B11.ashx",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "97738f8d-7e7e-4448-a533-49cf0843a1bb.PDF",
        "sha256": "064c4801664b75cb25435b609e59e8578fa3f5a9f1a7a149a7e154ebe1ae32af",
        "title": "Point of Care Testing Guidelines",
        "doc_code": "HRD/HRS/FRU/038",
        "version": "1",
        "effective_date": "2016-04-01",
        "authority": "Dubai Health Authority",
        "source_url": "https://www.dha.gov.ae/uploads/112021/97738f8d-7e7e-4448-a533-49cf0843a1bb.PDF",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "A863F6DE8D2C4CBC8279F7666CACC715.ashx.pdf",
        "sha256": "9e3832c30f40fa0efc050476d9681fcf156a670ed7f7703cf5fc50890f713373",
        "title": "Standard for the Principles and Procedures Governing the Recovery of Payment for Healthcare Services under the Health Insurance Scheme",
        "doc_code": "DOH/Payers/HSFR/V2",
        "version": "V2",
        "effective_date": "2023-12-01",
        "authority": "Department of Health - Abu Dhabi",
        "source_url": "https://www.doh.gov.ae/-/media/A863F6DE8D2C4CBC8279F7666CACC715.ashx",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "CBD6B8E5144743E8AE617CA37E83FA01.ashx.pdf",
        "sha256": "fe601e67e8d7456c959247cd2ccfa5a3a5609e5b21d8533b41d91d7a9c7fd458",
        "title": "Policy for Regulating Advertising of Unhealthy Food and Beverages on Out-of-Home Media Assets",
        "doc_code": "HLU001",
        "version": "V1",
        "effective_date": "2025-10-01",
        "authority": "Department of Health - Abu Dhabi",
        "source_url": "https://www.doh.gov.ae/-/media/CBD6B8E5144743E8AE617CA37E83FA01.ashx",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "DOH-Claims-and-Adjudication-Rules-V20251.ashx.pdf",
        "sha256": "3626da8842706940a6330f6dd3b0e21b99dea3cd9585a5800b53ca86107c3fdd",
        "title": "DoH CLAIMS & ADJUDICATION RULES Including the Mandatory Tariff Pricelist Application Rules.",
        "doc_code": "DOH/CLAIMS/ADJ-RULES",
        "version": "V2025.1",
        "effective_date": "2025-11-01",
        "authority": "Department of Health - Abu Dhabi",
        "source_url": "https://www.doh.gov.ae/-/media/Feature/shafifya/Prices/Adjudication-Rules/DOH-Claims-and-Adjudication-Rules-V20251.ashx",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "DOH-Coding-Manual-CSv2021.ashx.pdf",
        "sha256": "dab97ba048cf2530be9c301008cb0a7be2832da672fcb001a12f152c5c6d6549",
        "title": "Coding Manual For Coding within the Emirate of Abu Dhabi",
        "doc_code": "DOH/CODING/MANUAL",
        "version": "2.0",
        "effective_date": "2025-01-01",
        "authority": "Department of Health - Abu Dhabi",
        "source_url": "https://www.doh.gov.ae/-/media/Feature/shafifya/standards/coding/DOH-Coding-Manual-CSv2021.ashx",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "DOH-Guideline-on-RWD_RWE-based-clinical-research.ashx.pdf",
        "sha256": "3d2c2238a8b825a1d3ff19ad46a7da3699ab2c2fcea40608ac57f36ee9f8968c",
        "title": "GUIDELINES ON REAL-WORLD DATA/REAL-WORLD EVIDENCE-BASED CLINICAL RESEARCH",
        "doc_code": "DOH/GL/RWD_RWE/V1",
        "version": "1",
        "effective_date": "2023-04-01",
        "authority": "Department of Health - Abu Dhabi",
        "source_url": "https://www.doh.gov.ae/-/media/Feature/Resources/Guidelines/DOH-Guideline-on-RWD_RWE-based-clinical-research.ashx",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "E20D8A6D109E45579637151F75E61FD8.ashx.pdf",
        "sha256": "87ea95aea057f02054f4fc53c082df449c4a97c92c8cd0b03dc655ad4c011338",
        "title": "GUIDELINES FOR THE IMPLEMENTATION OF THE ABU DHABI HEALTHCARE INFORMATION AND CYBER SECURITY STANDARD [ADHICS]",
        "doc_code": "DOH/STD/ADHICS",
        "version": "1",
        "effective_date": "2019-12-01",
        "authority": "Department of Health - Abu Dhabi",
        "source_url": "https://www.doh.gov.ae/-/media/E20D8A6D109E45579637151F75E61FD8.ashx",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "E72D25EFCD6D4559A52FE5B171FF3E23.ashx.pdf",
        "sha256": "b50e8bbc1f4d6564862e787fc3dcb7c9570342cb00aa52c2b41a2abd0a323124",
        "title": "Policy for Infection Control in the Health Care Facilities",
        "doc_code": "PPR/HCP/P0010/07",
        "version": "I",
        "effective_date": "2007-05-01",
        "authority": "Department of Health - Abu Dhabi",
        "source_url": "https://www.doh.gov.ae/-/media/E72D25EFCD6D4559A52FE5B171FF3E23.ashx",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "EAB3C65E8B614555870BA764A495C2C2.ashx.pdf",
        "sha256": "113ff6bba08a4d87cbbb25b0503e812541364bf48667543487ff81fadb1e7e2f",
        "title": "THIQA COVERAGE POLICY ON MANAGEMENT OF VITAMIN D DEFICIENCY",
        "doc_code": "DOH/Pol/CPL/1/2022",
        "version": "1",
        "effective_date": "2022-07-01",
        "authority": "Department of Health - Abu Dhabi",
        "source_url": "https://www.doh.gov.ae/-/media/EAB3C65E8B614555870BA764A495C2C2.ashx",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "EE58BBA0DEFC44719E8A15C19192BC67.ashx.pdf",
        "sha256": "a1e7bafc3bd9707c137f77abf74cd3b60ba183038a3ccc81599397e8ce9fd213",
        "title": "Process and Standards for Documenting Allergy Cases",
        "doc_code": "2025 / 114",
        "version": "1",
        "effective_date": "2025-07-09",
        "authority": "Department of Health - Abu Dhabi",
        "source_url": "https://www.doh.gov.ae/-/media/EE58BBA0DEFC44719E8A15C19192BC67.ashx",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "Guidelines-for-One-Day-Surgery-Centers.ashx.pdf",
        "sha256": "d6f985590bde971f407dd19f0010a4ef398c8348c4d9421e677f57cdfa5b6c82",
        "title": "One Day Surgery Service Jawda Guidance",
        "doc_code": "DOH/JAWDA/ONE-DAY-SURGERY",
        "version": "1",
        "effective_date": "2021-01-01",
        "authority": "Department of Health - Abu Dhabi",
        "source_url": "https://www.doh.gov.ae/-/media/Feature/Muashir/Jawda/Jawda-Quarterly-Submission-Guidelines/Guidelines-for-One-Day-Surgery-Centers.ashx",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "Home-Healthcare-Jawda-Guidance_V9_2026.ashx.pdf",
        "sha256": "cd37c28a1f8a7415cb5c13a674e96e17d9c2fde97f8ec6169076a8df43080df6",
        "title": "Home Healthcare Service Jawda Guidance",
        "doc_code": "DOH/JAWDA/HOME-HEALTHCARE",
        "version": "9",
        "effective_date": "2026-03-01",
        "authority": "Department of Health - Abu Dhabi",
        "source_url": "https://www.doh.gov.ae/-/media/Feature/Muashir/Jawda/2026/Home-Healthcare-Jawda-Guidance_V9_2026.ashx",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "Issue Sick Leave Guide 20202022619814.pdf",
        "sha256": "d7e726678a49d4b7e09e92d1a1e277b4d45d01fc167a3d759f0b3e01b2d604c1",
        "title": "Issue Sick Leave Certificate Guide",
        "doc_code": "DHA/GDL/SICK-LEAVE",
        "version": "1",
        "effective_date": "2021-01-01",
        "authority": "Dubai Health Authority",
        "source_url": "https://www.dha.gov.ae/uploads/062022/Issue%20Sick%20Leave%20Guide%2020202022619814.pdf",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "Mental-Health-Services-Jawda-GuidanceV322025.ashx.pdf",
        "sha256": "250e2099bf3a00eaf709fd8850ea0c07457689229ea25aca9cf7d5fd63431a7c",
        "title": "Mental Health Service Jawda Guidance",
        "doc_code": "DOH/JAWDA/MENTAL-HEALTH",
        "version": "3.2",
        "effective_date": "2025-07-01",
        "authority": "Department of Health - Abu Dhabi",
        "source_url": "https://www.doh.gov.ae/-/media/Feature/Muashir/Jawda/Jawda2025/Mental-Health-Services-Jawda-GuidanceV322025.ashx",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "PQR_April_2025.pdf",
        "sha256": "75c361b96bc6c7117dd39dbd04579027fdd5e4d6734dca8ceeb5b9ada5a3a1c5",
        "title": "Unified Healthcare Professional Qualification Requirements",
        "doc_code": "MOHAP/PQR/UNIFIED-QUAL",
        "version": "3",
        "effective_date": "2025-04-01",
        "authority": "Ministry of Health and Prevention",
        "source_url": "https://services.dha.gov.ae/sheryan/wps/contenthandler/!ut/p/digest!L_negPqVWa2FDiru8UDKPw/war/SheryanHomeThemeStatic/themes/Portal8.5/docs/PQR_April_2025.pdf",
        "superseded": False,
        "tier": "official",
    },
    {
        "filename": "Responsible-AI-Standard-V1.ashx.pdf",
        "sha256": "5e03adb731620ed5013e7fbefa0502603338c762e9995da547e69e7f5243d97d",
        "title": "Responsible Artificial Intelligence (AI) Standard",
        "doc_code": "DoH/ST/DDGO/RAI/V1/2025",
        "version": "V1",
        "effective_date": "2025-10-01",
        "authority": "Department of Health - Abu Dhabi",
        "source_url": "https://www.doh.gov.ae/-/media/Feature/Resources/Standards/2025/Responsible-AI-Standard-V1.ashx",
        "superseded": False,
        "tier": "official",
    },
    # DHA/STD/ORGAN-TISSUE-DONATION (sha256 4af73e92...) deliberately omitted: it's a
    # 4-page inspection checklist, not the standard itself (see ocr_extraction.json's
    # own notes), and rechunks to a single 1-chunk document with no substantive
    # content -- the suggestion miner independently judged it content-free too.
    # Already ingested into both local and prod before this was caught (see
    # ARCHITECTURE.md); left in place there since prod is insert-only, but excluded
    # here so a future from-scratch run doesn't re-add it.
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

    # Re-resolve over the MERGED list, not just the additions. Without this, whatever
    # `superseded` a MANUAL_FIXES entry hardcodes is final, and a hand-added document
    # sharing a doc_code with an existing one leaves both flagged in force -- which
    # the supersession filter then cannot hide, because it only hides what is flagged.
    by_code = resolve_supersession(parsed)
    for code, group in sorted(by_code.items()):
        if len(group) > 1:
            current = group[0]
            print(f"  supersession: {code} -> v{current['version']} ({current['effective_date']}) "
                  f"in force, {len(group) - 1} superseded")

    PARSED_DOCUMENTS_FILE.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nparsed_documents.json now has {len(parsed)} documents")


if __name__ == "__main__":
    main()
