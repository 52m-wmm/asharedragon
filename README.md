# AShareDragon

A local-first **A-share historical indicator and trading-research workspace**. Built with Next.js / React / TypeScript / ECharts, FastAPI, PostgreSQL, and DuckDB/Parquet. The primary navigation is **Market Overview → Indicator Lab → Stock Research**, with secondary Data Management tools.

**Confirmed events and rule-reconstructed candidates are different evidence classes. Seasonality has not been rigorously established. This project does not execute trades or guarantee returns.**

![AShareDragon Indicator Lab with smooth historical indicator curves](docs/screenshots/homepage.png)

*Indicator Lab showing four aggregate historical series: limit-up stocks, first boards, second boards, and third boards or higher. The 90-day view is zoomed into a shorter interval. Smooth curves affect display only; underlying daily values and evidence classifications remain unchanged. This user-provided screenshot contains no stock-level price records, raw responses or credentials.*

## What is implemented

- **Market Overview** (`/`): historical breadth, advancing/declining counts, limit events, ladders, fixed-cohort promotion rates, positive/negative evidence and matched-cohort D1 changes. Unknown values remain null.
- **Indicator Lab** (`/indicator-lab`): 13 existing historical indicators including QQQ; searchable categories, multi-series smooth curves, raw/Z-score views, two axes, date ranges, zoom and browser-local saved combinations. Smoothing affects display only, never values or missing-data gaps.
- **Stock Research** (`/stock`, `/pool`, `/dragons`, `/seasonality`): unadjusted historical candles, limit-up lists, streak rankings, IPO classification, cycle evidence and exploratory seasonality. Existing URLs remain available.
- **Shared stock scope**: all stocks / exclude historical ST / exclude IPO special stages, including explicit handling of unknown identity. Historical rules are not replaced by a fixed listing-age cutoff.
- **Data Management** (`/data`, `/data/operations`): existing Inventory, field quality, snapshot versions and parent dependencies, collection jobs and source-error logs. Supplier views remain at `/supplier` and `/supplier/pool`.
- **Versioned research releases**: U/H/E1/E2/E3/D1 plus non-IPO classification dependencies, with installation, consistency checking and rollback tooling. These tools require explicitly provisioned local data; starting the app does not publish or activate a release.

The locally validated active calendar spans 2,391 trading days from 2017-01-03 through 2026-11-10. Calendar coverage is not proof of complete stock coverage or independently confirmed historical events. A separate 2024–2026 candidate experiment is **not silently mixed into active Indicator Lab data**.

## Architecture

```text
Authorized local/provider observations
        ├── PostgreSQL: operational records, provenance and job queues
        └── local Parquet + DuckDB: versioned research snapshots
                         ↓
              FastAPI read/query services
                         ↓
       Next.js + React + TypeScript + ECharts
```

Source adapters, normalization, analytics, research queries and UI are separate modules. Derived statistics retain evidence classes (`confirmed`, `reconstructed_candidate`, `unknown`) and do not overwrite original observations. Research snapshots and provider archives stay outside Git.

## Evidence and limitations

- Independently supported positive events take precedence over reconstruction. Reconstruction uses historical board rules and trusted references, and remains a candidate classification.
- Unknown is not a failed outcome or zero market activity. Coverage, IPO origin, historical ST status, boundary completeness and tradability are independent attributes.
- Unknown boundaries produce observed minimum heights, not invented final heights. Unique observation-segment keys do not prove that segments separated by unknown dates are independent economic cycles.
- The initial IPO sequence is not directly comparable with an ordinary post-listing streak. IPO-origin unknown is excluded from the non-IPO research main sample.
- Monthly launch distributions use locatable first-board dates, not peak-price months. Uneven annual coverage, sparse independent evidence, repeated stocks, corporate actions and selection bias limit interpretation.
- Feature availability is incomplete. Missing historical capitalisation, order-book, announcement-time or execution information is not filled with present-day values.
- QQQ is aligned to the last completed US session before 09:25 Asia/Shanghai, accounting for US holidays and daylight saving. Strict historical-vintage checks can leave it unavailable; explicitly selected retrospective mode remains candidate evidence, not a certified point-in-time trading dataset. Raw QQQ closes are not total returns. NQ and A-share index extensions have no fabricated values.
- Z-scores use the selected historical window for visual comparison, not leakage-free prediction.
- No automated trading, return guarantee, validated predictive model, or claim of formally established seasonality is provided.

