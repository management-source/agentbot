from __future__ import annotations

import base64
import json
from typing import List, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_ENV: str = "dev"

    # Runtime
    DEBUG: bool = False

    # Optional UI/API protection (recommended for production)
    UI_BASIC_AUTH_USER: Optional[str] = None
    UI_BASIC_AUTH_PASSWORD: Optional[str] = None

    # Prefer Postgres in production (Render Postgres). SQLite is OK for local dev.
    DATABASE_URL: str = "sqlite:///./email_autopilot.db"

    # App auth (local users). For production you MUST set JWT_SECRET.
    JWT_SECRET: str = "dev-insecure-change-me"

    # Bootstrap admin (created on startup if no users exist)
    BOOTSTRAP_ADMIN_EMAIL: str = "admin@example.com"
    BOOTSTRAP_ADMIN_NAME: str = "Admin"
    BOOTSTRAP_ADMIN_PASSWORD: str = "ChangeMeNow!"

    # Scheduler (APScheduler) can be enabled later (e.g., background worker).
    ENABLE_SCHEDULER: bool = True

    # --- Gmail Auth Mode ---
    # oauth: user OAuth flow (Connect to Google button)
    # service_account: Google Workspace Domain-Wide Delegation (industry standard)
    GMAIL_AUTH_MODE: str = "oauth"  # "oauth" | "service_account"

    # --- Google OAuth (oauth mode) ---
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GOOGLE_REDIRECT_URI: Optional[str] = None

    # --- Service Account (service_account mode) ---
    # Paste the full JSON contents OR set SERVICE_ACCOUNT_JSON_B64.
    SERVICE_ACCOUNT_JSON: Optional[str] = None
    SERVICE_ACCOUNT_JSON_B64: Optional[str] = None
    # The mailbox to impersonate (e.g., admin@yourdomain.com)
    IMPERSONATE_USER: Optional[str] = None

    # Optional: Gmail mailbox delegation (OAuth mode only). With DWD, use IMPERSONATE_USER instead.
    DELEGATED_MAILBOX: Optional[str] = None

    # If True, date-range sync will search in:anywhere (includes archived).
    SYNC_INCLUDE_ANYWHERE: bool = False

    # Comma-separated list of mailbox addresses that should count as "our" outbound replies.
    # Defaulted to your primary operations inbox to make unreplied detection work out-of-the-box.
    MY_EMAILS: str = "admin@donspremier.com.au"
    POLL_INTERVAL_SECONDS: int = 300
    REMINDER_INTERVAL_SECONDS: int = 900
    REMINDER_COOLDOWN_SECONDS: int = 3600

    REMINDER_TO_EMAIL: Optional[str] = None

    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o-mini"

    # Optional default signature if not set via UI
    DEFAULT_SIGNATURE: str = ""

    # Observability
    SENTRY_DSN: Optional[str] = None

    def my_emails_list(self) -> List[str]:
        return [e.strip().lower() for e in self.MY_EMAILS.split(",") if e.strip()]

    def service_account_info(self) -> Optional[dict]:
        """Return service account JSON dict (if configured), else None."""
        raw = self.SERVICE_ACCOUNT_JSON
        if not raw and self.SERVICE_ACCOUNT_JSON_B64:
            try:
                raw = base64.b64decode(self.SERVICE_ACCOUNT_JSON_B64).decode("utf-8")
            except Exception:
                raw = None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    @model_validator(mode="after")
    def _validate_modes(self):
        """Normalize modes and validate only when strictly required.

        We *do not* hard-fail startup in OAuth mode if GOOGLE_* vars are missing,
        because the UI can still load and show a helpful message.
        """
        mode = (self.GMAIL_AUTH_MODE or "oauth").strip().lower()
        object.__setattr__(self, "GMAIL_AUTH_MODE", mode)

        if mode == "service_account":
            if not self.service_account_info():
                raise ValueError(
                    "SERVICE_ACCOUNT_JSON (or SERVICE_ACCOUNT_JSON_B64) is required when GMAIL_AUTH_MODE=service_account"
                )
            if not (self.IMPERSONATE_USER or "").strip():
                raise ValueError("IMPERSONATE_USER is required when GMAIL_AUTH_MODE=service_account")

        return self

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
