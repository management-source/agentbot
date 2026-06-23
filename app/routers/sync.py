from fastapi import APIRouter, Depends
from app.authz import get_current_user
from app.deps import get_current_mailbox
from app.config import settings
from typing import Optional

from app.services.gmail_sync import sync_inbox_threads


router = APIRouter()


@router.post("/fetch-now")
def fetch_now(
    start: Optional[str] = None,
    end: Optional[str] = None,
    max_threads: int = 500,
    incremental: bool = True,
    include_anywhere: bool = False,
    awaiting_only: bool = True,
    mailbox: str = "all",
    user=Depends(get_current_user),
):
    """Manual sync endpoint.

    If mailbox='all' (default), sync all configured monitored mailboxes.
    Otherwise, sync only the selected mailbox.
    """
    mbs = settings.monitored_mailboxes_list()
    if not mbs:
        return {"ok": False, "error": "No monitored mailboxes configured."}

    targets = mbs if mailbox == "all" else [mailbox.strip().lower()]
    # Validate
    for mb in targets:
        if mb not in mbs:
            return {"ok": False, "error": f"Unknown mailbox '{mb}'."}

    results = []
    for mb in targets:
        results.append(
            sync_inbox_threads(
                mailbox=mb,
                max_threads=max_threads,
                start=start,
                end=end,
                incremental=incremental,
                include_anywhere=include_anywhere,
                awaiting_only=awaiting_only,
                auto_triage=False,
            )
        )
    return {"ok": True, "mailboxes": targets, "results": results}


@router.post("/check-updates")
def check_updates(
    max_threads: int = 200,
    mailbox: str = "all",
    user=Depends(get_current_user),
):
    """Incremental sync for one or all mailboxes."""
    mbs = settings.monitored_mailboxes_list()
    if not mbs:
        return {"ok": False, "error": "No monitored mailboxes configured."}
    targets = mbs if mailbox == "all" else [mailbox.strip().lower()]
    for mb in targets:
        if mb not in mbs:
            return {"ok": False, "error": f"Unknown mailbox '{mb}'."}

    results = []
    for mb in targets:
        results.append(
            sync_inbox_threads(
                mailbox=mb,
                max_threads=max_threads,
                start=None,
                end=None,
                incremental=True,
                include_anywhere=False,
                awaiting_only=True,
                auto_triage=False,
            )
        )
    return {"ok": True, "mailboxes": targets, "results": results}
