from __future__ import annotations

import base64
import re
from typing import List, Tuple

import httpx


# Match <img ... src="https://..."> and capture: (prefix)(quote)(url)(quote)
_IMG_SRC_RE = re.compile(
    r'(<img\b[^>]*\bsrc\s*=\s*)(["\'])(https?://[^"\'>\s]+)\2',
    re.IGNORECASE,
)


def inline_signature_images(
    signature_html: str,
    *,
    timeout_sec: float = 10.0,
    max_bytes: int = 2_500_000,
) -> Tuple[str, List[str]]:
    """Inline remote images in a Gmail signature.

    Gmail signatures are stored as HTML and often reference remote images (logos)
    via <img src="https://...">. Those images frequently fail to render when
    shown inside apps (CORS/auth/Gmail proxy URLs), and can also fail for
    recipients if the URL is protected.

    The most reliable fix is to embed the bytes as a data: URI.

    Returns (new_html, warnings).
    """
    if not signature_html:
        return "", []

    warnings: List[str] = []

    timeout = httpx.Timeout(timeout_sec, connect=min(5.0, timeout_sec))
    client = httpx.Client(timeout=timeout, follow_redirects=True)

    def _fetch_as_data_uri(url: str) -> str | None:
        try:
            r = client.get(url, headers={"User-Agent": "AgentBotSignatureInliner/1.0"})
            if r.status_code >= 400:
                warnings.append(f"Signature image fetch failed ({r.status_code}): {url}")
                return None
            ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
            if not ctype.startswith("image/"):
                warnings.append(f"Signature image not an image ({ctype or 'unknown'}): {url}")
                return None
            content = r.content
            if len(content) > max_bytes:
                warnings.append(f"Signature image too large (skipped): {url}")
                return None
            b64 = base64.b64encode(content).decode("ascii")
            return f"data:{ctype};base64,{b64}"
        except Exception:
            warnings.append(f"Signature image fetch error: {url}")
            return None

    def _repl(m: re.Match) -> str:
        prefix, quote, url = m.group(1), m.group(2), m.group(3)
        data_uri = _fetch_as_data_uri(url)
        if not data_uri:
            return m.group(0)
        return f"{prefix}{quote}{data_uri}{quote}"

    try:
        new_html = _IMG_SRC_RE.sub(_repl, signature_html)
    finally:
        client.close()

    return new_html, warnings
