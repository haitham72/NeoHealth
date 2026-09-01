"""POST /ask, POST /ask-stream. HTTP-shaping (try/except -> HTTPException, the
DAILY_OPENAI_CALL_CAP check, and SSE event formatting) stays inline
here deliberately -- it isn't business logic, so it's not extracted to a service."""
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from slowapi.util import get_remote_address

from app.api.schemas.ask import AskRequest
from app.core.config import DAILY_OPENAI_CALL_CAP
from app.core.db import get_connection, increment_daily_usage, release_connection
from app.core.limiter import limiter
from app.core.retrieval import answer_question, answer_question_stream
from app.services.enrichment import enrich_result

router = APIRouter()


@router.post("/ask")
@limiter.limit("10/minute;30/hour")
def ask(request: Request, req: AskRequest):
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "question must not be blank")
    # model is only meaningful for the local provider (a caller-supplied OpenAI model
    # id would otherwise pass straight through to a paid completion call unchecked).
    model = req.model if req.provider == "local" else None

    conn = get_connection()
    try:
        count = increment_daily_usage(conn)
        if count > DAILY_OPENAI_CALL_CAP:
            raise HTTPException(429, "Daily API usage cap reached -- try again tomorrow.")
        result = answer_question(
            conn, question, superseded_filter=req.superseded_filter,
            provider=req.provider, model=model, authority_filter=req.authority_filter,
            history=[h.model_dump() for h in req.history] if req.history else None,
            client_ip=get_remote_address(request),
        )
        enrich_result(conn, result)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        release_connection(conn)


@router.post("/ask-stream")
@limiter.limit("10/minute;30/hour")
def ask_stream(request: Request, req: AskRequest):
    """Same pipeline as /ask, but streams progress events (Server-Sent Events) as the
    pipeline advances, so the frontend can show a live reasoning trace instead of a
    blank spinner. Ends with a "done" event carrying the same payload /ask returns."""
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "question must not be blank")
    model = req.model if req.provider == "local" else None
    client_ip = get_remote_address(request)

    def event_stream():
        conn = get_connection()
        try:
            count = increment_daily_usage(conn)
            if count > DAILY_OPENAI_CALL_CAP:
                yield f"data: {json.dumps({'step': 'error', 'detail': 'Daily API usage cap reached -- try again tomorrow.'})}\n\n"
                return
            for event in answer_question_stream(
                conn, question, superseded_filter=req.superseded_filter,
                provider=req.provider, model=model, authority_filter=req.authority_filter,
                history=[h.model_dump() for h in req.history] if req.history else None,
                client_ip=client_ip,
            ):
                if event["step"] == "done":
                    enrich_result(conn, event["result"])
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'step': 'error', 'detail': str(e)})}\n\n"
        finally:
            release_connection(conn)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
