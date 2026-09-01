"""
ReguLense demo: runs one licensing question through naive RAG (no supersession
awareness) and ReguLense (supersession-filtered, mandatory citation) side by side.

This is the whole pitch: a plain RAG confidently cites whatever chunk ranks highest,
even if it's a superseded regulation. ReguLense knows which version is still in force.
"""
from app.core.db import get_connection
from app.core.retrieval import answer_question

QUESTION = "What happens if a healthcare professional's license application is rejected?"

WIDTH = 78


def rule(char="="):
    print(char * WIDTH)


def print_naive(result: dict):
    print("[ PLAIN RAG ]  -- no supersession awareness")
    print("-" * WIDTH)
    if result["abstained"]:
        print("(would not have retrieved a confident match)")
        return
    doc = result["document"]
    print(result["answer"])
    print()
    print(f"Source: {doc['title']}")
    if doc["superseded"]:
        print(f"  >>> WARNING: this chunk came from a SUPERSEDED version <<<")
    print(f"  Code:      {doc['doc_code']}")
    print(f"  Version:   {doc['version']}")
    print(f"  Effective: {doc['effective_date']}")
    print("  (no version check performed - cites whatever ranked highest)")


def print_regulense(result: dict):
    print("[ REGULENSE ]  -- supersession-filtered, mandatory citation")
    print("-" * WIDTH)
    if result["abstained"]:
        print("I don't have current guidance on that.")
        return
    doc = result["document"]
    print(result["answer"])
    print()
    print(f"Source: {doc['title']}")
    print(f"  Code:      {doc['doc_code']}")
    print(f"  Version:   {doc['version']}   <-- in force")
    print(f"  Effective: {doc['effective_date']}")
    print(f"  Authority: {doc['authority']}")
    print(f"  Page:      {result['page']}")
    if result["superseded_excluded"] > 0:
        print(
            f"  >>> {result['superseded_excluded']} superseded version"
            f"{'s' if result['superseded_excluded'] != 1 else ''} of this document "
            "were excluded from retrieval <<<"
        )


def main():
    conn = get_connection()

    rule()
    print(f"Q: {QUESTION}")
    rule()
    print()

    naive_result = answer_question(conn, QUESTION, superseded_filter=False)
    print_naive(naive_result)
    print()

    regulense_result = answer_question(conn, QUESTION, superseded_filter=True)
    print_regulense(regulense_result)
    print()
    rule()

    conn.close()


if __name__ == "__main__":
    main()
