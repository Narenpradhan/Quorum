import json
import logging
import signal
import sys
import time
from collections import defaultdict
from typing import Any, Dict, List, Tuple

import redis
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_worker_settings

settings = get_worker_settings()

logging.basicConfig(
    level=settings.LOG_LEVEL.upper(),
    format="%(asctime)s [%(levelname)s] [WORKER] %(message)s",
)
logger = logging.getLogger("quorum.worker")


class VoteWorker:
    """Asynchronous background worker consuming vote events and persisting them in batches."""

    def __init__(self) -> None:
        self.is_running: bool = True
        self.settings = settings
        self.engine: Engine = create_engine(
            self.settings.DATABASE_URL,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            future=True,
        )
        self.redis_client = redis.Redis(
            host=self.settings.REDIS_HOST,
            port=self.settings.REDIS_PORT,
            password=self.settings.REDIS_PASSWORD or None,
            decode_responses=True,
        )
        self._setup_signals()

    def _setup_signals(self) -> None:
        """Register OS signal handlers for graceful shutdown."""
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum: int, frame: Any) -> None:
        signame = signal.Signals(signum).name
        logger.info(f"Received signal {signame} ({signum}). Initiating graceful shutdown...")
        self.is_running = False

    def flush_batch(self, batch: List[Dict[str, Any]]) -> bool:
        """Persist a batch of vote events to PostgreSQL and decrement Redis tally offsets."""
        if not batch:
            return True

        start_time = time.perf_counter()
        batch_size = len(batch)

        try:
            persisted_votes: List[Tuple[int, int]] = []

            with self.engine.begin() as conn:
                # 1. Insert audit logs into votes table with ON CONFLICT DO NOTHING
                insert_stmt = text(
                    """
                    INSERT INTO votes (poll_id, option_id, voter_hash, created_at)
                    VALUES (:poll_id, :option_id, :voter_hash, :created_at)
                    ON CONFLICT (poll_id, voter_hash) DO NOTHING
                    RETURNING poll_id, option_id
                    """
                )

                for vote in batch:
                    row = conn.execute(
                        insert_stmt,
                        {
                            "poll_id": vote["poll_id"],
                            "option_id": vote["option_id"],
                            "voter_hash": vote["voter_hash"],
                            "created_at": vote.get("created_at"),
                        },
                    ).fetchone()
                    if row:
                        persisted_votes.append((row[0], row[1]))

                # Aggregate increments exclusively for successfully persisted votes
                option_increments: Dict[int, int] = defaultdict(int)
                poll_option_increments: Dict[Tuple[int, int], int] = defaultdict(int)

                for p_id, opt_id in persisted_votes:
                    option_increments[opt_id] += 1
                    poll_option_increments[(p_id, opt_id)] += 1

                # 2. Update aggregated option vote counts
                if option_increments:
                    update_stmt = text(
                        """
                        UPDATE poll_options
                        SET vote_count = vote_count + :inc
                        WHERE id = :opt_id
                        """
                    )
                    for opt_id, count in option_increments.items():
                        conn.execute(update_stmt, {"inc": count, "opt_id": opt_id})

            # 3. Synchronize Redis tallies: decrement offsets to balance real-time cache
            if poll_option_increments:
                pipe = self.redis_client.pipeline()
                for (p_id, opt_id), count in poll_option_increments.items():
                    pipe.hincrby(f"quorum:poll:{p_id}:tallies", str(opt_id), -count)
                pipe.execute()

            duration_ms = (time.perf_counter() - start_time) * 1000
            persisted_count = len(persisted_votes)
            logger.info(
                f"Flushed {persisted_count}/{batch_size} votes to PostgreSQL in {duration_ms:.2f}ms "
                f"({batch_size - persisted_count} duplicates skipped)"
            )
            return True

        except SQLAlchemyError as exc:
            logger.error(f"Database error during batch flush of {batch_size} items: {exc}")
            # Re-enqueue unprocessed items to front of queue to prevent data loss
            try:
                pipe = self.redis_client.pipeline()
                for item in reversed(batch):
                    pipe.lpush(self.settings.REDIS_QUEUE_KEY, json.dumps(item))
                pipe.execute()
                logger.info(f"Successfully re-enqueued {batch_size} votes into Redis queue.")
            except Exception as r_exc:
                logger.critical(f"Failed to re-enqueue items back to Redis: {r_exc}")
            return False

        except Exception as exc:
            logger.error(f"Unexpected error during batch flush: {exc}")
            return False

    def run(self) -> None:
        """Main batch processing loop."""
        logger.info(
            f"Worker started. Listening on queue '{self.settings.REDIS_QUEUE_KEY}' "
            f"(Batch Size: {self.settings.BATCH_SIZE}, Flush Interval: {self.settings.FLUSH_INTERVAL_SECONDS}s)"
        )

        batch: List[Dict[str, Any]] = []
        last_flush_time = time.time()

        while self.is_running:
            try:
                # Block for 1 second waiting for item from Redis queue
                result = self.redis_client.blpop([self.settings.REDIS_QUEUE_KEY], timeout=1)
                if result:
                    _, raw_payload = result
                    try:
                        vote_event = json.loads(raw_payload)
                        batch.append(vote_event)
                    except json.JSONDecodeError:
                        logger.warning(f"Discarding invalid JSON payload: {raw_payload}")

                    # Opportunistically drain up to BATCH_SIZE without blocking
                    while len(batch) < self.settings.BATCH_SIZE:
                        next_raw = self.redis_client.lpop(self.settings.REDIS_QUEUE_KEY)
                        if not next_raw:
                            break
                        try:
                            batch.append(json.loads(next_raw))
                        except json.JSONDecodeError:
                            logger.warning(f"Discarding invalid JSON payload: {next_raw}")

                # Check if batch limit or flush interval is reached
                now = time.time()
                time_since_flush = now - last_flush_time

                if (len(batch) >= self.settings.BATCH_SIZE) or (
                    batch and time_since_flush >= self.settings.FLUSH_INTERVAL_SECONDS
                ):
                    flushed = self.flush_batch(batch)
                    batch = [] if flushed else []
                    last_flush_time = time.time()

            except redis.ConnectionError as r_err:
                logger.warning(f"Redis connection error: {r_err}. Retrying in 2 seconds...")
                time.sleep(2)
            except Exception as exc:
                logger.error(f"Unexpected error in worker loop: {exc}")
                time.sleep(1)

        # Graceful shutdown: flush any pending items in memory
        if batch:
            logger.info(f"Flushing remaining {len(batch)} votes in memory before stopping...")
            self.flush_batch(batch)

        logger.info("Worker stopped gracefully.")


if __name__ == "__main__":
    worker = VoteWorker()
    worker.run()
