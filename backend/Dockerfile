FROM python:3.12-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]" 2>/dev/null || pip install --no-cache-dir .

COPY src/ src/
COPY alembic.ini .
COPY alembic/ alembic/

ENV PYTHONPATH=/app/src
ENV PORT=8001
EXPOSE 8001

CMD ["sh", "-c", "uvicorn contextedge.main:app --host 0.0.0.0 --port ${PORT:-8001}"]
