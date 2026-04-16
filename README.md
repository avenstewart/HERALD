# HERALD

**Highly Extensible Aggregator for Real-time Linked Data**

A self-hosted, open-source news aggregation pipeline. HERALD pulls articles from curated RSS sources, extracts clean text with [Trafilatura](https://trafilatura.readthedocs.io/), stores them in Postgres with full-text search, and exposes the corpus to consuming agents through a [FastMCP](https://github.com/jlowin/fastmcp) server.

The system is designed to deploy as a self-contained Docker stack **or** be pointed at externally-managed Postgres / Redis / TimescaleDB instances — the difference is a single config flag.

All HERALD containers are prefixed `herald_` and attach to a Docker network with statically-assigned IPv4 addresses (set in `.env`).

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                         HERALD                               │
│              Article Stream + Event Stream                   │
└─────────────────┬────────────────────────┬───────────────────┘
                  │                        │
         ┌────────▼────────┐     ┌─────────▼──────────┐
         │  ARTICLE STREAM  │    │   EVENT STREAM      │
         │                  │    │   (planned)         │
         │  RSSHub*         │    │                     │
         │  → Feed Poller   │    │  GDELT v2           │
         │  → Trafilatura   │    │  Events / GKG       │
         │  → Postgres      │    │  → TimescaleDB      │
         │                  │    │                     │
         │  Fundus*         │    │  GDELT DOC API*     │
         └────────┬─────────┘    └──────────┬──────────┘
                  │                         │
                  └──────────┬──────────────┘
                             │
                    ┌────────▼────────┐
                    │   FastMCP       │
                    │   Server        │
                    │   :8000         │
                    └─────────────────┘
```

`*` items marked planned are scheduled for follow-up releases. The current release ships the article stream end-to-end (poller → extractor → Postgres → MCP) plus database scaffolding for the GDELT and Fundus components.

---

## What's in this release

| Component | Status |
|---|---|
| `feed_poller` — direct-RSS poller | ✅ shipped |
| `extractor` — Trafilatura workers | ✅ shipped |
| `mcp_server` — FastMCP query interface | ✅ shipped (article + GDELT tools) |
| `postgres` schema + migration `0001_articles` | ✅ shipped |
| `timescaledb` schema (hypertables, retention, migration `0002_gdelt`) | ✅ shipped |
| `gdelt_ingestor` service | ✅ shipped |
| `rsshub` + `browserless` for JS-rendered sources | ✅ shipped |
| Expanded source catalogue (10 sources) | ✅ shipped |
| `backfill` service (Fundus + CC-NEWS) | ✅ shipped |

---

## Container naming and network

| Container | Service | IP variable |
|---|---|---|
| `herald_postgres` | bundled articles DB | `HERALD_POSTGRES_IP` |
| `herald_timescaledb` | bundled GDELT DB | `HERALD_TIMESCALE_IP` |
| `herald_redis` | queue + dedup | `HERALD_REDIS_IP` |
| `herald_feed_poller` | RSS poller | `HERALD_FEED_POLLER_IP` |
| `herald_extractor` | Trafilatura worker | `HERALD_EXTRACTOR_IP` |
| `herald_gdelt_ingestor` | GDELT v2 ingestor | `HERALD_GDELT_INGESTOR_IP` |
| `herald_rsshub` | RSS generator for sources without native feeds | `HERALD_RSSHUB_IP` |
| `herald_browserless` | Headless Chromium for JS-rendered sites | `HERALD_BROWSERLESS_IP` |
| `herald_mcp_server` | FastMCP endpoint | `HERALD_MCP_SERVER_IP` |
| `herald_backfill` | One-shot Fundus CLI (profile `backfill`) | `HERALD_BACKFILL_IP` |

Every container attaches to an external Docker network named by `HERALD_NETWORK_NAME` (default: `herald-net`). The network is managed outside of HERALD — create it once on the target Docker host if it doesn't already exist.

### Picking a network driver

**Bridge (recommended for most users)** — Docker's default. Containers are reachable from the Docker host by their container IPs, port-mapped services are reachable from the LAN via the host's IP. No special routing setup. The `HERALD_*_IP` variables still work but are scoped to the internal Docker bridge subnet.

```bash
docker network create --driver bridge \
  --subnet 172.28.0.0/24 <network-name>
```

**Macvlan (advanced)** — Gives each container its own IP on your physical LAN, so any device on the LAN can reach containers directly. Useful when you want stable LAN-addressable services without port-mapping, or for multi-host discovery.

```bash
docker network create --driver macvlan \
  --subnet <your LAN subnet> --gateway <your LAN gateway> \
  -o parent=<host iface> <network-name>
```

> ⚠️ **Macvlan gotcha** — by Linux kernel design, the Docker host itself cannot reach containers on a macvlan network it hosts (other machines on the LAN can). If you deploy on macvlan, either run administrative commands (`./scripts/bootstrap_db.sh`, `docker exec psql`, etc.) from a different LAN machine, or add a macvlan shim interface on the host:
> ```bash
> sudo ip link add mvshim link <parent iface> type macvlan mode bridge
> sudo ip addr add <unused host IP>/32 dev mvshim
> sudo ip link set mvshim up
> sudo ip route add <container subnet>/24 dev mvshim
> ```
> Persist via your distro's network config (netplan / systemd-networkd / `/etc/network/interfaces`).

Set `HERALD_NETWORK_NAME` in `.env` to match the name you chose, and assign static IPs for each container via the `HERALD_*_IP` variables. Compose will refuse to start services with missing IP values — that's intentional, it prevents accidental address collisions.

### Horizontally scaling the extractor
Because a fixed `container_name` precludes `deploy.replicas`, the single `herald_extractor` container is the default. To add extraction capacity, launch additional containers (e.g. `herald_extractor_2`) on distinct IPs with the same `.env`; they'll join the same Redis consumer group (`EXTRACTOR_CONSUMER_GROUP=herald-extractors`) automatically.

---

## Quickstart — standalone (bundled databases)

```bash
git clone <this-repo> herald
cd herald

# 1. Configure
cp .env.example .env
${EDITOR:-nano} .env       # passwords, network name, HERALD_*_IP addresses

# 2. Start the bundled databases
docker compose up -d postgres timescaledb redis

# 3. Create app DBs/users/extensions, run migrations
./scripts/bootstrap_db.sh

# 4. Start the application services
docker compose up -d feed_poller extractor mcp_server

# 5. Verify
docker compose ps
docker compose logs -f extractor   # should see "stored" events within ~5 min
curl -s http://localhost:8000/healthz || true
```

`Makefile` shortcuts: `make up`, `make down`, `make logs`, `make ps`, `make bootstrap`, `make test`, `make migrate`.

---

## Deploying to a remote Docker host

HERALD is deployed in this project against a remote Docker host on the local network. The mechanism is stock Docker contexts — the compose file is unchanged.

```bash
# One-time: register the remote host as a Docker context
docker context create herald-remote --docker host=tcp://localhost:2375

# Activate it
docker context use herald-remote

# From here on, every `docker compose ...` command runs against the remote host
docker compose up -d
docker compose logs -f mcp_server

# Switch back to local
docker context use default
```

If the remote host is reachable only over SSH, use `ssh://user@ipaddress` as the host URL instead of `tcp://...`.

> **Note on volumes:** the bundled `postgres_data`, `timescale_data`, and `redis_data` volumes live on whichever Docker host is active. When you switch contexts, you switch data stores. For production deployments, point HERALD at managed databases (next section) and avoid the bundled containers.

---

## Using external Postgres / Redis / TimescaleDB

To run HERALD against existing managed instances:

1. **Edit `.env`** — set `*_HOST`, `*_PORT`, `*_USER`, `*_PASSWORD` for each external service.
2. **Disable the bundled containers** — set `COMPOSE_PROFILES=` (empty), and `USE_BUNDLED_POSTGRES=false`, `USE_BUNDLED_TIMESCALE=false`, `USE_BUNDLED_REDIS=false`.
3. **Provide bootstrap superuser creds** — `BOOTSTRAP_PG_SUPERUSER` / `BOOTSTRAP_PG_SUPERPASS` must be a principal authorized to `CREATE DATABASE` and `CREATE ROLE` on the target instance. Same for the TimescaleDB target.
4. **Run bootstrap** — `./scripts/bootstrap_db.sh`. The script creates the application DB and role on each target, installs `pgcrypto`, `pg_trgm`, and `timescaledb`, runs Alembic migrations.
5. **Start the application services** — `docker compose up -d feed_poller extractor mcp_server`.

The bootstrap script is **idempotent** — re-running it against an already-configured instance is safe and does not change existing data.

---

## Configuration reference

Every config knob is a single environment variable. Defaults match the bundled docker-compose stack.

| Variable | Default | Purpose |
|---|---|---|
| `COMPOSE_PROFILES` | `bundled-db` | Set to empty when using external DBs |
| `USE_BUNDLED_POSTGRES` | `true` | Application-side hint (not currently consumed by code) |
| `USE_BUNDLED_TIMESCALE` | `true` | Same |
| `USE_BUNDLED_REDIS` | `true` | Same |
| `POSTGRES_HOST` / `_PORT` / `_DB` / `_USER` / `_PASSWORD` | `postgres` / `5432` / `herald` / `herald` / *(required)* | Articles database |
| `TIMESCALE_HOST` / `_PORT` / `_DB` / `_USER` / `_PASSWORD` | `timescaledb` / `5432` / `herald_ts` / `herald` / *(required)* | GDELT database (provisioned now, unused until next release) |
| `REDIS_HOST` / `_PORT` / `_DB_APP` / `_DB_RSSHUB` / `_PASSWORD` | `redis` / `6379` / `0` / `1` / *(empty)* | Queue + dedup state |
| `MCP_HOST` / `_PORT` / `_TRANSPORT` | `0.0.0.0` / `8000` / `streamable-http` | MCP server bind |
| `SOURCES_FILE` | `/app/sources/feeds.yaml` | Source catalogue path inside containers |
| `HERALD_NETWORK_NAME` | `herald-net` | Name of the external Docker network containers join |
| `HERALD_POSTGRES_IP` / `_TIMESCALE_IP` / `_REDIS_IP` / `_FEED_POLLER_IP` / `_EXTRACTOR_IP` / `_MCP_SERVER_IP` | *(required)* | Static IPv4 for each container on the external network |
| `EXTRACTOR_CONSUMER_GROUP` | `herald-extractors` | Redis Streams consumer group name |
| `EXTRACTOR_FETCH_TIMEOUT` | `15` | HTTP timeout (seconds) for article downloads |
| `DEDUP_TTL_DAYS` | `30` | TTL for the seen-URLs dedup window |
| `BOOTSTRAP_PG_SUPERUSER` / `_SUPERPASS` | `postgres` / *(required)* | Used by bootstrap script only |
| `BOOTSTRAP_TS_SUPERUSER` / `_SUPERPASS` | `postgres` / *(required)* | Used by bootstrap script only |
| `LOG_LEVEL` | `INFO` | Service log level |

URLs (`postgresql://...`, `redis://...`) are **assembled in `shared/settings.py`** from the parts above — there are no whole-URL env vars. This keeps the host swappable without string surgery.

---

## Services overview

### `feed_poller`
Async loop that polls every source in `sources/feeds.yaml` at its tier-defined interval (A: 2 min, B: 5 min, C: 15 min). Deduplicates URLs against a Redis SET (30-day TTL) and pushes new URLs onto the `herald:article_queue` Redis Stream as JSON-ish messages with source metadata.

### `extractor`
Workers (default 4 replicas) that consume from `herald:article_queue` via a shared Redis Streams consumer group (`herald-extractors`). Each worker downloads the article HTML with Trafilatura, extracts clean text + metadata, and upserts into the `articles` table. Messages are ACKed only after a successful insert so failures are retryable.

### `mcp_server`
FastMCP server on port 8000. Exposes article query tools to consuming agents. Connects to Postgres via an asyncpg pool. GDELT tools are scheduled for the next release.

### `gdelt_ingestor`
Polls `http://data.gdeltproject.org/gdeltv2/lastupdate.txt` every 60s. When a new 15-minute batch appears, downloads the Events and GKG CSVs (Mentions is skipped by default), parses them, and bulk-inserts into TimescaleDB hypertables. Batch state tracked in a `gdelt_state` singleton table so restarts don't re-ingest.

**Observed scale:** ~1,000 events + ~900 GKG records per 15-min batch → ~180k rows/day, ~65M rows/year. Row payload is small (events ≈ 250 B, GKG ≈ 1 KB with the top-40 GCAM dims). At 2-year retention, expect **~5–7 GB** on the Timescale volume before TimescaleDB compression, ~2 GB after.

### `rsshub` + `browserless`
Third-party containers that generate RSS feeds for sites lacking native ones. [RSSHub](https://docs.rsshub.app/) supports hundreds of named routes (Reuters, AP, Politico, Brookings, etc.). It caches responses in Redis DB 1 (separate from the app queue) and delegates JS-heavy pages to `browserless/chromium` via Puppeteer over WebSocket.

Source entries in `feeds.yaml` that declare `rsshub_route:` instead of `url:` are resolved against `http://${RSSHUB_HOST}:${RSSHUB_PORT}` at poll time.

### `backfill`
One-shot CLI backed by [Fundus](https://github.com/flairNLP/fundus) for historical data. Not started by default — invoked on demand via compose profile `backfill`:

```bash
# List every publisher Fundus knows about
docker compose --profile backfill run --rm backfill list-publishers

# Live-crawl publishers' websites for the last 3 months
docker compose --profile backfill run --rm backfill \
  crawl --publishers us.APNews,uk.BBC,qa.AlJazeera --months 3

# Pull historical articles from the CC-NEWS CommonCrawl archive
docker compose --profile backfill run --rm backfill \
  ccnews --publishers us.APNews --start 2024-01-01 --end 2024-06-01

# Summarize DB counts by extraction method (trafilatura / fundus / fundus_ccnews)
docker compose --profile backfill run --rm backfill status
```

Backfilled articles land in the same `articles` table as the live pipeline. The `extraction_method` column distinguishes rows (`trafilatura` for live, `fundus` for live-crawled, `fundus_ccnews` for CommonCrawl). URL conflicts do idempotent updates, so re-running a backfill is safe.

---

## MCP tool reference

All tools below are available in this release via the `mcp_server` service.

### `search_articles(query, start_date?, end_date?, sources?, categories?, language="en", limit=20)`
Full-text search using Postgres `tsvector` + `websearch_to_tsquery`. Results ranked by relevance then recency.

### `get_recent_articles(lookback_minutes=60, sources?, categories?, limit=50)`
Articles ingested in the last *N* minutes, newest first.

### `get_article_by_url(url)`
Fetch a single article by its (normalized) URL.

### `list_sources()`
Returns every configured source plus its last-ingest time and 24h article count.

### `get_ingestion_status()`
Queue depth, pending consumer-group messages, total + 24h article counts, last ingest timestamp.

### GDELT tools

#### `get_gdelt_events(start_date, end_date, actor1_country?, actor2_country?, cameo_root_code?, min_goldstein?, max_goldstein?, min_mentions?, limit=50)`
Query CAMEO-coded events from GDELT v2. Actor countries use FIPS 2-char codes. CAMEO root codes are the 20 coarse categories (e.g. `14` = PROTEST, `19` = FIGHT).

#### `get_gdelt_themes(themes, start_date, end_date, limit=50)`
Find GKG records whose `themes` array overlaps any of the given codes (see `sources/gdelt_themes.yaml` for a curated catalogue).

#### `get_gdelt_tone_timeline(start_date, end_date, themes?, resolution='hour')`
Average media tone and article volume bucketed over time (`minute`, `hour`, or `day`). Useful for detecting sentiment regime shifts.

#### `get_gdelt_entities(entity_type, start_date, end_date, min_mentions=5, limit=50)`
Top-N most-mentioned persons, organizations, or locations in a window.

#### `gdelt_doc_search(query, timespan='24h', mode='artlist', max_records=75)`
Proxy to GDELT's DOC 2.0 API — searches GDELT's 3-month rolling full-text index. Supports query operators like `theme:ECON_INFLATION`, `tone<-5`, `sourcecountry:US`, and phrase search.

### Planned
`get_article_volume` — time-series of article counts (detecting news volume spikes).

---

## Adding sources

Edit `sources/feeds.yaml`. Each entry needs a `name`, `tier` (A/B/C), `category`, and exactly one of `url` (direct RSS) or `rsshub_route` (RSSHub path — only usable once the RSSHub service ships).

```yaml
sources:
  - name: My Wire Feed
    url: https://example.com/feed.xml
    tier: B
    category: world
```

Restart the poller to pick up changes: `docker compose restart feed_poller`.

---

## Data retention policy

| Data | Retention | Notes |
|---|---|---|
| `articles.content` | 6 months rolling | (planned cron) Truncate body, keep metadata |
| `articles` metadata | 2 years | title, url, source, published_at, category |
| `gdelt_events` | 2 years | TimescaleDB `drop_chunks()` (planned) |
| `gdelt_gkg.gcam_scores` | 6 months | Large JSONB; truncate older rows (planned) |
| `gdelt_gkg` metadata | 2 years | themes, persons, orgs, tone, url |

Retention enforcement jobs land alongside the GDELT release.

---

## Development

```bash
# Local Python environment
uv sync --all-extras

# Run tests
make test
# or
uv run pytest -v

# Lint / format
make lint
make fmt

# Add a migration
uv run alembic revision -m "describe change"
# edit migrations/versions/<rev>_describe_change.py
uv run alembic -x db=postgres upgrade head
# (or -x db=timescale for the GDELT target)
```

The `shared/` package is the only place env vars are read; service code imports `from shared.settings import settings`.

---

## What HERALD does not do

- **No NLP enrichment** beyond what Trafilatura provides natively. Consuming systems add entity extraction, sentiment, classification, etc. as post-processing.
- **No paywall bypass.** Trafilatura works on publicly-fetchable HTML only. Paywalled stories may be stored as metadata-only records (title/url/published_at) with `null` content.
- **No social media** (Twitter/X, Reddit). Different problem, different rate-limiting and legal posture.
- **No relevance ranking** of sources. All articles are equal; consuming systems apply their own logic.

---

## License

MIT.
