# -------------------------------------------------------------
# Stage 1: Build & Install dependencies with Python 3.13
# -------------------------------------------------------------
FROM python:3.13-slim AS builder

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip
COPY requirements.txt .

# Install dependencies into /install
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# -------------------------------------------------------------
# Stage 2: Distroless Debian 13 (Bundles Python 3.13)
# -------------------------------------------------------------
FROM gcr.io/distroless/python3-debian13:latest

WORKDIR /app

# Copy site-packages from the 3.13 folder
COPY --from=builder /install/lib/python3.13/site-packages /usr/lib/python3.13/site-packages
COPY --from=builder /install/bin /usr/local/bin
COPY ./app /app

ENV PYTHONPATH=/usr/lib/python3.13/site-packages:/app:/
ENV PYTHONUNBUFFERED=1

ENTRYPOINT [ "python3", "-m", "app.worker" ]
