"""tests/test_db_tier.py -- documents.tier column behavior (default, NOT NULL,
free-text values, idempotent migration). See ARCHITECTURE.md section 3."""
import psycopg2
import pytest

from app.core.db import SCHEMA_SQL
from tests.conftest import seed_document


def test_tier_defaults_to_official(test_conn):
    with test_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO documents (title, doc_code, version, effective_date, authority, sha256)
            VALUES ('Untiered Doc', 'DHA/TEST/1', '1', '2025-01-01', 'Dubai Health Authority', 'sha-untier-1')
            RETURNING id
            """
        )
        doc_id = cur.fetchone()[0]
        cur.execute("SELECT tier FROM documents WHERE id = %s", (doc_id,))
        assert cur.fetchone()[0] == "official"


def test_tier_not_null(test_conn):
    with pytest.raises(psycopg2.errors.NotNullViolation):
        with test_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents (title, doc_code, version, effective_date, authority, sha256, tier)
                VALUES ('Null Tier Doc', 'DHA/TEST/2', '1', '2025-01-01', 'Dubai Health Authority', 'sha-untier-2', NULL)
                """
            )
    test_conn.rollback()  # the failed INSERT aborts the transaction; keep the connection usable


def test_tier_accepts_all_values(test_conn):
    for i, tier in enumerate(["official", "research", "commentary"]):
        doc_id = seed_document(test_conn, doc_code=f"DHA/TEST/TIER-{i}", tier=tier)
        with test_conn.cursor() as cur:
            cur.execute("SELECT tier FROM documents WHERE id = %s", (doc_id,))
            assert cur.fetchone()[0] == tier


def test_alter_table_idempotent(test_conn):
    with test_conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        cur.execute(SCHEMA_SQL)
    test_conn.commit()
