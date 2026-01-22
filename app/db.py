from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import Base

def _normalize_database_url(url: str) -> str:
    # Render and some providers expose Postgres URLs as postgres://
    # but SQLAlchemy expects postgresql:// (or postgresql+psycopg2://).
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://"): ]
    return url



def _create_engine(database_url: str):
    # SQLite needs check_same_thread for FastAPI + threaded workers.
    if database_url.startswith("sqlite"):
        return create_engine(database_url, connect_args={"check_same_thread": False})
    return create_engine(database_url, pool_pre_ping=True)


engine = _create_engine(_normalize_database_url(settings.DATABASE_URL))

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
