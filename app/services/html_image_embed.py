from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from typing import List, Tuple

from google.auth.transport.requests import AuthorizedSession

from app.services.gmail_client import get_google_credentials


@dataclass
class EmbeddedImage:
    cid: str
    content: bytes
    content_type: str
    filename: str


# Matches <img ... src="..."> for http(s) sources.
_IMG_SRC_RE = re.compile(
    r'(<img\b[^>]*\bsrc\s*=\s*)(["\'])(https?://[^"\'>\s]+)\2',
    re.IGNORECASE,
)


def _guess_content_type(url: str, header_ct: str | None) -> str:
    ct = (header_ct or "").split(";")[0].strip().lower()
    if ct.startswith("image/"):
        return ct
    guessed, _ = mimetypes.guess_type(url)
    if guessed and guessed.startswith("image/"):
        return guessed
    return "application/octet-stream"


def embed_remote_images_as_cid(
    *,
    db,
    html: str,
    cid_prefix: str = "sigimg",
    max_bytes: int = 2_500_000,
    timeout_sec: float = 10.0,
) -> Tuple[str, List[EmbeddedImage], List[str]]:
    """Fetch remote <img src=https://...> URLs and replace them with cid: references.

    This is the most reliable way to ensure images render in *sent* emails.
    Many clients (including Gmail) strip/ignore data: URIs in email HTML.

    Returns (new_html, embedded_images, warnings).

    We fetch using the same Google credentials the app already uses for Gmail,
    which helps with Google-hosted images that require auth.
    """
    if not html:
        return "", [], []

    warnings: List[str] = []
    embedded: List[EmbeddedImage] = []

    creds = get_google_credentials(db)
    authed = AuthorizedSession(creds)

    # Keep stable CID assignment per distinct URL within this message.
    url_to_cid: dict[str, str] = {}
    counter = 0

    def _fetch(url: str) -> tuple[bytes | None, str | None]:
        try:
            resp = authed.get(url, timeout=timeout_sec, headers={"User-Agent": "AgentBot/1.0"})
            if resp.status_code >= 400:
                warnings.append(f"Image fetch failed ({resp.status_code}): {url}")
                return None, None
            content = resp.content
            if len(content) > max_bytes:
                warnings.append(f"Image too large (skipped): {url}")
                return None, None
            ct = _guess_content_type(url, resp.headers.get("content-type"))
            if not ct.startswith("image/"):
                warnings.append(f"Image URL not image ({ct}): {url}")
                return None, None
            return content, ct
        except Exception:
            warnings.append(f"Image fetch error: {url}")
            return None, None

    def _repl(m: re.Match) -> str:
        nonlocal counter
        prefix, quote, url = m.group(1), m.group(2), m.group(3)

        # Assign CID for this URL
        if url not in url_to_cid:
            counter += 1
            url_to_cid[url] = f"{cid_prefix}{counter}"

        cid = url_to_cid[url]

        # Only fetch and embed once per URL
        if not any(e.cid == cid for e in embedded):
            content, ct = _fetch(url)
            if not content or not ct:
                return m.group(0)  # leave URL as-is
            ext = mimetypes.guess_extension(ct) or ""
            filename = f"{cid}{ext}"
            embedded.append(EmbeddedImage(cid=cid, content=content, content_type=ct, filename=filename))

        return f"{prefix}{quote}cid:{cid}{quote}"

    new_html = _IMG_SRC_RE.sub(_repl, html)
    return new_html, embedded, warnings
