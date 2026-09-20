from typing import Generator
import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from app.config import get_settings

settings = get_settings()

# SQLAlchemy PostgreSQL Engine & Session
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    future=True,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()

# Redis Connection Pool
redis_pool = redis.ConnectionPool(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    password=settings.REDIS_PASSWORD or None,
    decode_responses=True,
)


def get_redis_client() -> redis.Redis:
    """Return a Redis client instance from the shared connection pool."""
    return redis.Redis(connection_pool=redis_pool)


def get_db() -> Generator[Session, None, None]:
    """Dependency for obtaining a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_redis() -> Generator[redis.Redis, None, None]:
    """Dependency for obtaining a Redis client."""
    client = get_redis_client()
    try:
        yield client
    finally:
        # Releasing connection back to pool
        client.close()
