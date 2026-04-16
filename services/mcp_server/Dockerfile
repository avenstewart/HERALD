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
    "asyncpg>=0.29" "redis>=5.0" "pyyaml>=6.0" \
    "python-dateutil>=2.9" "fastmcp>=0.2.0" "structlog>=24.1"

COPY shared/  ./shared/
COPY sources/ ./sources/
COPY services/__init__.py ./services/__init__.py
COPY services/mcp_server/ ./services/mcp_server/

ENV PYTHONPATH=/app

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "services.mcp_server.server"]
