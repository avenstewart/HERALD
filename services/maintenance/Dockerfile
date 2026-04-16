FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    "pydantic>=2.8" "pydantic-settings>=2.4" \
    "sqlalchemy[asyncio]>=2.0.30" "asyncpg>=0.29" \
    "python-dateutil>=2.9" "structlog>=24.1"

COPY shared/  ./shared/
COPY services/__init__.py ./services/__init__.py
COPY services/maintenance/ ./services/maintenance/

ENV PYTHONPATH=/app

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "services.maintenance.worker"]
