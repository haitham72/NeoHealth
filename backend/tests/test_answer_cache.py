"""Tests for the answer_cache table schema."""


def test_answer_cache_table_exists(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'answer_cache'
            """
        )
        assert cur.fetchone() is not None
