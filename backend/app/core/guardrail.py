"""LLM relevance guardrail for /ask (and /ask-stream).

Why this exists alongside the numeric confidence tier: the tier judges *embedding
proximity*, which genuinely misfires on playful off-topic questions -- measured live:
"What shoe size is Messi?" scores 0.2287 ("size" embeds near "burn size estimation"
plus cover-page template noise), clearing the 0.15 abstain floor and producing a
sourced low-confidence answer. No threshold fixes that; only a reader of the text can.

Pipeline position (the cheap middle ground): embed + search + fuse always run first
(one tiny embedding call + local Postgres -- cheap), the free numeric floor still
abstains below 0.15 with zero LLM spend, and only then does this gate run -- on the
question plus the top-3 chunk texts, with a hard-capped short verdict. Full
generation (the expensive call, up to 7 chunks of context) happens only on pass.

Fail-open everywhere: any exception from the guardrail LLM call (LM Studio down,
timeout, fallback unavailable), or an unparseable verdict, returns relevant=True and
the pipeline proceeds down the old numeric path. A guardrail outage must never block
real questions -- same spirit as enrich_result() and the cache-gate wrappers.

The OFF_TOPIC message is composed here from a fixed template (deterministic wording
across web/CLI/streaming/history), never LLM prose: the model only supplies the
subject line and redirect suggestions. The redirect topic is validated against
REDIRECT_ALLOWLIST so the model can't invent a regulation area ("DHA shoe
rules"); suggestions are capped, stripped, and inherently safe anyway -- clicking
one re-enters the full RAG pipeline, which abstains if it turns out unanswerable.
"""
from __future__ import annotations

import logging
import re

from app.core.safety import build_user_message

logger = logging.getLogger(__name__)

GUARDRAIL_MAX_CHUNKS = 3  # chunk excerpts fed to the judge (text only, truncated)
GUARDRAIL_CHUNK_CHARS = 600  # per-excerpt cap -- bounds the prompt, keeps it fast
GUARDRAIL_MAX_TOKENS = 400  # verdict is 4 short lines; hard cap keeps it cheap
GUARDRAIL_SUGGESTION_CAP = 3
GUARDRAIL_SUGGESTION_CHARS = 200  # overlong "suggestions" are dropped, not truncated

# In-scope areas the corpus actually covers. The judge must pick the redirect from
# these (validated by validate_redirect()); anything else falls back to GENERIC.
# Deliberately broad labels, not doc codes -- they feed a human sentence, and the
# backend canonicalizes to these exact strings.
REDIRECT_ALLOWLIST = [
    "licensing of healthcare professionals",
    "licensing of health facilities",
    "telehealth and telemedicine standards",
    "patient safety and quality standards",
    "mental health services",
    "health data governance and reporting",
    "research ethics and trusted research environments",
    "clinical guidelines and standards of care",
]
GENERIC_REDIRECT = "licensing of healthcare professionals and health facilities"

OFFTOPIC_MESSAGE_TEMPLATE = (
    "This looks like it's about {subject}, which is out of scope for UAE health "
    "regulation (DHA, DoH and MOHAP). However, you can check {redirect}."
)
OFFTOPIC_MESSAGE_NO_SUBJECT = (
    "This question looks out of scope for UAE health regulation "
    "(DHA, DoH and MOHAP). However, you can check {redirect}."
)


def _build_messages(question: str, chunks: list[dict]) -> list[dict]:
    excerpts = "\n\n".join(
        f"--- Excerpt {i + 1} ---\n{(c.get('text') or '')[:GUARDRAIL_CHUNK_CHARS]}"
        for i, c in enumerate(chunks[:GUARDRAIL_MAX_CHUNKS])
    )
    return [
        {
            "role": "system",
            "content": (
                "You are a domain gate for a Q&A system that answers questions about "
                "UAE healthcare regulation and practice (Dubai Health Authority, "
                "Department of Health Abu Dhabi, MOHAP). Anything a licensed UAE "
                "healthcare facility or professional must know or do is IN SCOPE: "
                "licensing and renewal, scopes of practice, staffing and qualification "
                "requirements, facility design, capacity and equipment, clinical "
                "standards and care protocols, documentation, fees, timelines, "
                "inspections, patient safety, health data -- and such questions are IN "
                "SCOPE even when very specific, operational, or administrative. The "
                "excerpts below are the nearest retrieved passages; they may be weak "
                "or unrelated. If the question is about UAE healthcare, verdict "
                "RELEVANT. If any excerpt is from a UAE health authority document and "
                "addresses the question's subject even partially, verdict MUST be "
                "RELEVANT. Only OFF_TOPIC when the question's subject is clearly "
                "outside healthcare (sports, celebrities, politics, weather, travel, "
                "unrelated law, etc.). Reply with EXACTLY these four labeled lines and "
                "nothing else:\n"
                "VERDICT: RELEVANT or OFF_TOPIC\n"
                "SUBJECT: what the question is about, in a few words "
                "(e.g. \"a footballer's shoe size\")\n"
                "REDIRECT: the nearest of these areas, or \"general\": "
                + "; ".join(REDIRECT_ALLOWLIST) + "\n"
                "SUGGESTIONS: 1-3 concrete in-scope questions the user could ask "
                "instead, separated by \" | \", each under 140 characters -- or the "
                "single word \"none\" if nothing fits."
            ),
        },
        # safety.build_user_message, not an inline dict: the judge's system prompt is
        # fixed text only, and the question goes in the user role structurally rather
        # than by convention. Same bytes as before.
        build_user_message(question, excerpts),
    ]


