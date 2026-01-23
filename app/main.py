from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
import base64

from app.db import init_db
from app.config import settings
from app.scheduler import scheduler
from app.routers import tasks

from app.routers import auth, tickets, autopilot, ui, threads
from app.services.gmail_sync import sync_inbox_threads
from app.services.reminders import run_reminders


app = FastAPI(title="Email Autopilot Manager", debug=settings.DEBUG)

# --- Middleware ---
class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Optional HTTP Basic auth for UI/API.

    Enable by setting UI_BASIC_AUTH_USER and UI_BASIC_AUTH_PASSWORD.
    """

    def __init__(self, app):
        super().__init__(app)
        self.user = (settings.UI_BASIC_AUTH_USER or '').strip()
        self.password = (settings.UI_BASIC_AUTH_PASSWORD or '').strip()
        self.enabled = bool(self.user and self.password)

    async def dispatch(self, request, call_next):
        if not self.enabled:
            return await call_next(request)

        # Allow health check without auth
        if request.url.path in ('/health', '/auth/google/callback'):
            return await call_next(request)

        auth = request.headers.get('authorization') or ''
        if auth.lower().startswith('basic '):
            try:
                raw = base64.b64decode(auth.split(' ', 1)[1]).decode('utf-8')
                u, p = raw.split(':', 1)
                if u == self.user and p == self.password:
                    return await call_next(request)
            except Exception:
                pass

        return Response(
            content='Authentication required',
            status_code=401,
            headers={'WWW-Authenticate': 'Basic realm="AgentBot"'},
        )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('Referrer-Policy', 'no-referrer')
        resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        resp.headers.setdefault('Permissions-Policy', 'geolocation=(), microphone=(), camera=()')
        return resp


app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(BasicAuthMiddleware)


@app.get('/health')
def health():
    return {'ok': True}


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
