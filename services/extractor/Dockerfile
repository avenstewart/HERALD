FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini libxml2 libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    "pydantic>=2.8" "pydantic-settings>=2.4" \
    "sqlalchemy[asyncio]>=2.0.30" "asyncpg>=0.29" \
    "redis>=5.0" "httpx>=0.27" "pyyaml>=6.0" "python-dateutil>=2.9" \
    "trafilatura>=1.12" "lxml>=5.2" "structlog>=24.1"

COPY shared/  ./shared/
COPY services/__init__.py ./services/__init__.py
COPY services/extractor/ ./services/extractor/

ENV PYTHONPATH=/app

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "services.extractor.worker"]
