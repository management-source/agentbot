from typing import List, Dict, Any


def openai_text_completion(
    client,
    *,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.3,
    max_tokens: int = 600,
) -> str:
    """
    Compatibility wrapper for OpenAI SDKs:
    - Supports newer `client.responses`
    - Supports v1.x `client.chat.completions`
    - Supports legacy openai==0.x
    """

    # Newer SDKs (Responses API)
    if hasattr(client, "responses"):
        r = client.responses.create(
            model=model,
            input=messages,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        if hasattr(r, "output_text") and r.output_text:
            return r.output_text.strip()

        try:
            parts = []
            for item in (r.output or []):
                for c in (item.content or []):
                    if getattr(c, "type", None) in ("output_text", "text"):
                        parts.append(getattr(c, "text", "") or "")
            return "\n".join(parts).strip()
        except Exception:
            return ""

    # Most common SDK path (OpenAI >=1.0.0)
    if hasattr(client, "chat") and hasattr(client.chat, "completions"):
        r = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (r.choices[0].message.content or "").strip()

    # Legacy SDK (openai==0.x)
    import openai
    r = openai.ChatCompletion.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (r["choices"][0]["message"]["content"] or "").strip()
