import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import redis
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.database import get_db, get_redis
from backend.app.models import Poll, PollOption
from backend.app.schemas import (
    HealthResponse,
    PollOptionResponse,
    PollResponse,
    ReadinessResponse,
    VoteRequest,
    VoteResponse,
)

settings = get_settings()

# Configure logging
logging.basicConfig(
    level=settings.LOG_LEVEL.upper(),
    format="%(asctime)s [%(levelname)s] [API] %(message)s",
)
logger = logging.getLogger("quorum.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan hook for initialization and cleanup."""
    logger.info("Quorum API Gateway initialized. Ready to receive events.")
    yield
    logger.info("Quorum API Gateway shutting down.")


app = FastAPI(
    title="Quorum API Gateway",
    version="1.0.0",
    description="High-throughput event-driven voting and polling platform API gateway.",
    lifespan=lifespan,
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _build_poll_response(poll: Poll, redis_client: redis.Redis) -> PollResponse:
    """Helper to merge database baseline vote counts with real-time Redis tally offsets."""
    tally_key = f"quorum:poll:{poll.id}:tallies"
    try:
        redis_tallies = redis_client.hgetall(tally_key)
    except Exception as exc:
        logger.warning(f"Could not read Redis tallies for poll {poll.id}: {exc}")
        redis_tallies = {}

    options_data: List[PollOptionResponse] = []
    total_votes = 0

    for opt in poll.options:
        raw_offset = redis_tallies.get(str(opt.id), 0)
        try:
            offset = max(0, int(raw_offset))
        except (ValueError, TypeError):
            offset = 0

        effective_count = opt.vote_count + offset
        total_votes += effective_count
        options_data.append(
            PollOptionResponse(
                id=opt.id,
                poll_id=opt.poll_id,
                label=opt.label,
                vote_count=effective_count,
                percentage=0.0,
            )
        )

    # Calculate percentages
    for opt in options_data:
        if total_votes > 0:
            opt.percentage = round((opt.vote_count / total_votes) * 100, 1)
        else:
            opt.percentage = 0.0

    return PollResponse(
        id=poll.id,
        title=poll.title,
        description=poll.description,
        created_at=poll.created_at,
        options=options_data,
        total_votes=total_votes,
    )


# -------------------------------------------------------------------------
# Health & Readiness Probes
# -------------------------------------------------------------------------


@app.get("/healthz", response_model=HealthResponse, tags=["Health"])
def healthz() -> HealthResponse:
    """Liveness probe: verifies process is alive."""
    return HealthResponse(status="alive")


@app.get("/readyz", response_model=ReadinessResponse, tags=["Health"])
def readyz(
    db: Session = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
) -> JSONResponse:
    """Readiness probe: validates connectivity to PostgreSQL and Redis."""
    db_status = "connected"
    redis_status = "connected"
    errors = []

    # Check PostgreSQL
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error(f"PostgreSQL readiness check failed: {exc}")
        db_status = f"unreachable ({type(exc).__name__})"
        errors.append("database")

    # Check Redis
    try:
        r.ping()
    except Exception as exc:
        logger.error(f"Redis readiness check failed: {exc}")
        redis_status = f"unreachable ({type(exc).__name__})"
        errors.append("redis")

    is_ready = len(errors) == 0
    status_code = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if is_ready else "unready",
            "database": db_status,
            "redis": redis_status,
        },
    )


# -------------------------------------------------------------------------
# Polls & Voting Endpoints
# -------------------------------------------------------------------------


@app.get("/api/polls", response_model=List[PollResponse], tags=["Polls"])
def list_polls(
    db: Session = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
) -> List[PollResponse]:
    """Retrieve all polls with options merged with real-time Redis tally offsets."""
    polls = db.query(Poll).order_by(Poll.id.asc()).all()
    return [_build_poll_response(poll, r) for poll in polls]


@app.get("/api/polls/{poll_id}", response_model=PollResponse, tags=["Polls"])
def get_poll(
    poll_id: int,
    db: Session = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
) -> PollResponse:
    """Retrieve an individual poll with its options and real-time tally breakdowns."""
    poll = db.query(Poll).filter(Poll.id == poll_id).first()
    if not poll:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Poll with ID {poll_id} not found",
        )
    return _build_poll_response(poll, r)


@app.post(
    "/api/polls/{poll_id}/vote",
    response_model=VoteResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Voting"],
)
def submit_vote(
    poll_id: int,
    vote_data: VoteRequest,
    db: Session = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
) -> VoteResponse:
    """Accept and queue an incoming vote event, atomically updating the Redis tally."""
    # Validate option belongs to target poll
    option = (
        db.query(PollOption)
        .filter(PollOption.id == vote_data.option_id, PollOption.poll_id == poll_id)
        .first()
    )
    if not option:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Option {vote_data.option_id} does not belong to poll {poll_id}",
        )

    event_payload = json.dumps(
        {
            "poll_id": poll_id,
            "option_id": vote_data.option_id,
            "voter_hash": vote_data.voter_fingerprint,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    tally_key = f"quorum:poll:{poll_id}:tallies"

    try:
        # Atomic pipeline: increment real-time tally offset and push event to stream queue
        pipe = r.pipeline()
        pipe.hincrby(tally_key, str(vote_data.option_id), 1)
        pipe.rpush(settings.REDIS_QUEUE_KEY, event_payload)
        pipe.execute()
    except Exception as exc:
        logger.error(f"Failed to enqueue vote into Redis: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Vote processing buffer currently unavailable",
        )

    return VoteResponse(
        status="queued",
        poll_id=poll_id,
        option_id=vote_data.option_id,
    )
