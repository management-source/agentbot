from fastapi import APIRouter, Header, HTTPException
from app.config import settings
from app.services.gmail_sync import sync_inbox_threads
from app.services.reminders import run_reminders

router = APIRouter()

def _require_scheduler_key(x_scheduler_key: str | None):
    if not settings.SCHEDULER_KEY:
        raise HTTPException(500, "SCHEDULER_KEY not configured")
    if not x_scheduler_key or x_scheduler_key != settings.SCHEDULER_KEY:
        raise HTTPException(401, "Unauthorized")

@router.post("/poll")
def poll(x_scheduler_key: str | None = Header(default=None)):
    _require_scheduler_key(x_scheduler_key)
    return sync_inbox_threads(max_threads=100)

@router.post("/remind")
def remind(x_scheduler_key: str | None = Header(default=None)):
    _require_scheduler_key(x_scheduler_key)
    run_reminders()
    return {"ok": True}
