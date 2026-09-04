"""Per-visitor chat history: list/create/load chats and persist messages.

client_id is a UUID the frontend generates once and stores in localStorage -- there is
no login, so a chat is only ever visible to the client_id that created it, not tied to
IP (IPs are shared across NATs/carriers and rotate, so they'd mix up or lose people's
history) or any real identity. Deliberately decoupled from /ask and /ask-stream: those
endpoints are untouched by this file, and a persistence failure here must never break
asking a question -- the frontend calls these as best-effort side calls, same spirit as
enrichment.py's additive-only extras."""
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from psycopg2.extras import Json

from app.api.schemas.chats import (
    ChatDetail,
    ChatMessageOut,
    ChatSummary,
    CreateChatRequest,
    SaveMessageRequest,
)
from app.core.db import get_connection, release_connection

router = APIRouter()

MAX_CHATS_LISTED = 50
TITLE_MAX_LEN = 80


def _title_from_content(content: str) -> str:
    snippet = " ".join(content.split())
    return snippet if len(snippet) <= TITLE_MAX_LEN else snippet[: TITLE_MAX_LEN - 1].rstrip() + "…"


def _summary(row: tuple[Any, ...]) -> ChatSummary:
    return ChatSummary(id=row[0], title=row[1], created_at=row[2].isoformat(), updated_at=row[3].isoformat())


@router.get("/chats", response_model=list[ChatSummary])
def list_chats(client_id: str):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, created_at, updated_at
                FROM chats
                WHERE client_id = %s
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (client_id, MAX_CHATS_LISTED),
            )
            rows = cur.fetchall()
        return [_summary(row) for row in rows]
    finally:
        release_connection(conn)


@router.post("/chats", response_model=ChatSummary)
def create_chat(req: CreateChatRequest):
    chat_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chats (id, client_id)
                VALUES (%s, %s)
                RETURNING id, title, created_at, updated_at
                """,
                (chat_id, req.client_id),
            )
            row = cur.fetchone()
        conn.commit()
        return _summary(row)
    finally:
        release_connection(conn)


@router.get("/chats/{chat_id}", response_model=ChatDetail)
def get_chat(chat_id: str, client_id: str):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, created_at, updated_at FROM chats WHERE id = %s AND client_id = %s",
                (chat_id, client_id),
            )
            chat_row = cur.fetchone()
            if chat_row is None:
                raise HTTPException(404, "chat not found")
            cur.execute(
                """
                SELECT role, content, response, created_at
                FROM chat_messages
                WHERE chat_id = %s
                ORDER BY created_at ASC, id ASC
                """,
                (chat_id,),
            )
            message_rows = cur.fetchall()
        return ChatDetail(
            id=chat_row[0],
            title=chat_row[1],
            created_at=chat_row[2].isoformat(),
            updated_at=chat_row[3].isoformat(),
            messages=[
                ChatMessageOut(role=m[0], content=m[1], response=m[2], created_at=m[3].isoformat())
                for m in message_rows
            ],
        )
    finally:
        release_connection(conn)


@router.post("/chats/{chat_id}/messages", response_model=ChatSummary)
def save_message(chat_id: str, req: SaveMessageRequest):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT title FROM chats WHERE id = %s AND client_id = %s", (chat_id, req.client_id))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(404, "chat not found")
            title = row[0]
            cur.execute(
                """
                INSERT INTO chat_messages (chat_id, role, content, response)
                VALUES (%s, %s, %s, %s)
                """,
                (chat_id, req.role, req.content, Json(req.response) if req.response is not None else None),
            )
            if title is None and req.role == "user":
                title = _title_from_content(req.content)
            cur.execute(
                """
                UPDATE chats SET updated_at = now(), title = COALESCE(%s, title)
                WHERE id = %s
                RETURNING id, title, created_at, updated_at
                """,
                (title, chat_id),
            )
            updated = cur.fetchone()
        conn.commit()
        return _summary(updated)
    finally:
        release_connection(conn)
