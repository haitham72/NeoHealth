"""Tests for the diff_cache table schema."""


def test_diff_cache_table_exists(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'diff_cache'
            """
        )
        assert cur.fetchone() is not None
