from __future__ import annotations
from app.config import settings

def draft_acknowledgement(from_name: str | None, subject: str, snippet: str) -> tuple[str, str]:
    """
    MVP: if OPENAI_API_KEY is not set, fall back to a safe template.
    """
    safe_subject = subject.strip() or "(no subject)"
    reply_subject = f"Re: {safe_subject}"

    # Fallback template (always safe)
    fallback = (
        "Thank you for your email. We have received your message and will respond shortly.\n\n"
        "Kind regards,"
    )

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
            f"Original subject: {safe_subject}\n"
            f"Original snippet: {snippet[:600]}\n"
        )

        resp = client.responses.create(
            model=settings.OPENAI_MODEL,
            input=[
                {"role": "system", "content": "You draft concise formal acknowledgment emails."},
                {"role": "user", "content": prompt},
            ],
        )

        text = (resp.output_text or "").strip()
        if not text:
            text = fallback

        # Ensure it contains a greeting (optional)
        if not text.lower().startswith(("hi", "hello", "dear")):
            text = f"Hello {name_part}\n\n{text}"

        return reply_subject, text

    except Exception:
        return reply_subject, fallback
