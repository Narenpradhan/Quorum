import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import redis
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db, get_redis
from app.models import Poll, PollOption, Vote
from app.schemas import (
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


def _check_voter_status(
    poll_id: int,
    voter_fingerprint: Optional[str],
    redis_client: redis.Redis,
    db: Session,
) -> tuple[bool, Optional[int]]:
    """Determine whether the voter has already cast a ballot on this poll."""
    if not voter_fingerprint:
        return False, None

    # 1. Fast check in Redis set
    voters_set_key = f"quorum:poll:{poll_id}:voters"
    choices_key = f"quorum:poll:{poll_id}:voter_choices"

    try:
        if redis_client.sismember(voters_set_key, voter_fingerprint):
            raw_choice = redis_client.hget(choices_key, voter_fingerprint)
            chosen_opt = int(raw_choice) if raw_choice else None
            return True, chosen_opt
    except Exception as exc:
        logger.warning(f"Error checking Redis voter set: {exc}")

    # 2. Check PostgreSQL persistence store
    existing_vote = (
        db.query(Vote)
        .filter(Vote.poll_id == poll_id, Vote.voter_hash == voter_fingerprint)
        .first()
    )
    if existing_vote:
        # Backfill Redis cache for instant subsequent lookups
        try:
            pipe = redis_client.pipeline()
            pipe.sadd(voters_set_key, voter_fingerprint)
            pipe.hset(choices_key, voter_fingerprint, str(existing_vote.option_id))
            pipe.execute()
        except Exception as exc:
            logger.warning(f"Could not backfill Redis voter cache: {exc}")
        return True, existing_vote.option_id

    return False, None


def _build_poll_response(
    poll: Poll,
    redis_client: redis.Redis,
    db: Session,
    voter_fingerprint: Optional[str] = None,
) -> PollResponse:
    """Build poll response, concealing voting statistics until the voter has cast a ballot."""
    has_voted, user_voted_option_id = _check_voter_status(
        poll.id, voter_fingerprint, redis_client, db
    )

    # If the user has NOT voted, return options with counts and percentages hidden
    if not has_voted:
        hidden_options = [
            PollOptionResponse(
                id=opt.id,
                poll_id=opt.poll_id,
                label=opt.label,
                vote_count=None,
                percentage=None,
            )
            for opt in poll.options
        ]
        return PollResponse(
            id=poll.id,
            title=poll.title,
            description=poll.description,
            created_at=poll.created_at,
            options=hidden_options,
            has_voted=False,
            user_voted_option_id=None,
            total_votes=None,
        )

    # If the user HAS voted, reveal live statistics merged with Redis tally offsets
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
        has_voted=True,
        user_voted_option_id=user_voted_option_id,
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
    voter_fingerprint: Optional[str] = Query(
        None, description="Optional client UID to check vote status and reveal stats"
    ),
    db: Session = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
) -> List[PollResponse]:
    """Retrieve all polls, concealing statistics unless the voter has cast a ballot."""
    polls = db.query(Poll).order_by(Poll.id.asc()).all()
    return [_build_poll_response(poll, r, db, voter_fingerprint) for poll in polls]


@app.get("/api/polls/{poll_id}", response_model=PollResponse, tags=["Polls"])
def get_poll(
    poll_id: int,
    voter_fingerprint: Optional[str] = Query(
        None, description="Optional client UID to check vote status and reveal stats"
    ),
    db: Session = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
) -> PollResponse:
    """Retrieve an individual poll, revealing statistics only if the voter has voted."""
    poll = db.query(Poll).filter(Poll.id == poll_id).first()
    if not poll:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Poll with ID {poll_id} not found",
        )
    return _build_poll_response(poll, r, db, voter_fingerprint)


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
    """Accept and queue an incoming vote event, strictly enforcing one vote per UID."""
    # 1. Validate option belongs to target poll
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

    # 2. Enforce one vote per UID: check if voter has already voted
    has_voted, _ = _check_voter_status(poll_id, vote_data.voter_fingerprint, r, db)
    if has_voted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already cast a vote on this poll. Changing votes is not permitted.",
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
    voters_set_key = f"quorum:poll:{poll_id}:voters"
    choices_key = f"quorum:poll:{poll_id}:voter_choices"

    try:
        # Atomic pipeline: mark voter as voted, store choice, increment tally, and queue event
        pipe = r.pipeline()
        pipe.sadd(voters_set_key, vote_data.voter_fingerprint)
        pipe.hset(choices_key, vote_data.voter_fingerprint, str(vote_data.option_id))
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
