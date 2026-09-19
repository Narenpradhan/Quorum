# Quorum

> **High-throughput, event-driven voting and polling platform engineered for ultra-low latency write ingestion, resilient batch persistence, anti-bias concealment, and strict one-vote-per-UID enforcement.**

Quorum eliminates write-amplification and database locking bottlenecks typical of real-time polling platforms. Rather than writing each vote synchronously to disk, Quorum utilizes an asynchronous, event-driven architecture:
1. **FastAPI Gateway**: Validates vote requests in sub-milliseconds, atomically updates in-memory counters in **Redis**, registers voter UIDs to enforce single-vote constraints, and streams vote events into a Redis queue.
2. **Anti-Bias Privacy**: Poll statistics (percentages, counts, progress bars, and total tallies) remain concealed from voters until they cast their ballot, preventing voting bias.
3. **Asynchronous Batch Worker**: Consumes the stream in batches and executes atomic multi-row inserts and aggregated updates to **PostgreSQL**, minimizing database I/O and row-level locks.
4. **Sleek Vanilla Frontend**: Served via **Nginx** with client UUID fingerprinting, real-time live results polling, instant optimistic state unlocking, and cluster readiness probes.

---

## Architecture Topology

```mermaid
graph TD
    Client["Client Browser (with UUID Fingerprint)"] -->|HTTP / SPA Traffic| Nginx["Nginx Gateway / Ingress (:80)"]
    
    subgraph Frontend Tier
        Nginx -->|Static Assets (no-cache)| WebApp["Vanilla HTML5 / CSS / ES6+ Dashboard"]
    end

    subgraph API Tier
        Nginx -->|Proxy /api/*, /healthz, /readyz| API["FastAPI Gateway (:8000)"]
    end

    subgraph Real-Time & Queue Tier
        API -->|1. Check & SADD Voter UID| RedisVoters[("Redis Voter Sets (quorum:poll:{id}:voters)")]
        API -->|2. HINCRBY Offset| RedisTallies[("Redis Hash Tallies (quorum:poll:{id}:tallies)")]
        API -->|3. RPUSH Stream Queue| RedisQueue[("Redis Queue (quorum:vote_stream)")]
        API -.->|Read Baseline Counts| Postgres[("PostgreSQL 16 DB")]
        API -.->|Read Live Offsets| RedisTallies
    end

    subgraph Batch Persistence Tier
        Worker["Asynchronous Python Worker"] -->|BLPOP / LPOP Batch| RedisQueue
        Worker -->|Batch INSERT (ON CONFLICT DO NOTHING)| Postgres
        Worker -->|Aggregated UPDATE vote_count| Postgres
        Worker -->|HINCRBY -count Offset Sync| RedisTallies
    end
```

---

## Directory Structure

```text
quorum/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── config.py         # Pydantic Settings injection
│   │   ├── database.py       # Engine, SessionLocal, Redis pool & dependencies
│   │   ├── main.py           # FastAPI endpoints (/healthz, /readyz, /api/polls, /api/polls/{id}/vote)
│   │   ├── models.py         # SQLAlchemy models (Poll, PollOption, Vote with unique constraints)
│   │   └── schemas.py        # Pydantic validation & response schemas
│   └── requirements.txt
├── worker/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── config.py         # Worker tunables (BATCH_SIZE=50, FLUSH_INTERVAL_SECONDS=2.0)
│   │   └── worker.py         # Idempotent batch consumer with PostgreSQL persistence & signal handling
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── 50x.html          # Custom error page for cluster reconnects
│   │   ├── app.js            # Vanilla ES6+ client with UUID fingerprinting, anti-bias UI, and live polling
│   │   ├── index.html        # Semantic dark-themed interface with cache-busting
│   │   └── styles.css        # Glassmorphic cyber theme with micro-animations
│   └── nginx.conf            # Nginx config with dynamic Docker resolver, reverse proxy, gzip, and cache control
├── db/
│   └── init.sql              # Idempotent DDL, indexes, unique constraints, and seed polls
├── docker-compose.yml        # Multi-container local orchestration
├── .env.example              # Environment variables template
├── .gitignore                # Production ignore patterns
└── README.md                 # Complete documentation with Mermaid diagram & API contract
```

---

## Environment Variables Reference

| Variable | Description | Default Value | Service |
| :--- | :--- | :--- | :--- |
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://postgres:postgres@localhost:5432/quorum` | Backend, Worker |
| `REDIS_HOST` | Hostname of the Redis cache/broker | `localhost` | Backend, Worker |
| `REDIS_PORT` | TCP Port of the Redis instance | `6379` | Backend, Worker |
| `REDIS_PASSWORD` | Optional authentication password for Redis | `None` / Empty | Backend, Worker |
| `REDIS_QUEUE_KEY`| Redis key for the vote ingestion stream | `quorum:vote_stream` | Backend, Worker |
| `BATCH_SIZE` | Maximum votes accumulated before flushing to DB | `50` | Worker |
| `FLUSH_INTERVAL_SECONDS` | Maximum seconds before an unfulfilled batch flushes | `2.0` | Worker |
| `LOG_LEVEL` | Application logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` | Backend, Worker |
| `CORS_ORIGINS` | Permitted CORS origins (comma-separated or `*`) | `*` | Backend |

---

## Local Development & Setup Guide

### Prerequisites

* **Python 3.11+**
* **PostgreSQL 14+** (running on port 5432)
* **Redis 7+** (running on port 6379)
* **Nginx** (or Docker Compose)

### 1. Database Initialization

Apply the idempotent schema migration and seed data script:

```bash
# Create database (if needed)
createdb quorum

