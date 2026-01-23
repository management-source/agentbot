from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from app.config import settings
from app.models import TicketCategory


AI_CATEGORY_KEYS = (
    "maintenance",
    "compliance",
    "rent_arrears",
    "lease_renewal",
    "notice_legal",
    "general",
)


AI_CATEGORY_TO_TICKET_CATEGORY: Dict[str, TicketCategory] = {
    "maintenance": TicketCategory.MAINTENANCE,
    "compliance": TicketCategory.COMPLIANCE,
    "rent_arrears": TicketCategory.RENT_ARREARS,
    "lease_renewal": TicketCategory.LEASING,
    # Keep "notice_legal" distinct for AI, but map to GENERAL in the existing enum.
    "notice_legal": TicketCategory.GENERAL,
    "general": TicketCategory.GENERAL,
}


def _norm(s: str) -> str:
    return (s or "").strip()


def content_hash(*parts: str) -> str:
    """Stable hash so we can avoid re-running AI on unchanged content."""
    h = hashlib.sha256()
    for p in parts:
        h.update((_norm(p) + "\n").encode("utf-8", errors="ignore"))
    return h.hexdigest()


def _keyword_hits(text: str, keywords: Tuple[str, ...]) -> bool:
    t = (text or "").lower()
    return any(k in t for k in keywords)


@dataclass
class AiTriageResult:
    ai_category: str
    ticket_category: TicketCategory
    urgency: int  # 1..5
    confidence_percent: int  # 0..100
    reasons: list[str]
    summary: str = ""


def _heuristic_triage(subject: str, snippet: str, body_text: str) -> AiTriageResult:
    """Fast, no-dependency triage that is safe to run even without OpenAI."""
    text = f"{subject}\n{snippet}\n{body_text}".lower()
    reasons: list[str] = []

    # --- Urgency floor (liability-safe) ---
    urgent_5 = (
        "gas leak",
        "smell gas",
        "carbon monoxide",
        "co2",
        "fire",
        "sparking",
        "electric shock",
        "flood",
        "flooding",
        "burst pipe",
        "sewage",
        "smoke alarm",
        "no water",
        "no hot water",
        "blocked toilet",
    )
    urgent_4 = (
        "urgent",
        "asap",
        "immediately",
        "notice to vacate",
        "breach",
        "vcat",
        "tribunal",
        "warrant",
        "police",
    )

    urgency_floor = 1
    if _keyword_hits(text, urgent_5):
        urgency_floor = 5
        reasons.append("Contains high-risk safety/essential service keywords")
    elif _keyword_hits(text, urgent_4):
        urgency_floor = 4
        reasons.append("Contains legal/urgent escalation keywords")

    # --- Category heuristics ---
    maintenance_kw = (
        "leak",
        "leaking",
        "plumber",
        "tap",
        "toilet",
        "shower",
        "hot water",
        "aircon",
        "air con",
        "electrical",
        "electrician",
        "gas",
        "smoke alarm",
        "mould",
        "broken",
        "repair",
        "maintenance",
    )
    compliance_kw = (
        "smoke alarm compliance",
        "gas compliance",
        "co2 compliance",
        "electrical safety",
        "compliance check",
        "council",
        "inspection",
    )
    arrears_kw = (
        "rent overdue",
        "arrears",
        "behind on rent",
        "late rent",
        "breach",
        "payment plan",
        "unpaid rent",
        "notice to vacate",
    )
    lease_kw = (
        "lease renewal",
        "renewal",
        "lease extended",
        "periodic",
        "fixed term",
        "rent increase",
        "increase notice",
        "agreement",
    )

    if _keyword_hits(text, arrears_kw):
        ai_cat = "rent_arrears"
        reasons.append("Matches rent arrears keywords")
    elif _keyword_hits(text, maintenance_kw):
        ai_cat = "maintenance"
        reasons.append("Matches maintenance keywords")
    elif _keyword_hits(text, compliance_kw):
        ai_cat = "compliance"
        reasons.append("Matches compliance keywords")
    elif _keyword_hits(text, lease_kw):
        ai_cat = "lease_renewal"
        reasons.append("Matches lease/renewal keywords")
    elif _keyword_hits(text, ("notice to vacate", "vcat", "tribunal")):
        ai_cat = "notice_legal"
        reasons.append("Matches notice/legal keywords")
    else:
        ai_cat = "general"
        reasons.append("No strong keyword match")

    # --- Urgency heuristic (layered on top of floor) ---
    urgency = urgency_floor
    if urgency < 4 and _keyword_hits(text, ("today", "tomorrow", "48 hours", "24 hours")):
        urgency = max(urgency, 3)
        reasons.append("Contains time-sensitive language")

    confidence = 70 if ai_cat != "general" else 55

    return AiTriageResult(
        ai_category=ai_cat,
        ticket_category=AI_CATEGORY_TO_TICKET_CATEGORY[ai_cat],
        urgency=max(1, min(5, urgency)),
        confidence_percent=max(0, min(100, confidence)),
        reasons=reasons[:5],
        summary="",
    )


