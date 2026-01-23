from __future__ import annotations

import base64
import json
from typing import List, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_ENV: str = "dev"

    # Prefer Postgres in production (Render Postgres). SQLite is OK for local dev.
    DATABASE_URL: str = "sqlite:///./email_autopilot.db"

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

    MY_EMAILS: str = ""
    POLL_INTERVAL_SECONDS: int = 300
    REMINDER_INTERVAL_SECONDS: int = 900
    REMINDER_COOLDOWN_SECONDS: int = 3600

    REMINDER_TO_EMAIL: Optional[str] = None

    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o-mini"

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
        mode = (self.GMAIL_AUTH_MODE or "oauth").strip().lower()
        object.__setattr__(self, "GMAIL_AUTH_MODE", mode)

        if mode == "service_account":
            if not self.service_account_info():
                raise ValueError("SERVICE_ACCOUNT_JSON (or SERVICE_ACCOUNT_JSON_B64) is required when GMAIL_AUTH_MODE=service_account")
            if not (self.IMPERSONATE_USER or "").strip():
                raise ValueError("IMPERSONATE_USER is required when GMAIL_AUTH_MODE=service_account")
        else:
            # oauth mode
            missing = [k for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI") if not (getattr(self, k) or "").strip()]
            if missing:
                raise ValueError(f"Missing required OAuth settings: {', '.join(missing)}")
        return self

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
