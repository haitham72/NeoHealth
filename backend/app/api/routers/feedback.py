"""POST /report-answer."""
from fastapi import APIRouter, Request

from app.api.schemas.feedback import ReportAnswerRequest
from app.core.limiter import limiter

router = APIRouter()


@router.post("/report-answer")
@limiter.limit("10/minute;30/hour")
def report_answer(request: Request, req: ReportAnswerRequest):
    """User-triggered feedback on a specific answer, logged to LangSmith against the
    run_id that produced it (see app.core.retrieval's _current_run_id()) rather than a
    new Postgres table -- same "review signal lives in LangSmith" precedent as
    _flag_low_confidence(). Never raises: a broken run_id or a LangSmith outage
    degrades to {"success": False}, not a 500, since failing to log feedback should
    never look like the report itself crashed the app to the user submitting it."""
    try:
        from langsmith import Client

        Client().create_feedback(
            run_id=req.run_id,
            key="user_report",
            value=req.reason,
            comment=req.comment,
        )
        return {"success": True}
    except Exception as e:
        return {"success": False, "reason": str(e)}
