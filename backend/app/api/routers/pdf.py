"""GET /pdf/{document_id}."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from app.core.config import DATASET_DIR
from app.core.db import get_connection, release_connection
from app.core.limiter import limiter
from app.core.urls import filename_from_url

router = APIRouter()


@router.get("/pdf/{document_id}")
@limiter.limit("30/minute")
def get_pdf(request: Request, document_id: int):
    """Serves the locally cached source PDF (downloaded during ingestion) rather than
    proxying the live DHA/DoH URL — avoids depending on the government site being
    reachable during a live demo, and lets us force inline display (their .ashx
    endpoints force a download via Content-Disposition: attachment)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT source_url FROM documents WHERE id = %s", (document_id,))
            row = cur.fetchone()
    finally:
        release_connection(conn)

    if not row or not row[0]:
        raise HTTPException(404, "no source URL on record for this document")

    filename = filename_from_url(row[0])
    path = DATASET_DIR / filename
    if not path.exists():
        raise HTTPException(404, f"source PDF not found on disk: {filename}")

    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
