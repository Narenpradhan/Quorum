# Quorum

> **High-throughput, event-driven voting and polling platform engineered for ultra-low latency write ingestion and resilient batch persistence.**

Quorum solves the write-amplification and database locking bottlenecks typical of real-time polling platforms. Rather than writing each vote synchronously to disk, Quorum utilizes an asynchronous, event-driven architecture:
1. The **FastAPI Gateway** validates vote requests in sub-milliseconds, atomically updates in-memory counters in **Redis**, and streams vote events into a Redis queue.
2. A dedicated **Asynchronous Python Worker** consumes the stream in batches and executes atomic multi-row inserts and aggregated updates to **PostgreSQL**, minimizing database I/O and row-level locks.
3. A sleek, reactive **Vanilla Dashboard** served via **Nginx** displays live tally updates, provides instant visual feedback, and includes liveness/readiness indicators.

---

## Architecture Topology

```mermaid
graph TD
    Client["Client Browser"] -->|HTTP / SPA Traffic| Nginx["Nginx Gateway / Ingress (:80)"]
    
    subgraph Frontend Tier
        Nginx -->|Static Assets| WebApp["Vanilla HTML5 / CSS / ES6+ Dashboard"]
    end

    subgraph API Tier
        Nginx -->|Proxy /api/*, /healthz, /readyz| API["FastAPI Gateway (:8000)"]
    end

    subgraph Real-Time & Queue Tier
        API -->|1. HINCRBY Offset| RedisTallies[("Redis Hash Tallies (quorum:poll:{id}:tallies)")]
        API -->|2. RPUSH Stream| RedisQueue[("Redis Queue (quorum:vote_stream)")]
        API -.->|Read Baseline Counts| Postgres[("PostgreSQL 16 DB")]
        API -.->|Read Live Offsets| RedisTallies
    end

    subgraph Batch Persistence Tier
        Worker["Asynchronous Python Worker"] -->|BLPOP / LPOP Batch| RedisQueue
        Worker -->|Batch INSERT & UPDATE vote_count| Postgres
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
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── main.py
│   │   ├── models.py
│   │   └── schemas.py
│   └── requirements.txt
├── worker/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   └── worker.py
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── app.js
│   │   ├── index.html
│   │   └── styles.css
│   └── nginx.conf
├── db/
│   └── init.sql
├── .env.example
├── .gitignore
└── README.md
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
* **Nginx** (optional for local dev; built-in static servers or Docker can also be used)

### 1. Database Initialization

Execute the idempotent schema migration and seed data script against your PostgreSQL instance:

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
# Navigate to backend and create virtual environment
cd backend
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run FastAPI API Gateway with live reload
cd ..
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

The API will be accessible at:
* Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)
* Health probe: [http://localhost:8000/healthz](http://localhost:8000/healthz)
* Readiness probe: [http://localhost:8000/readyz](http://localhost:8000/readyz)

### 4. Asynchronous Worker Setup

Open a separate terminal to run the vote persistence worker:

```bash
cd worker
python3 -m venv .venv
source .venv/bin/activate

# Install worker dependencies
pip install -r requirements.txt

# Run worker process
cd ..
python3 -m worker.app.worker
```

### 5. Frontend Dashboard

#### Option A: Running with Nginx

Copy static files and configuration to your Nginx root:

```bash
sudo cp -r frontend/src/* /usr/share/nginx/html/
sudo cp frontend/nginx.conf /etc/nginx/conf.d/quorum.conf
sudo nginx -s reload
```

#### Option B: Running with Static HTTP Server (Development)

You can also preview the frontend directly via any static file server:

```bash
cd frontend/src
python3 -m http.server 3000
```
Open [http://localhost:3000](http://localhost:3000) in your browser. (The client script automatically routes API calls to `http://localhost:8000` when served from alternate ports).

---

## API Contract & cURL Examples

### 1. Liveness Probe
Validates that the gateway process is running.

```bash
curl -i http://localhost:8000/healthz
```

**Response (`200 OK`)**:
```json
{
  "status": "alive"
}
```

### 2. Readiness Probe
Verifies that downstream dependencies (PostgreSQL and Redis) are connected and ready to handle traffic.

```bash
curl -i http://localhost:8000/readyz
```

**Response (`200 OK`)**:
```json
{
  "status": "ready",
  "database": "connected",
  "redis": "connected"
}
```

### 3. List All Polls
Returns all active polls with their options, merging database counts with live Redis tally offsets.

```bash
curl -i http://localhost:8000/api/polls
```

**Response (`200 OK`)**:
```json
[
  {
    "id": 1,
    "title": "What is your preferred container orchestration tool?",
    "description": "Cast your vote for the primary orchestration framework driving your modern infrastructure stack.",
    "created_at": "2026-09-19T12:00:00Z",
    "options": [
      {
        "id": 1,
        "poll_id": 1,
        "label": "Kubernetes",
        "vote_count": 42,
        "percentage": 52.5
      },
      {
        "id": 2,
        "poll_id": 1,
        "label": "Docker Swarm",
        "vote_count": 18,
        "percentage": 22.5
      },
      {
        "id": 3,
        "poll_id": 1,
        "label": "Nomad",
        "vote_count": 15,
        "percentage": 18.8
      },
      {
        "id": 4,
        "poll_id": 1,
        "label": "Bare Metal",
        "vote_count": 5,
        "percentage": 6.2
      }
    ],
    "total_votes": 80
  }
]
```

### 4. Get Single Poll
Retrieves detailed breakdown for a specific poll by ID.

```bash
curl -i http://localhost:8000/api/polls/1
```

### 5. Submit a Vote
Casts a vote for an option. The vote is immediately acknowledged (`202 Accepted`), Redis in-memory tally is atomically incremented, and the event is queued for background batch persistence.

```bash
curl -i -X POST http://localhost:8000/api/polls/1/vote \
  -H "Content-Type: application/json" \
  -d '{
    "option_id": 1,
    "voter_fingerprint": "a3f8c9b2-0192-4f9a-b851-f7638d01ef4a"
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

---

## Production Containerization & Kubernetes Readiness

This repository is structured for minimal distroless and multi-stage container builds:
* **Decoupled codebases**: `backend/` and `worker/` maintain separate lightweight dependencies.
* **Health Probes**: `/healthz` and `/readyz` map directly to Kubernetes `livenessProbe` and `readinessProbe`.
* **Graceful Worker Shutdown**: The asynchronous worker intercepts `SIGTERM` and `SIGINT` to flush pending in-memory batches before exiting, preventing vote loss during Kubernetes rolling deployments or Pod terminations.
* **Auditability**: Individual votes are preserved in the `votes` audit table with timestamp and voter hash for auditing and fraud analysis.
