from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

@contextmanager
def db_session():
    """Yield a SQLAlchemy session; commits/rollbacks managed by caller."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
