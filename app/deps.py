from __future__ import annotations

from fastapi import Depends, HTTPException, Header, Query
from app.config import settings
from app.authz import get_current_user

def get_current_mailbox(
    mailbox: str | None = Query(default=None),
    x_mailbox: str | None = Header(default=None, alias="X-Mailbox"),
    user=Depends(get_current_user),
) -> str:
    """Return the mailbox context for the request, validated against allowed mailboxes.

    Clients may pass either:
      - query param ?mailbox=...
      - header X-Mailbox: ...

    If omitted, defaults to the first configured monitored mailbox.
    """
    requested = (mailbox or x_mailbox or "").strip().lower()
    allowed = settings.monitored_mailboxes_list()
    if not allowed:
        raise HTTPException(status_code=500, detail="No monitored mailboxes configured.")
    if not requested:
        return allowed[0]
    if requested not in allowed:
        raise HTTPException(status_code=400, detail=f"Unknown mailbox '{requested}'.")
    return requested