# Apply schema & seed data
psql -U postgres -d quorum -f db/init.sql
```

### 2. Environment Configuration

Copy the example environment configuration:

```bash
cp .env.example .env
```

### 3. Backend API Setup

Set up a virtual environment and launch the FastAPI server with Uvicorn:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ..
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

The API will be accessible at:
* Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)
* Health probe: [http://localhost:8000/healthz](http://localhost:8000/healthz)
* Readiness probe: [http://localhost:8000/readyz](http://localhost:8000/readyz)

### 4. Asynchronous Worker Setup

Run the vote persistence worker in a separate terminal:

```bash
cd worker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ..
python3 -m worker.app.worker
```

### 5. Frontend Dashboard

#### Option A: Running with Docker Compose (Recommended)

Spin up the entire stack with a single command:

```bash
docker compose up -d
```
Open [http://localhost:8080](http://localhost:8080) in your browser.

#### Option B: Running with Nginx

Copy static files and configuration to your Nginx root:

```bash
sudo cp -r frontend/src/* /usr/share/nginx/html/
sudo cp frontend/nginx.conf /etc/nginx/conf.d/quorum.conf
sudo nginx -s reload
```

---

## API Contract & cURL Examples

### 1. Liveness Probe (`GET /healthz`)
Validates process liveness.

```bash
curl -i http://localhost:8000/healthz
```
```json
{"status": "alive"}
```

### 2. Readiness Probe (`GET /readyz`)
Validates downstream PostgreSQL and Redis connectivity.

```bash
curl -i http://localhost:8000/readyz
```
```json
{"status": "ready", "database": "connected", "redis": "connected"}
```

### 3. List All Polls (`GET /api/polls`)

#### A. Pre-Vote State (Statistics Concealed)
When queried with a voter UID who has not yet voted:

```bash
curl -s "http://localhost:8000/api/polls?voter_fingerprint=fresh-voter-001"
```
```json
[
  {
    "id": 1,
    "title": "What is your preferred container orchestration tool?",
    "description": "Cast your vote for the primary orchestration framework driving your modern infrastructure stack.",
    "created_at": "2026-09-19T07:21:26.491925Z",
    "options": [
      {"id": 1, "poll_id": 1, "label": "Kubernetes", "vote_count": null, "percentage": null},
      {"id": 2, "poll_id": 1, "label": "Docker Swarm", "vote_count": null, "percentage": null},
      {"id": 3, "poll_id": 1, "label": "Nomad", "vote_count": null, "percentage": null},
      {"id": 4, "poll_id": 1, "label": "Bare Metal", "vote_count": null, "percentage": null}
    ],
    "has_voted": false,
    "user_voted_option_id": null,
    "total_votes": null
  }
]
```

#### B. Post-Vote State (Statistics Revealed)
When queried with a voter UID who has cast a ballot:

```bash
curl -s "http://localhost:8000/api/polls/1?voter_fingerprint=fresh-voter-001"
```
```json
{
  "id": 1,
  "title": "What is your preferred container orchestration tool?",
  "description": "Cast your vote for the primary orchestration framework driving your modern infrastructure stack.",
  "created_at": "2026-09-19T07:21:26.491925Z",
  "options": [
    {"id": 1, "poll_id": 1, "label": "Kubernetes", "vote_count": 2, "percentage": 50.0},
    {"id": 2, "poll_id": 1, "label": "Docker Swarm", "vote_count": 1, "percentage": 25.0},
    {"id": 3, "poll_id": 1, "label": "Nomad", "vote_count": 1, "percentage": 25.0},
    {"id": 4, "poll_id": 1, "label": "Bare Metal", "vote_count": 0, "percentage": 0.0}
  ],
  "has_voted": true,
  "user_voted_option_id": 1,
  "total_votes": 4
}
```

### 4. Cast a Vote (`POST /api/polls/{poll_id}/vote`)

#### A. Initial Vote Submission (Success)
```bash
curl -i -X POST http://localhost:8000/api/polls/1/vote \
  -H "Content-Type: application/json" \
  -d '{
    "option_id": 1,
    "voter_fingerprint": "fresh-voter-001"
  }'
```
**Response (`202 Accepted`)**:
```json
{
  "status": "queued",
  "poll_id": 1,
  "option_id": 1
}
```

#### B. Duplicate Submission (Rejection)
Attempting to vote a second time or change the ballot with the same UID returns `HTTP 409 Conflict`:

```bash
curl -i -X POST http://localhost:8000/api/polls/1/vote \
  -H "Content-Type: application/json" \
  -d '{
    "option_id": 2,
    "voter_fingerprint": "fresh-voter-001"
  }'
```
**Response (`409 Conflict`)**:
```json
{
  "detail": "You have already cast a vote on this poll. Changing votes is not permitted."
}
```

---

## Production Containerization & Kubernetes Readiness

* **Anti-Fraud Uniqueness**: Database enforces `uq_votes_poll_voter` on `votes(poll_id, voter_hash)`.
* **Idempotent Batch Consumer**: Worker executes `INSERT INTO votes ... ON CONFLICT (poll_id, voter_hash) DO NOTHING RETURNING poll_id, option_id` to guarantee exactly-once persistence.
* **Health Probes**: `/healthz` and `/readyz` map directly to Kubernetes `livenessProbe` and `readinessProbe`.
* **Zero Vote Loss**: Asynchronous worker intercepts `SIGTERM` and `SIGINT` to flush pending in-memory buffers before shutdown during Kubernetes rolling updates.
* **Zero Client Stale Cache**: Nginx sets strict `no-cache` policies for application bundles (`.css`, `.js`) and dynamic Docker DNS resolution.
