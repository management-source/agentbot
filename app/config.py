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
    RECAPTCHA_SITE_KEY: Optional[str] = None
    RECAPTCHA_SECRET_KEY: Optional[str] = None
    RECAPTCHA_VERIFY_URL: str = "https://www.google.com/recaptcha/api/siteverify"
    RECAPTCHA_TIMEOUT_SECONDS: float = 8.0

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

    # Optional: monitor multiple mailboxes (comma-separated). When set, the app will
    # isolate tickets/settings/blacklists per mailbox and impersonate each mailbox as needed.
    # Example: admin@donspremier.com.au,lushan@donspremier.com.au
    MONITORED_MAILBOXES: Optional[str] = None

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

    # Tenant portal media uploads. On Render, mount the persistent disk at /var/data.
    TENANT_UPLOAD_DIR: str = "/var/data/tenant_uploads"
    TENANT_UPLOAD_MAX_BYTES: int = 25 * 1024 * 1024

    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_TRANSCRIBE_MODEL: str = "whisper-1"

    # Inspection route planning providers. The Vicmap URL is the public address
    # locator, not the much slower raw FeatureServer query endpoint. A production
    # deployment can point OSRM at a managed/self-hosted instance.
    INSPECTIONS_VICMAP_URL: str = "https://corp-geo.mapshare.vic.gov.au/arcgis/rest/services/Geocoder/VMAddressEZIAdd/GeocodeServer/findAddressCandidates"
    INSPECTIONS_VICMAP_WFS_URL: str = "https://opendata.maps.vic.gov.au/geoserver/wfs"
    INSPECTIONS_OSRM_BASE_URL: str = "https://router.project-osrm.org"
    INSPECTIONS_HTTP_TIMEOUT_SECONDS: float = 12.0
    INSPECTIONS_PROVIDER_BUDGET_SECONDS: float = 60.0

    # Optional default signature if not set via UI
    DEFAULT_SIGNATURE: str = ""

    # Observability
    SENTRY_DSN: Optional[str] = None

    def monitored_mailboxes_list(self) -> List[str]:
        raw = (self.MONITORED_MAILBOXES or "").strip()
        if raw:
            return [e.strip().lower() for e in raw.split(",") if e.strip()]
        # Back-compat: fall back to single impersonation mailbox.
        if (self.IMPERSONATE_USER or "").strip():
            return [(self.IMPERSONATE_USER or "").strip().lower()]
        # OAuth delegated inbox deployments should still have a concrete mailbox
        # context even when MONITORED_MAILBOXES is not explicitly set.
        if (self.DELEGATED_MAILBOX or "").strip():
            return [(self.DELEGATED_MAILBOX or "").strip().lower()]
        my_emails = self.my_emails_list()
        if my_emails:
            return [my_emails[0]]
        return []

    def my_emails_list(self) -> List[str]:
        return [e.strip().lower() for e in self.MY_EMAILS.split(",") if e.strip()]

    def recaptcha_enabled(self) -> bool:
        return bool((self.RECAPTCHA_SITE_KEY or "").strip() and (self.RECAPTCHA_SECRET_KEY or "").strip())

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
            mbs = self.monitored_mailboxes_list()
            if not mbs:
                raise ValueError(
                    "IMPERSONATE_USER (or MONITORED_MAILBOXES) is required when GMAIL_AUTH_MODE=service_account"
                )
            # Normalize IMPERSONATE_USER to the first monitored mailbox for backwards compatibility.
            if not (self.IMPERSONATE_USER or "").strip():
                object.__setattr__(self, "IMPERSONATE_USER", mbs[0])

        return self
    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
