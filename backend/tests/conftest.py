"""Shared pytest fixtures for ReguLense backend tests. Uses a real Postgres+pgvector
test database (test_regulense) rather than mocking the connection -- retrieval.py's SQL
(tier filters, RRF, cosine distance) is exactly what these tests exist to exercise, so
a mocked connection would test nothing real. Only the two OpenAI-facing edges
(retrieval.embed, retrieval.generate_answer) are ever mocked -- see stub_llm."""
import math
import os
import uuid

import psycopg2
import pytest
from dotenv import load_dotenv

load_dotenv()

from app.core import retrieval
from app.core.db import SCHEMA_SQL


def _test_connect_kwargs() -> dict:
    test_url = os.environ.get("TEST_DATABASE_URL")
    if test_url:
        return {"dsn": test_url}
    return {
        "host": os.environ["PGHOST"],
        "port": os.environ["PGPORT"],
        "user": os.environ["PGUSER"],
        "password": os.environ["PGPASSWORD"],
        "dbname": "test_regulense",
    }


@pytest.fixture
def test_conn():
    conn = psycopg2.connect(**_test_connect_kwargs())
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()
    yield conn
    conn.rollback()
    conn.close()


def vec(cosine_to_query: float, dim: int = 1536) -> list[float]:
    """A unit vector whose cosine similarity to QUERY_VEC is exactly `cosine_to_query`
    -- lets a test pin a chunk's semantic_score to a precise value (e.g. just above or
    below CONFIDENCE_LOW) without a real embedding call. Both this and QUERY_VEC only
    ever populate dimensions 0-1, so two vec() outputs compare correctly against each
    other ONLY when one of them is QUERY_VEC (theta=0) -- see stub_llm."""
    theta = math.acos(max(-1.0, min(1.0, cosine_to_query)))
    v = [0.0] * dim
    v[0] = math.cos(theta)
    v[1] = math.sin(theta)
    return v


QUERY_VEC = vec(1.0)


def orthogonal_vec(dim: int = 1536) -> list[float]:
    """A vector with zero cosine similarity to every vec() output (which only ever
    populates dimensions 0-1) -- stands in for retrieval.embed() on a genuinely
    off-topic question, without needing angle arithmetic between two arbitrary vec()
    outputs."""
    v = [0.0] * dim
    v[2] = 1.0
    return v


def seed_document(
    conn,
    *,
    doc_code: str = "DHA/HRS/HPSD/ST-14",
    title: str = "Standards for Telehealth Services",
    version: str = "4",
    authority: str = "Dubai Health Authority",
    tier: str = "official",
    superseded: bool = False,
    effective_date: str = "2025-11-26",
    sha256: str | None = None,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO documents (title, doc_code, version, effective_date, authority,
                                    source_url, sha256, superseded, tier)
            VALUES (%s, %s, %s, %s, %s, NULL, %s, %s, %s)
            RETURNING id
            """,
            (title, doc_code, version, effective_date, authority,
             sha256 or f"sha-{uuid.uuid4().hex}", superseded, tier),
        )
        return cur.fetchone()[0]


def seed_chunk(
    conn, document_id: int, *, page: int = 1, text: str = "Sample chunk text.", score: float = 0.7,
) -> int:
    embedding = vec(score)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO chunks (document_id, page, page_start, page_end, text, embedding, tsv)
            VALUES (%s, %s, %s, %s, %s, %s, to_tsvector('english', %s))
            RETURNING id
            """,
            (document_id, page, page, page, text, embedding, text),
        )
        return cur.fetchone()[0]


def seed_official_doc(conn, *, score: float = 0.7) -> dict:
    """A DHA official document with 2 chunks, per tier-system-and-research-ui.md's Part 4."""
    doc_id = seed_document(conn, tier="official")
    chunk_ids = [
        seed_chunk(conn, doc_id, page=83, text="Standards for telehealth services in Dubai.", score=score),
        seed_chunk(conn, doc_id, page=84, text="Tele-mental health quality requirements.", score=score),
    ]
    return {"document_id": doc_id, "chunk_ids": chunk_ids}


def seed_research_doc(conn, *, score: float = 0.7) -> dict:
    """Mirrors RESEARCH/ELHAYEK-01 (apply_manual_fixes.py) -- a research-tier document
    with 2 chunks, journal authority instead of a regulator."""
    doc_id = seed_document(
        conn,
        doc_code="RESEARCH/ELHAYEK-01",
        title="Telepsychiatry in the Arab World",
        version="1",
        authority="Asian Journal of Psychiatry",
        tier="research",
        effective_date="2021-06-01",
    )
    chunk_ids = [
        seed_chunk(conn, doc_id, page=5, text="Telepsychiatry adoption before COVID-19.", score=score),
        seed_chunk(conn, doc_id, page=6, text="Telepsychiatry adoption during COVID-19.", score=score),
    ]
    return {"document_id": doc_id, "chunk_ids": chunk_ids}


@pytest.fixture
def stub_llm(monkeypatch):
    """Patches retrieval.py's two OpenAI-facing calls so tests exercise real SQL/RRF/
    tier-splitting logic against the test DB without a network call or API key.
    embed() always returns QUERY_VEC -- tests control relevance entirely via each
    seeded chunk's embedding (see vec()), not via what embed() returns."""
    monkeypatch.setattr(retrieval, "embed", lambda text: QUERY_VEC)
    monkeypatch.setattr(retrieval, "generate_answer", lambda *a, **kw: "Stub answer text.")
    return QUERY_VEC
