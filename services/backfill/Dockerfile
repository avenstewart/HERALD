FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libxml2 libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    "pydantic>=2.8" "pydantic-settings>=2.4" \
    "psycopg[binary]>=3.2" "pyyaml>=6.0" "python-dateutil>=2.9" \
    "structlog>=24.1" "typer>=0.12" "fundus>=0.6"

COPY shared/   ./shared/
COPY services/__init__.py ./services/__init__.py
COPY services/backfill/ ./services/backfill/

ENV PYTHONPATH=/app

# Default invocation shows help; real commands passed via docker compose run args.
ENTRYPOINT ["python", "-m", "services.backfill.backfill"]
CMD ["--help"]
