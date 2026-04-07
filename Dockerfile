# --- Stage 1: build React frontend ---
FROM node:20-alpine AS frontend

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build


# --- Stage 2: Python runtime ---
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
  && rm -rf /var/lib/apt/lists/*

# Copy source needed for install (setuptools reads package metadata)
COPY pyproject.toml ./
COPY polymarket_weather/ polymarket_weather/

RUN pip install --no-cache-dir ".[web]"

# Config + static assets + Alembic migrations
COPY config/ config/
COPY config.railway.yaml config.yaml
COPY alembic.ini ./
COPY migrations/ migrations/
COPY --from=frontend /app/frontend/dist ./frontend/dist

ENV PYTHONUNBUFFERED=1
ENV CONFIG_PATH=/app/config.yaml

# Drop privileges — never run the process as root.
RUN useradd --create-home --uid 1000 --shell /bin/bash appuser \
  && chown -R appuser:appuser /app
USER appuser

# Railway injects $PORT; polymarket_weather.config reads it and binds FastAPI to 0.0.0.0:$PORT
CMD ["python", "-m", "polymarket_weather.server"]