def parse_verdict(text: str) -> dict:
    """Lenient parse of the 4-line verdict. Ambiguity resolves to RELEVANT
    (fail-open): an unparseable verdict must never block a real question."""
    verdict_match = re.search(r"^\s*VERDICT\s*:\s*(OFF_TOPIC|RELEVANT)\s*$", text, re.IGNORECASE | re.MULTILINE)
    is_relevant = True
    if verdict_match:
        is_relevant = verdict_match.group(1).upper() != "OFF_TOPIC"

    def _line(label: str) -> str:
        m = re.search(rf"^\s*{label}\s*:\s*(.+?)\s*$", text, re.IGNORECASE | re.MULTILINE)
        return (m.group(1).strip() if m else "")

    subject = _line("SUBJECT").strip("\"' ")
    redirect = validate_redirect(_line("REDIRECT"))

    raw_suggestions = _line("SUGGESTIONS")
    suggestions: list[str] = []
    if raw_suggestions and raw_suggestions.lower() != "none":
        for part in re.split(r"\s*\|\s*|\n+", raw_suggestions):
            s = re.sub(r"^[\d\-\*\•\.]+\s*", "", part.strip()).strip("\"' ")
            if s and len(s) <= GUARDRAIL_SUGGESTION_CHARS:
                suggestions.append(s)
            if len(suggestions) >= GUARDRAIL_SUGGESTION_CAP:
                break

    return {"is_relevant": is_relevant, "subject": subject, "redirect": redirect, "suggestions": suggestions}


def validate_redirect(topic: str) -> str:
    """Canonicalizes the model's redirect against REDIRECT_ALLOWLIST (case-insensitive
    substring either way); anything unrecognized becomes GENERIC_REDIRECT so the
    composed message can never name an invented regulation area."""
    tl = (topic or "").strip().lower()
    if not tl or tl == "general":
        return GENERIC_REDIRECT
    for entry in REDIRECT_ALLOWLIST:
        if entry in tl or tl in entry:
            return entry
    return GENERIC_REDIRECT


def build_offtopic_message(subject: str, redirect: str) -> str:
    """Fixed template -- the only off-topic wording the product ever emits."""
    if (subject or "").strip():
        return OFFTOPIC_MESSAGE_TEMPLATE.format(subject=subject.strip(), redirect=redirect)
    return OFFTOPIC_MESSAGE_NO_SUBJECT.format(redirect=redirect)


def check_relevance(
    question: str, chunks: list[dict], client_ip: str | None = None,
) -> dict:
    """Returns {"is_relevant", "message", "suggestions"}. Never raises to the
    pipeline: any failure (model down, timeout, fallback unconfigured) logs and
    returns relevant=True so the numeric tiering decides, as before.

    Judge model: ALWAYS chat_completion's OpenAI->NaraRouter chain (currently
    gpt-4o-mini), never the caller's selected provider. Measured live: local qwen 4B
    rejected specific operational questions ("How many students may one school nurse
    cover...") that gpt-4o-mini accepts with identical retrieved evidence, and a bad
    verdict silently suppresses a real answer -- the one place a weaker model is not
    an acceptable trade. The verdict is 4 short lines, so the cost stays trivial.
    Exact mined questions skip this gate altogether (see
    retrieval._safe_check_relevance). Retrieval-side imports are deferred to call
    time: this module is imported BY retrieval.py, so a top-level import back would
    be circular."""
    from app.core.retrieval import chat_completion

    try:
        messages = _build_messages(question, chunks)
        resp, _ = chat_completion(
            messages, client_ip=client_ip, max_tokens=GUARDRAIL_MAX_TOKENS,
        )
        # chat_completion is non-streaming here, but NaraRouter has emitted a
        # trailing chunk with an empty choices list before -- guard the same way
        # the streaming path does rather than indexing blindly.
        choices = getattr(resp, "choices", None) or []
        content = (choices[0].message.content if choices else None) or ""
        parsed = parse_verdict(content)
        if parsed["is_relevant"]:
            return {"is_relevant": True, "message": "", "suggestions": []}
        return {
            "is_relevant": False,
            "message": build_offtopic_message(parsed["subject"], parsed["redirect"]),
            "suggestions": parsed["suggestions"],
        }
    except Exception:
        logger.warning("relevance guardrail failed; falling back to numeric tiering", exc_info=True)
        return {"is_relevant": True, "message": "", "suggestions": []}
