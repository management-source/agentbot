from fastapi import APIRouter
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
):
    """Manual sync endpoint.

    This endpoint is the only supported way to pull tickets into the system.

    - If start/end are provided: performs a date-range sync (up to max_threads).
    - Otherwise: performs an incremental sync using Gmail historyId (when available),
      falling back to a 30-day window for first-time bootstrap.

    Notes:
    - awaiting_only=True ensures we only create/update tickets that still require a reply.
    - We intentionally do not invoke AI from sync.
    """
    return sync_inbox_threads(
        max_threads=max_threads,
        start=start,
        end=end,
        incremental=incremental,
        include_anywhere=include_anywhere,
        awaiting_only=awaiting_only,
        auto_triage=False,
    )


@router.post("/check-updates")
def check_updates(max_threads: int = 200):
    """Incremental sync.

    Fetch only threads that have changed since the last successful sync. This is
    intended to be used frequently.
    """
    return sync_inbox_threads(
        max_threads=max_threads,
        start=None,
        end=None,
        incremental=True,
        include_anywhere=False,
        awaiting_only=True,
        auto_triage=False,
    )
