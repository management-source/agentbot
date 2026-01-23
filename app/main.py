import logging
import uuid
from datetime import datetime
from pydantic import ValidationError

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
import base64

import sentry_sdk
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

from app.db import init_db, SessionLocal
from app.config import settings
from app.scheduler import scheduler
from app.routers import tasks

from app.routers import auth, tickets, autopilot, ui, threads
from app.routers import user_auth
from app.models import User, UserRole
from app.security import hash_password
from app.services.gmail_sync import sync_inbox_threads
from app.services.reminders import run_reminders
from app.services.escalation import run_sla_escalations
from fastapi.responses import JSONResponse


app = FastAPI(title="Email Autopilot Manager", debug=settings.DEBUG)


# -------- Observability --------

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "path"],
)


@app.exception_handler(ValidationError)
async def pydantic_validation_exception_handler(request: Request, exc: ValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": "validation_error", "details": exc.errors()},
    )


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        import json

        payload = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # pass structured extras if present
        for k in ("request_id", "path", "method", "status_code", "latency_ms", "user"):
            if hasattr(record, k):
                payload[k] = getattr(record, k)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]

    # Silence noisy third-party CSS parsers used by premailer
    logging.getLogger('CSSUTILS').setLevel(logging.CRITICAL)
    logging.getLogger('cssutils').setLevel(logging.CRITICAL)


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        resp = await call_next(request)
        resp.headers.setdefault("X-Request-ID", request_id)
        return resp


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/metrics":
            return await call_next(request)

        path = request.url.path
        method = request.method
        with REQUEST_LATENCY.labels(method=method, path=path).time():
            resp = await call_next(request)

        REQUEST_COUNT.labels(method=method, path=path, status=str(resp.status_code)).inc()
        return resp


setup_logging()

if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.APP_ENV,
        traces_sample_rate=0.0,
    )

if (settings.APP_ENV or "").lower() == "prod" and getattr(settings, "SENTRY_DSN", None):
    sentry_sdk.init(dsn=settings.SENTRY_DSN, traces_sample_rate=0.1)

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
app.add_middleware(RequestIdMiddleware)
app.add_middleware(MetricsMiddleware)


@app.get('/health')
def health():
    return {'ok': True}


@app.get("/metrics")
def metrics():
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(ui.router, tags=["ui"])

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(user_auth.router)
app.include_router(tickets.router, prefix="/tickets", tags=["tickets"])
app.include_router(autopilot.router, prefix="/autopilot", tags=["autopilot"])
app.include_router(threads.router, prefix="/threads", tags=["threads"])
app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])

@app.on_event("startup")
def on_startup():
    init_db()

    # Bootstrap local admin if no users exist yet
    db = SessionLocal()
    try:
        user_count = db.query(User).count()
        if user_count == 0:
            u = User(
                email=settings.BOOTSTRAP_ADMIN_EMAIL.lower(),
                name=settings.BOOTSTRAP_ADMIN_NAME,
                role=UserRole.ADMIN,
                is_active=True,
                password_hash=hash_password(settings.BOOTSTRAP_ADMIN_PASSWORD),
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(u)
            db.commit()
            logging.getLogger("bootstrap").info(
                "Bootstrapped admin user",
                extra={"user": u.email},
            )
    finally:
        db.close()

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

        def _escalate_job():
            db = SessionLocal()
            try:
                run_sla_escalations(db)
            finally:
                db.close()

        scheduler.add_job(
            func=_escalate_job,
            trigger="interval",
            seconds=600,
            id="sla_escalations",
            replace_existing=True,
        )

        scheduler.start()

@app.on_event("shutdown")
def on_shutdown():
    if settings.ENABLE_SCHEDULER:
        scheduler.shutdown(wait=False)
