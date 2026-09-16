"""Input safety screen for /ask and /ask-stream: prompt-injection blocking and PII
redaction, plus the one helper that keeps every piece of user text inside the `user`
role.

Deliberately minimal and deterministic -- regex only, no model call, no pluggable
backend. A screen that costs an LLM verdict to decide whether to spend an LLM verdict
is not a screen; Presidio/Llama Guard/NeMo are upgrades this can grow into, not
day-one needs.

Two different failure modes, so two different postures:

*Injection fails CLOSED.* A match returns a fixed refusal and the pipeline stops
before the cache probe -- no embedding, no LLM call, no cache read, no cache write.
The cost of a false negative (a successful instruction override on a compliance tool
people trust to quote regulation verbatim) is far worse than the cost of a false
positive, so the patterns still have to be tight (see below).

*PII fails OPEN.* A match redacts to a typed token and the question continues on the
redacted text. Blocking would punish a user for pasting a patient's phone number into
an otherwise legitimate compliance question; redacting keeps the answer working while
keeping the raw identifier out of the embedding call, the cache key, the LangSmith
trace and the persisted chat history -- everything downstream sees the token, because
`safe_text` is what the pipeline actually runs on.

The hard constraint on the injection patterns: **an over-eager pattern that eats real
questions is worse than no screen at all.** This corpus is full of words an injection
filter reaches for -- "ignore", "rules", "instructions", "act as", "override",
"system". So no pattern here fires on a verb alone: every instruction-override pattern
requires the verb AND a deictic reference to this conversation's own instructions
("your", "previous", "above"), and every extraction pattern requires the possessive or
the literal words "system prompt". "What should be ignored under the retention rule?"
and "What are the instructions for renewing a licence?" both pass straight through,
which is the whole point. See tests/test_safety.py, where ten real compliance
questions are asserted to survive.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- injection ---

# Verbs that introduce a request to disclose the conversation's own configuration.
# Only meaningful in combination with a target below -- never on their own.
_REVEAL_VERB = (
    r"(?:reveal|show|print|repeat|output|display|disclose|expose|dump|echo|leak|"
    r"recite|spit\s+out|give\s+me|tell\s+me|list|what\s+(?:is|are|was|were)|"
    r"what's|whats)"
)
# What that verb has to be aimed at. Split in two on purpose: the possessive form
# ("your instructions") is an extraction attempt, while the bare article form ("the
# instructions") is an ordinary question about a regulation's instructions and must
# never match -- which is why "the" only ever appears attached to "system prompt" and
# friends here, never to a bare noun.
_EXTRACTION_TARGET = (
    r"(?:"
    r"your\s+(?:system\s+|initial\s+|original\s+|hidden\s+|secret\s+|internal\s+|"
    r"developer\s+)?(?:prompt|prompts|instructions?|system\s+message)"
    r"|(?:the\s+)?(?:system|initial|original|hidden|developer)\s+prompt"
    r"|developer\s+(?:message|instructions?)"
    r")"
)

INJECTION_PATTERNS: list[re.Pattern] = [
    # -- instruction override: verb + deictic + instruction-noun, all three required.
    re.compile(
        r"\b(?:ignore|disregard|forget|discard|override|bypass|skip)(?:s|d|es|ed|ing)?\s+"
        r"(?:all\s+|any\s+|the\s+|every\s+)*"
        r"(?:previous|prior|preceding|earlier|above|foregoing|initial|original|system|your|these|those)\s+"
        r"(?:\w+\s+){0,2}?"
        r"(?:instruction|instructions|prompt|prompts|direction|directions|directive|directives|"
        r"rule|rules|guideline|guidelines|command|commands|constraint|constraints|"
        r"restriction|restrictions|message|messages)\b",
        re.IGNORECASE,
    ),
    # -- "disregard the above" / "ignore everything above": no noun to lean on, so the
    # verb stays strictly imperative (no -ed/-ing) to keep a phrase like "records
    # ignored above the threshold" out.
    re.compile(
        r"\b(?:ignore|disregard|forget)\s+(?:the\s+|everything\s+|all\s+|anything\s+)?"
        r"(?:that\s+came\s+|written\s+|said\s+|stated\s+)?"
        r"(?:above|preceding|foregoing|before\s+this)\b",
        re.IGNORECASE,
    ),
    # -- "stop following your instructions" / "do not obey the previous rules".
    re.compile(
        r"\b(?:do\s+not|don'?t|stop|no\s+longer)\s+"
        r"(?:follow|following|obey|obeying|adhere\s+to|comply\s+with)\s+"
        r"(?:the\s+|any\s+)?"
        r"(?:previous|prior|above|earlier|original|initial|system|your)\s+"
        r"(?:\w+\s+){0,2}?"
        r"(?:instruction|instructions|prompt|prompts|rule|rules|guideline|guidelines)\b",
        re.IGNORECASE,
    ),
    # -- system-prompt extraction: a reveal verb within a short window of the target.
    re.compile(_REVEAL_VERB + r"\b[^.\n]{0,40}?\b" + _EXTRACTION_TARGET, re.IGNORECASE),
    # -- extraction phrasings that carry no reveal verb of their own.
    re.compile(
        r"\brepeat\s+(?:everything|all|the\s+text|the\s+words|the\s+message)\s+(?:above|before)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bwhat\s+(?:were|was)\s+you\s+(?:told|instructed|programmed|trained)\b", re.IGNORECASE),
    # -- role reassignment. Each one is anchored on the assistant itself ("you") or on
    # a jailbreak term, so "Can a pharmacist act as an authorised signatory?" is safe.
    re.compile(r"\byou\s+are\s+(?:now|no\s+longer|from\s+now\s+on)\b", re.IGNORECASE),
    re.compile(r"\bfrom\s+now\s+on,?\s+you\b", re.IGNORECASE),
    re.compile(r"\byou\s+(?:must|will|shall|should)\s+now\s+(?:act|behave|respond|pretend|be)\b", re.IGNORECASE),
    re.compile(r"\bpretend\s+(?:that\s+)?(?:you|to\s+be)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:act|behave|respond|talk|roleplay|role-play)\s+as\s+"
        r"(?:if\s+)?(?:you\b|an?\s+(?:unrestricted|unfiltered|uncensored|unlimited|"
        r"jailbroken|evil|rogue|different)\b)",
        re.IGNORECASE,
    ),
    re.compile(r"\bimagine\s+(?:that\s+)?you\s+(?:are|were)\b", re.IGNORECASE),
    re.compile(r"\bsimulate\s+(?:being|that\s+you)\b", re.IGNORECASE),
    re.compile(r"\byour\s+new\s+(?:role|instructions?|task|persona|identity)\b", re.IGNORECASE),
    re.compile(r"\b(?:dan\s+mode|developer\s+mode|jailbreak|jailbroken|do\s+anything\s+now)\b", re.IGNORECASE),
    re.compile(r"\byou\s+have\s+no\s+(?:restrictions|rules|limits|limitations|guidelines|filters)\b", re.IGNORECASE),
    # -- delimiter smuggling: chat-template markup has no business in a user question.
    re.compile(r"\[\s*/?\s*(?:system|inst|assistant)\s*\]", re.IGNORECASE),
    re.compile(r"<\s*\|?\s*/?\s*(?:system|im_start|im_end|endoftext|assistant)\s*\|?\s*>", re.IGNORECASE),
    re.compile(r"<<\s*/?\s*sys\s*>>", re.IGNORECASE),
    re.compile(r"^\s*#{2,}\s*system\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*system\s*:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"[\"']?\brole\b[\"']?\s*:\s*[\"']?\s*system\b", re.IGNORECASE),
]

# The only wording a blocked question ever gets back. Fixed, like guardrail.py's
# off-topic template -- never model prose, and never an explanation of which pattern
# matched (that is just a probing oracle).
INJECTION_REFUSAL = (
    "I can't act on instructions that try to change how I work or reveal how I'm "
    "configured. Ask a question about UAE health regulation (DHA, DoH or MOHAP) and "
    "I'll answer it from the published source documents."
)

# ---------------------------------------------------------------------- PII ---

REDACTION_TOKENS = {
    "email": "[REDACTED_EMAIL]",
    "emirates_id": "[REDACTED_EMIRATES_ID]",
    "phone": "[REDACTED_PHONE]",
}

# Order matters: the Emirates ID runs first because its 15 digits would otherwise be
# partly eaten by the phone patterns, leaving a half-redacted identifier behind.
PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Emirates ID: 784-YYYY-NNNNNNN-C, also accepted unpunctuated as 15 digits.
    ("emirates_id", re.compile(r"\b784[-\s]?\d{4}[-\s]?\d{7}[-\s]?\d\b")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    # UAE mobile/landline in international form: +971 50 123 4567, 00971502345678.
    ("phone", re.compile(r"(?:\+|00)971[-\s.]?\(?0?\)?[-\s.]?\d{1,2}[-\s.]?\d{3}[-\s.]?\d{4}\b")),
    # UAE local form: 050 123 4567 / 04 123 4567.
    ("phone", re.compile(r"\b0\d{1,2}[-\s.]?\d{3}[-\s.]?\d{4}\b")),
    # Any other international number. A leading "+" is required on purpose: a bare run
    # of digits in this corpus is far more likely a clause number, a fee or a year
    # than a phone number.
    ("phone", re.compile(r"\+\d{1,3}[-\s.]?\(?\d{1,4}\)?[-\s.]?\d{3,4}[-\s.]?\d{3,4}\b")),
]


def redact_pii(text: str) -> str:
    """Replaces identifiers with typed tokens. Typed, not a generic [REDACTED], so the
    question still reads as a question to the retriever and to a human reading the
    trace -- "what is the penalty for sharing [REDACTED_EMIRATES_ID]" keeps its shape."""
    out = text or ""
    for kind, pattern in PII_PATTERNS:
        out = pattern.sub(REDACTION_TOKENS[kind], out)
    return out


def contains_injection(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in INJECTION_PATTERNS)


# ------------------------------------------------------------------ screen ---


def screen_question(raw: str) -> dict:
    """Returns {"safe_text", "blocked", "reason"}.

    Callers must use `safe_text` (not the original) for everything downstream --
    embedding, cache key, retrieval, generation, persistence -- which is what keeps
    redacted PII out of all of them. On `blocked`, the caller returns `reason` as a
    fixed abstention and does nothing else at all."""
    text = raw or ""
    if contains_injection(text):
        # Still hand back the redacted form: a blocked question never reaches a model,
        # but it can still reach a log line.
        return {"safe_text": redact_pii(text), "blocked": True, "reason": INJECTION_REFUSAL}
    return {"safe_text": redact_pii(text), "blocked": False, "reason": ""}


def build_user_message(question: str, context: str = "") -> dict:
    """The single place user-supplied text is turned into a chat message -- and it is
    always the `user` role, never the system prompt.

    Both retrieval._build_messages() and guardrail._build_messages() already did this
    correctly by convention; routing both through one function makes it structural, so
    "no user text in the system prompt" is a property a test can assert once instead of
    a rule every future prompt edit has to remember. `context` is the already-formatted
    source block (whose label differs between the two callers) and is appended
    verbatim."""
    content = f"Question: {question}"
    if context:
        content += f"\n\n{context}"
    return {"role": "user", "content": content}
