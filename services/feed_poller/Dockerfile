FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    "pydantic>=2.8" "pydantic-settings>=2.4" \
    "redis>=5.0" "httpx>=0.27" "pyyaml>=6.0" "python-dateutil>=2.9" \
    "feedparser>=6.0" "tldextract>=5.1" "structlog>=24.1"

COPY shared/  ./shared/
COPY sources/ ./sources/
COPY services/__init__.py ./services/__init__.py
COPY services/feed_poller/ ./services/feed_poller/

ENV PYTHONPATH=/app

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "services.feed_poller.poller"]
