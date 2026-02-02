from fastapi import APIRouter, Depends, HTTPException
from typing import Optional

from app.authz import get_mailbox_id
from app.services.gmail_sync import sync_inbox_threads
from app.scheduler import scheduler
from app.config import settings

router = APIRouter()

# NOTE: Autopilot is not used in the UI (feature removed), but these endpoints are kept
# for backward compatibility / admin testing.

@router.post("/fetch-now")
def fetch_now(
    start: Optional[str] = None,
    end: Optional[str] = None,
    max_threads: int = 500,
    incremental: bool = True,
    include_anywhere: bool = False,
    awaiting_only: bool = True,
    auto_triage: bool = False,
    mailbox_id: str = Depends(get_mailbox_id),
):
    return sync_inbox_threads(
        mailbox_id=mailbox_id,
        max_threads=max_threads,
        start=start,
        end=end,
        incremental=incremental,
        include_anywhere=include_anywhere,
        awaiting_only=awaiting_only,
        auto_triage=auto_triage,
    )


@router.post("/check-updates")
def check_updates(
    max_threads: int = 200,
    mailbox_id: str = Depends(get_mailbox_id),
):
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


@router.post("/start")
def start_autopilot():
    if not settings.ENABLE_SCHEDULER:
        raise HTTPException(400, "Scheduler is disabled (ENABLE_SCHEDULER=false).")
    job = scheduler.get_job("gmail_poll")
    if not job:
        raise HTTPException(500, "gmail_poll job not found")
    job.resume()
    return {"ok": True, "status": "started"}


@router.post("/stop")
def stop_autopilot():
    if not settings.ENABLE_SCHEDULER:
        raise HTTPException(400, "Scheduler is disabled (ENABLE_SCHEDULER=false).")
    job = scheduler.get_job("gmail_poll")
    if not job:
        raise HTTPException(500, "gmail_poll job not found")
    job.pause()
    return {"ok": True, "status": "stopped"}


@router.get("/status")
def autopilot_status():
    if not settings.ENABLE_SCHEDULER:
        return {"ok": True, "running": False, "next_run_time": None, "scheduler_enabled": False}
    job = scheduler.get_job("gmail_poll")
    if not job:
        return {"ok": True, "running": False, "next_run_time": None, "scheduler_enabled": True}
    return {"ok": True, "running": True, "next_run_time": str(job.next_run_time), "scheduler_enabled": True}
