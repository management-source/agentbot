from fastapi import APIRouter, Depends
from typing import Optional

from app.authz import get_mailbox_id
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
    mailbox_id: str = Depends(get_mailbox_id),
):
    """Manual sync endpoint (per selected mailbox)."""
    return sync_inbox_threads(
        mailbox_id=mailbox_id,
        max_threads=max_threads,
        start=start,
        end=end,
        incremental=incremental,
        include_anywhere=include_anywhere,
        awaiting_only=awaiting_only,
        auto_triage=False,
    )


@router.post("/check-updates")
def check_updates(
    max_threads: int = 200,
    mailbox_id: str = Depends(get_mailbox_id),
):
    """Incremental sync for the selected mailbox."""
    return sync_inbox_threads(
        mailbox_id=mailbox_id,
        max_threads=max_threads,
        start=None,
        end=None,
        incremental=True,
        include_anywhere=False,
        awaiting_only=True,
        auto_triage=False,
    )
