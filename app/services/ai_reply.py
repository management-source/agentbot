from __future__ import annotations

from app.config import settings
from app.services.ai_client import openai_text_completion


def draft_acknowledgement(
    from_name: str | None,
    subject: str,
    snippet: str,
    ai_category: str | None = None,
    ai_urgency: int | None = None,
) -> tuple[str, str]:
    """
    MVP: if OPENAI_API_KEY is not set, fall back to a safe template.
    """
    safe_subject = subject.strip() or "(no subject)"
    reply_subject = f"Re: {safe_subject}"

    # Category-aware fallback (always safe, and varies so it doesn't look "samey")
    safe_first = (from_name or "there").strip().split(" ")[0] or "there"
    cat = (ai_category or "general").strip().lower()
    urgent = (ai_urgency or 0)

    if cat in {"maintenance"}:
        fallback = (
            f"Hello {safe_first},\n\n"
            "Thank you for your email. We have received your maintenance request and will review it shortly. "
            "If you can confirm your preferred access times (and whether pets are on site), we can progress this faster.\n\n"
            "Kind regards,"
        )
    elif cat in {"compliance"}:
        fallback = (
            f"Hello {safe_first},\n\n"
            "Thank you for your email. We have received your message regarding compliance and will review the details shortly. "
            "We will be in touch if we require any further information to arrange access.\n\n"
            "Kind regards,"
        )
    elif cat in {"rent_arrears"}:
        fallback = (
            f"Hello {safe_first},\n\n"
            "Thank you for your email. We have received your message regarding rent and will review the account shortly. "
            "If you have a recent payment reference/receipt, please reply with it so we can reconcile promptly.\n\n"
            "Kind regards,"
        )
    elif cat in {"lease_renewal"}:
        fallback = (
            f"Hello {safe_first},\n\n"
            "Thank you for your email. We have received your message regarding the lease/renewal and will review it shortly. "
            "We will update you once we have confirmed the next steps.\n\n"
            "Kind regards,"
        )
    elif cat in {"notice_legal"}:
        fallback = (
            f"Hello {safe_first},\n\n"
            "Thank you for your email. We have received your message and will review it as a priority. "
            "If there are any key dates or deadlines, please reply with them so we can ensure compliance.\n\n"
            "Kind regards,"
        )
    else:
        fallback = (
            f"Hello {safe_first},\n\n"
            "Thank you for your email. We have received your message and will respond shortly.\n\n"
            "Kind regards,"
        )

    # Simple urgency cue (still safe)
    if urgent >= 4 and "priority" not in fallback.lower():
        fallback = fallback.replace("will review", "will review as a priority")

    if not settings.OPENAI_API_KEY:
        return reply_subject, fallback

    # Use OpenAI if configured
    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.OPENAI_API_KEY)

        name_part = f"{from_name}," if from_name else "there,"
        prompt = (
            "Write a short, formal email acknowledgment reply.\n"
            "- 2–3 sentences max\n"
            "- Professional and neutral tone\n"
            "- Do not promise specific timelines unless generic ('shortly')\n"
            "- No bullet points\n"
            "- End with 'Kind regards,' only (no name)\n\n"
            f"AI category: {cat}\n"
            f"AI urgency (1-5): {urgent}\n"
            f"Original subject: {safe_subject}\n"
            f"Original snippet: {snippet[:600]}\n"
        )

        text = openai_text_completion(
            client,
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You draft concise formal acknowledgment emails."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=220,
        )
        if not text:
            text = fallback

        # Ensure it contains a greeting (optional)
        if not text.lower().startswith(("hi", "hello", "dear")):
            text = f"Hello {name_part}\n\n{text}"

        return reply_subject, text

    except Exception:
        return reply_subject, fallback
