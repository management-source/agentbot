from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import init_db
from app.config import settings
from app.scheduler import scheduler
from app.routers import tasks

from app.routers import auth, tickets, autopilot, ui, threads
from app.services.gmail_sync import sync_inbox_threads
from app.services.reminders import run_reminders


app = FastAPI(title="Email Autopilot Manager", debug=True)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(ui.router, tags=["ui"])

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(tickets.router, prefix="/tickets", tags=["tickets"])
app.include_router(autopilot.router, prefix="/autopilot", tags=["autopilot"])
app.include_router(threads.router, prefix="/threads", tags=["threads"])
app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])

@app.on_event("startup")
def on_startup():
    init_db()

    # For free-hosting deployments (e.g., Render free tier), we typically disable
    # background schedulers and rely on manual sync.
    if settings.ENABLE_SCHEDULER:
        # IMPORTANT: job id must match what endpoints look for
        scheduler.add_job(
            func=sync_inbox_threads,
            trigger="interval",
            seconds=settings.POLL_INTERVAL_SECONDS,
            id="gmail_poll",
            replace_existing=True,
        )

        scheduler.add_job(
            func=run_reminders,
            trigger="interval",
            seconds=settings.REMINDER_INTERVAL_SECONDS,
            id="reminders",
            replace_existing=True,
        )

        scheduler.start()

@app.on_event("shutdown")
def on_shutdown():
    if settings.ENABLE_SCHEDULER:
        scheduler.shutdown(wait=False)
