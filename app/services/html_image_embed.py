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
_REMOTE_IMG_SRC_RE = re.compile(
    r'(<img\b[^>]*\bsrc\s*=\s*)(["\'])(https?://[^"\'>\s]+)\2',
    re.IGNORECASE,
)

# Matches <img ... src="/static/signature/..."> local assets.
_LOCAL_IMG_SRC_RE = re.compile(
    r'(<img\b[^>]*\bsrc\s*=\s*)(["\'])(/static/signature/[^"\'>\s]+)\2',
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

    new_html = _REMOTE_IMG_SRC_RE.sub(_repl, html)
    return new_html, embedded, warnings


def embed_local_signature_images_as_cid(
    *,
    html: str,
    static_dir: str,
    cid_prefix: str = "siglocal",
) -> Tuple[str, List[EmbeddedImage], List[str]]:
    """Replace local /static/signature/* URLs with cid: and attach their bytes.

    These assets live inside the app and are therefore fully controllable. This is
    the most reliable mechanism to ensure signature icons / logos render in Gmail.
    """
    if not html:
        return "", [], []

    warnings: List[str] = []
    embedded: List[EmbeddedImage] = []
    url_to_cid: dict[str, str] = {}
    counter = 0

    def _read(local_url: str) -> tuple[bytes | None, str | None, str | None]:
        # local_url like /static/signature/logo.png
        rel = local_url[len("/static/") :].lstrip("/")
        path = f"{static_dir.rstrip('/')}/{rel}"
        try:
            with open(path, "rb") as f:
                content = f.read()
            ct, _ = mimetypes.guess_type(path)
            ct = ct or "application/octet-stream"
            if not ct.startswith("image/"):
                warnings.append(f"Local asset not an image ({ct}): {local_url}")
                return None, None, None
            filename = path.split("/")[-1]
            return content, ct, filename
        except FileNotFoundError:
            warnings.append(f"Local signature asset missing: {local_url}")
            return None, None, None
        except Exception:
            warnings.append(f"Local signature asset read error: {local_url}")
            return None, None, None

    def _repl(m: re.Match) -> str:
        nonlocal counter
        prefix, quote, url = m.group(1), m.group(2), m.group(3)

        if url not in url_to_cid:
            counter += 1
            url_to_cid[url] = f"{cid_prefix}{counter}"
        cid = url_to_cid[url]

        if not any(e.cid == cid for e in embedded):
            content, ct, filename = _read(url)
            if not content or not ct or not filename:
                return m.group(0)
            embedded.append(EmbeddedImage(cid=cid, content=content, content_type=ct, filename=filename))

        return f"{prefix}{quote}cid:{cid}{quote}"

    new_html = _LOCAL_IMG_SRC_RE.sub(_repl, html)
    return new_html, embedded, warnings


def embed_images_as_cid(
    *,
    db,
    html: str,
    static_dir: str,
) -> Tuple[str, List[EmbeddedImage], List[str]]:
    """Embed both local signature assets and remote images.

    Order matters:
    1) Local /static/signature/* assets are embedded first (guaranteed availability)
    2) Remote https:// images are fetched and embedded second (best-effort)

    Returns (new_html, embedded_images, warnings)
    """
    if not html:
        return "", [], []

    html2, local_imgs, w1 = embed_local_signature_images_as_cid(html=html, static_dir=static_dir)
    html3, remote_imgs, w2 = embed_remote_images_as_cid(db=db, html=html2)
    return html3, [*local_imgs, *remote_imgs], [*w1, *w2]
