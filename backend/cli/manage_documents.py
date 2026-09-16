"""
ReguLense document management: list, remove, and mark documents superseded.

There's no dedicated tool for this in the original build -- both operations are a
single SQL statement against a schema that already supports them (chunks cascade-
delete via documents.id ON DELETE CASCADE; superseded is a plain boolean retrieval
already filters on). This script exists purely so you don't have to hand-write SQL
under interview pressure.

Usage (from backend/):
  python -m cli.manage_documents list
  python -m cli.manage_documents remove <id>
  python -m cli.manage_documents supersede <id>
  python -m cli.manage_documents unsupersede <id>
"""
import sys

from app.core.db import get_connection


def list_documents(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.id, d.doc_code, d.version, d.effective_date, d.superseded,
                   d.title, count(c.id) AS chunk_count
            FROM documents d LEFT JOIN chunks c ON c.document_id = d.id
            GROUP BY d.id
            ORDER BY d.doc_code, d.effective_date DESC
            """
        )
        rows = cur.fetchall()
    print(f"{'ID':>4}  {'DOC_CODE':<20} {'VERSION':<8} {'EFFECTIVE':<12} {'STATUS':<11} {'CHUNKS':>6}  TITLE")
    for r in rows:
        status = "superseded" if r[4] else "in force"
        print(f"{r[0]:>4}  {r[1]:<20} {r[2]:<8} {str(r[3]):<12} {status:<11} {r[6]:>6}  {r[5]}")


def remove_document(conn, doc_id: int):
    with conn.cursor() as cur:
        cur.execute("SELECT title, doc_code, version FROM documents WHERE id = %s", (doc_id,))
        row = cur.fetchone()
        if not row:
            print(f"No document with id={doc_id}")
            return
        cur.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
    conn.commit()
    print(f"Removed: {row[0]} ({row[1]} v{row[2]}) -- chunks cascade-deleted automatically.")


def set_superseded(conn, doc_id: int, value: bool):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE documents SET superseded = %s WHERE id = %s RETURNING title, doc_code, version",
            (value, doc_id),
        )
        row = cur.fetchone()
        if not row:
            print(f"No document with id={doc_id}")
            return
    conn.commit()
    label = "superseded" if value else "in force"
    print(f"{row[0]} ({row[1]} v{row[2]}) is now marked {label}.")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]
    conn = get_connection()
    try:
        if command == "list":
            list_documents(conn)
        elif command == "remove":
            remove_document(conn, int(sys.argv[2]))
        elif command == "supersede":
            set_superseded(conn, int(sys.argv[2]), True)
        elif command == "unsupersede":
            set_superseded(conn, int(sys.argv[2]), False)
        else:
            print(f"Unknown command: {command}")
            print(__doc__)
            sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