def _openai_triage(subject: str, snippet: str, body_text: str) -> Optional[AiTriageResult]:
    if not settings.OPENAI_API_KEY:
        return None

    # Avoid sending excessive data.
    subj = _norm(subject)[:200]
    snip = _norm(snippet)[:800]
    body = _norm(body_text)[:2000]

    prompt = (
        "Classify and triage this email for a property management inbox.\n"
        "Return ONLY valid JSON with keys: category, urgency, confidence, reasons, summary.\n"
        "category must be one of: maintenance, compliance, rent_arrears, lease_renewal, notice_legal, general.\n"
        "urgency must be an integer 1..5.\n"
        "confidence must be an integer 0..100.\n"
        "reasons must be a short array of strings (max 5).\n"
        "summary must be a one-sentence neutral summary (max 25 words).\n\n"
        f"Subject: {subj}\n"
        f"Snippet: {snip}\n"
        f"Body: {body}\n"
    )

    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        resp = client.responses.create(
            model=settings.OPENAI_MODEL,
            input=[
                {
                    "role": "system",
                    "content": "You classify property management emails and output strict JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
        )

        raw = (resp.output_text or "").strip()
        # Best-effort: extract JSON object from the response.
        m = re.search(r"\{.*\}", raw, flags=re.S)
        if not m:
            return None
        obj = json.loads(m.group(0))

        cat = str(obj.get("category") or "general").strip().lower()
        if cat not in AI_CATEGORY_KEYS:
            cat = "general"

        urgency = int(obj.get("urgency") or 1)
        urgency = max(1, min(5, urgency))

        confidence = int(obj.get("confidence") or 50)
        confidence = max(0, min(100, confidence))

        reasons = obj.get("reasons") or []
        if not isinstance(reasons, list):
            reasons = []
        reasons = [str(r)[:120] for r in reasons][:5]

        summary = str(obj.get("summary") or "").strip()[:200]

        return AiTriageResult(
            ai_category=cat,
            ticket_category=AI_CATEGORY_TO_TICKET_CATEGORY[cat],
            urgency=urgency,
            confidence_percent=confidence,
            reasons=reasons,
            summary=summary,
        )
    except Exception:
        return None


def triage_email(subject: str, snippet: str, body_text: str) -> AiTriageResult:
    """Hybrid triage: rules + optional OpenAI, with urgency floor enforcement."""
    base = _heuristic_triage(subject, snippet, body_text)
    ai = _openai_triage(subject, snippet, body_text)
    if not ai:
        return base

    # Enforce urgency floor from rules.
    urgency = max(base.urgency, ai.urgency)
    # If AI selects an unknown category, keep base.
    cat = ai.ai_category if ai.ai_category in AI_CATEGORY_KEYS else base.ai_category
    ticket_cat = AI_CATEGORY_TO_TICKET_CATEGORY.get(cat, base.ticket_category)

    reasons = (ai.reasons or [])
    if not reasons:
        reasons = base.reasons

    return AiTriageResult(
        ai_category=cat,
        ticket_category=ticket_cat,
        urgency=urgency,
        confidence_percent=ai.confidence_percent or base.confidence_percent,
        reasons=reasons[:5],
        summary=ai.summary or base.summary,
    )


def detect_sender_role(from_email: str | None) -> str:
    """Best-effort role detection used for drafting.

    Returns one of: tenant, landlord, tradie, council, other.
    """
    e = (from_email or "").lower().strip()
    if not e:
        return "other"
    if any(k in e for k in ("council", ".gov", ".vic.gov")):
        return "council"
    # crude but effective: most tradies use business domains or known platforms.
    if any(k in e for k in ("hipages", "service", "plumbing", "electrical", "maintenance", "tradie")):
        return "tradie"
    return "other"


def draft_context_reply(
    *,
    from_name: Optional[str],
    from_email: Optional[str],
    subject: str,
    last_message_text: str,
    ai_category: str,
    urgency: int,
    tone: str = "neutral",
) -> Tuple[str, str, Dict[str, Any]]:
    """Draft a contextual reply.

    Returns (reply_subject, reply_body, meta).
    Always returns a safe fallback if OpenAI is not configured.
    """
    safe_subject = subject.strip() or "(no subject)"
    reply_subject = f"Re: {safe_subject}"

    name = (from_name or "").strip()
    greeting = f"Hello {name}," if name else "Hello,"
    role = detect_sender_role(from_email)

    # Safe fallback templates (human-in-the-loop)
    if ai_category == "maintenance":
        body = (
            f"{greeting}\n\n"
            "Thank you for your email. We have noted the maintenance request and will review the details. "
            "We will be in touch shortly with the next steps (including arranging access if required).\n\n"
            "Kind regards,"
        )
    elif ai_category == "rent_arrears":
        body = (
            f"{greeting}\n\n"
            "Thank you for your email. We have noted your message regarding rent and will review the tenant ledger. "
            "We will follow up shortly with an update.\n\n"
            "Kind regards,"
        )
    elif ai_category == "compliance":
        body = (
            f"{greeting}\n\n"
            "Thank you for your email. We have noted the compliance matter and will review what is required. "
            "We will follow up shortly with confirmation of next steps.\n\n"
            "Kind regards,"
        )
    elif ai_category == "lease_renewal":
        body = (
            f"{greeting}\n\n"
            "Thank you for your email. We have received your message regarding the lease/tenancy and will review the details. "
            "We will be in touch shortly with an update.\n\n"
            "Kind regards,"
        )
    else:
        body = (
            f"{greeting}\n\n"
            "Thank you for your email. We have received your message and will respond shortly.\n\n"
            "Kind regards,"
        )

    if not settings.OPENAI_API_KEY:
        return reply_subject, body, {"role": role, "used_ai": False}

    # OpenAI enhanced draft
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        prompt = (
            "Draft a professional email reply for a property management agency.\n"
            "Constraints:\n"
            "- Be concise and neutral; avoid admissions of liability.\n"
            "- Do not promise specific timelines unless generic (e.g., 'shortly').\n"
            "- Use Australian English.\n"
            "- End with 'Kind regards,' only (no name).\n"
            "- If information is missing, ask up to 2 clarifying questions.\n\n"
            f"Sender role hint: {role}\n"
            f"Category: {ai_category}\n"
            f"Urgency (1-5): {urgency}\n"
            f"Requested tone adjustment: {tone}\n\n"
            f"Original subject: {safe_subject}\n"
            f"Latest message (plain text):\n{_norm(last_message_text)[:2000]}\n"
        )

        resp = client.responses.create(
            model=settings.OPENAI_MODEL,
            input=[
                {
                    "role": "system",
                    "content": "You draft legally cautious, professional property-management email replies.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        text = (resp.output_text or "").strip()
        if not text:
            return reply_subject, body, {"role": role, "used_ai": False}

        # Ensure greeting exists
        if not text.lower().startswith(("hi", "hello", "dear")):
            text = f"{greeting}\n\n{text}"

        return reply_subject, text, {"role": role, "used_ai": True}
    except Exception:
        return reply_subject, body, {"role": role, "used_ai": False}
