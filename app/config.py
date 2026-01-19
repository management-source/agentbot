from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    APP_ENV: str = "dev"
    DATABASE_URL: str = "sqlite:///./email_autopilot.db"

    # Scheduler (APScheduler) is convenient locally, but free hosting tiers may sleep.
    # For manual-sync deployments (e.g., Render free tier), set ENABLE_SCHEDULER=false.
    ENABLE_SCHEDULER: bool = True

    # --- Google OAuth (NO JSON file) ---
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str

    MY_EMAILS: str = ""
    POLL_INTERVAL_SECONDS: int = 300
    REMINDER_INTERVAL_SECONDS: int = 900
    REMINDER_COOLDOWN_SECONDS: int = 3600

    # Make optional so Cloud Run can boot even if you haven't configured reminders yet
    REMINDER_TO_EMAIL: str | None = None

    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str = "gpt-4o-mini"

    def my_emails_list(self) -> List[str]:
        return [e.strip().lower() for e in self.MY_EMAILS.split(",") if e.strip()]

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
