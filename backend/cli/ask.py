"""
ReguLense CLI (from backend/): python -m cli.ask "<question>"

Hybrid retrieval, restricted to in-force documents, mandatory citation or abstention.
"""
import sys

from app.core.db import get_connection
from app.core.retrieval import answer_question


def print_result(result: dict):
    if result["abstained"]:
        print("I don't have current guidance on that.")
        if "top_score" in result:
            print(f"(best match confidence {result['top_score']:.2f}, below threshold)")
        return

    doc = result["document"]
    print(result["answer"])
    print()
    print("Source:", doc["title"])
    print(f"  Code:      {doc['doc_code']}")
    print(f"  Version:   {doc['version']}")
    print(f"  Effective: {doc['effective_date']}")
    print(f"  Authority: {doc['authority']}")
    print(f"  Page:      {result['page']}")
    if result["superseded_excluded"] > 0:
        print(
            f"  ({result['superseded_excluded']} superseded version"
            f"{'s' if result['superseded_excluded'] != 1 else ''} of this document "
            "excluded from retrieval)"
        )


def main():
    if len(sys.argv) < 2:
        print('Usage: python -m cli.ask "<question>"')
        sys.exit(1)

    question = sys.argv[1]
    conn = get_connection()
    result = answer_question(conn, question, superseded_filter=True)
    conn.close()
    print_result(result)


if __name__ == "__main__":
    main()