Adapters include AKShare and BaoStock, optional Tushare-compatible access, and tools for locally licensed historical files. Provider access is not redistribution permission. Optional US-market context research is also documented separately. **No third-party dataset, purchased archive, credentials, database or raw API response is bundled.**

## Local setup

Prerequisites: Python 3.12+, Node.js compatible with Next.js 16 (the local validation used Node 24), and PostgreSQL 16 or a compatible installation. Commands below run from the project root.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r backend/requirements.lock.txt
.venv/bin/pip install --no-deps -e backend
npm ci --prefix frontend
cp .env.example .env
createdb asharedragon  # once, after starting PostgreSQL
.venv/bin/alembic -c backend/alembic.ini upgrade head
```

Set `DATABASE_URL` in the ignored `.env` for your local PostgreSQL instance. The example uses localhost without a password; configure credentials privately if required. Optional provider credentials default to empty. Never commit `.env` or put credentials in URLs or screenshots.

Start the API and frontend in separate terminals:

```bash
PYTHONPATH=.:backend .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
npm run dev --prefix frontend
```

Visit <http://127.0.0.1:3000> and API documentation at <http://127.0.0.1:8000/docs>. For a production frontend, run `npm run build --prefix frontend`, then `npm run start --prefix frontend`.

The frontend defaults to `http://127.0.0.1:8000`. To change it, set `NEXT_PUBLIC_API_URL` in `frontend/.env.local` before building; configure matching backend `CORS_ORIGINS` in the root `.env`. Next.js does not automatically read the parent `.env`.

The optional `./scripts/dev.sh` also starts the collection worker; the separate commands above do not. Only run `.venv/bin/python -m app.worker` when you intend to process queued collection jobs.

### Research data is not included

A fresh clone provides software, not populated research screens. Existing snapshot readers expect locally provisioned, authorized artifacts under `~/Datasets/asharedragon/`, including the unified research and historical-statistics manifests. Standalone plotting/report scripts may additionally require Matplotlib. Some research routes require their matching parent snapshots and return unavailable/empty states without them. The setup commands do not reconstruct the private reference dataset or download missing data.

Use the [documentation index](docs/README.md) for schema, snapshot construction order and study-specific reproduction instructions. Historical scripts may assume the original local folder layout and access rights. Do not substitute fabricated data for unavailable evidence.

## Validation

```bash
PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests -q
node --experimental-strip-types --test frontend/tests/*.test.mjs
npm run typecheck --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
```

PostgreSQL tests use isolated temporary schemas. Snapshot-dependent integration tests require authorized local artifacts; some skip when artifacts are absent, and older historical validation scripts assume local inputs. A successful fresh-clone installation is not equivalent to reproducing the local research results.

See [current workspace release validation](docs/workspace_release_validation.md) and [previous release validation](docs/release_validation.md) for the actual test environment, results, security review and release boundary.

## Documentation

Start with the [documentation index](docs/README.md), [architecture](docs/architecture.md), [data dictionary](docs/data_dictionary.md), [limit-rule sources](docs/historical_limit_rule_sources.md), [streak classification](docs/dragon_streak_classification.md), and [non-IPO study](docs/non_ipo_dragon_study.md).

Historical reports are retained as dated research records. Their counts and acceptance criteria belong to their stated snapshots; they are not all the current product state. Links labelled **local-only artifact** refer to excluded generated evidence, not files distributed by this repository.

## Research-use disclaimer

This is research software, not investment advice. Historical patterns and exploratory comparisons do not establish a tradable edge, future returns, or execution feasibility. You are responsible for data licences, provider terms and your own decisions. The public repository distributes implementation and methodological summaries; it does not grant rights to third-party market data.
