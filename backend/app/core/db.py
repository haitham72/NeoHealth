"""Shared Postgres connection + schema for ReguLense."""
import os
import threading

import psycopg2
import psycopg2.pool
from dotenv import load_dotenv

load_dotenv()

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    doc_code TEXT NOT NULL,
    version TEXT NOT NULL,
    effective_date DATE NOT NULL,
    authority TEXT NOT NULL,
    source_url TEXT,
    sha256 TEXT UNIQUE NOT NULL,
    ingested_at TIMESTAMPTZ DEFAULT now(),
    superseded BOOLEAN NOT NULL DEFAULT false
);

-- 'official' = binding regulation from a government health authority (DHA, DoH, ...);
-- 'research' = academic literature; 'commentary' = secondary sources (law firm/advisory
-- write-ups). Added after the corpus grew past DHA/DoH-only -- default keeps every
-- existing row 'official' with no backfill needed.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS tier TEXT NOT NULL DEFAULT 'official';

CREATE TABLE IF NOT EXISTS chunks (
    id SERIAL PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding vector(1536),
    tsv tsvector
);

-- Added when chunking moved from page-level (pdfplumber) to Docling's structure-aware,
-- cross-page chunks. heading_path is the section title(s) the chunk falls under (an
-- array since a merged tiny-section run can span several distinct headings -- see
-- rechunk.py). bboxes is the exact per-element PDF provenance (page + bounding box)
-- Docling provides, used to render precise highlight rectangles instead of the old
-- fuzzy client-side text search. page/page_start are kept in sync (page = page_start)
-- so existing single-page call sites keep working without every one needing an
-- immediate update; page_end is only different from page_start for a cross-page chunk.
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS heading_path TEXT[];
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS bboxes JSONB;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS page_start INTEGER;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS page_end INTEGER;

CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING gin(tsv);
CREATE INDEX IF NOT EXISTS documents_doc_code_idx ON documents(doc_code);

-- Tracks API calls per day so api.py can enforce a hard daily cap.
-- Postgres-backed (not an in-process counter) because the app can restart/sleep
-- between requests, which would silently reset an in-memory counter.
CREATE TABLE IF NOT EXISTS daily_usage (
    usage_date DATE PRIMARY KEY,
    api_call_count INTEGER NOT NULL DEFAULT 0
);

-- Migration: rename existing column if it exists
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'daily_usage' AND column_name = 'openai_call_count'
    ) THEN
        ALTER TABLE daily_usage RENAME COLUMN openai_call_count TO api_call_count;
    END IF;
END $$;

-- Per-visitor chat history. client_id is a UUID the frontend generates once and
-- stores in localStorage -- there is no login, so this is anonymous grouping-by-
-- browser, not a real account system. id is generated in Python (uuid.uuid4()), not
-- via a Postgres default, so this table needs no extension (pgcrypto/uuid-ossp).
CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    title TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chats_client_id_idx ON chats(client_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id SERIAL PRIMARY KEY,
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    response JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_messages_chat_id_idx ON chat_messages(chat_id, created_at);
"""
# corpus is a few hundred chunks; a plain sequential scan on the embedding column
# is fast enough for a demo, so no ANN index (ivfflat/hnsw) is built.

_POOL_MAXCONN = 10
_pool: psycopg2.pool.ThreadedConnectionPool | None = None
# psycopg2's pool classes raise PoolError immediately on exhaustion rather than
# blocking, so a semaphore bounds concurrent checkouts to _POOL_MAXCONN and makes
# get_connection() actually wait for a slot instead of surfacing a raw 500 under a
# burst above maxconn.
_pool_semaphore = threading.Semaphore(_POOL_MAXCONN)


def _connect_kwargs() -> dict:
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return {"dsn": database_url}
    return {
        "host": os.environ["PGHOST"],
        "port": os.environ["PGPORT"],
        "user": os.environ["PGUSER"],
        "password": os.environ["PGPASSWORD"],
        "dbname": os.environ["PGDATABASE"],
    }


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    # Single uvicorn worker (see render.yaml) -- a module-level pool is safe, no
    # cross-process coordination needed. maxconn leaves headroom below any small
    # managed Postgres's connection cap.
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1, maxconn=_POOL_MAXCONN, **_connect_kwargs()
        )
    return _pool


def get_connection():
    """Checks out a connection from the pool, waiting for a free slot if all
    _POOL_MAXCONN are in use (a burst degrades to slower responses, not failures).
    Pair with release_connection() (not conn.close(), which would remove the
    connection from the pool instead of returning it)."""
    _pool_semaphore.acquire()
    try:
        return _get_pool().getconn()
    except Exception:
        _pool_semaphore.release()
        raise


def release_connection(conn) -> None:
    try:
        _get_pool().putconn(conn)
    finally:
        _pool_semaphore.release()


def increment_daily_usage(conn) -> int:
    """Atomically increments today's API call counter and returns the new total.
    UPSERT+RETURNING lets Postgres's row lock serialize concurrent increments instead
    of a separate read-then-write that could race. The caller compares the returned
    count against its cap -- increment-then-check means the request that trips the
    cap is still counted, so a burst of concurrent requests can't all read "just
    under cap" before any of them increments, which would defeat the cap entirely."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO daily_usage (usage_date, api_call_count)
            VALUES (CURRENT_DATE, 1)
            ON CONFLICT (usage_date)
            DO UPDATE SET api_call_count = daily_usage.api_call_count + 1
            RETURNING api_call_count
            """
        )
        new_count = cur.fetchone()[0]
    conn.commit()
    return new_count


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()
