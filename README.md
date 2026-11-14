# finance-tracker

[![Tests](https://github.com/PabloRS98/finance-tracker/actions/workflows/tests.yml/badge.svg)](https://github.com/PabloRS98/finance-tracker/actions/workflows/tests.yml)
[![Container Smoke Tests](https://github.com/PabloRS98/finance-tracker/actions/workflows/despliegue.yml/badge.svg)](https://github.com/PabloRS98/finance-tracker/actions/workflows/despliegue.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Version 1.0** · Personal net worth, portfolio, and income/expense tracker.
Local-first with no paid APIs: prices come from free public data sources and
everything lives in a single SQLite database inside a Docker volume. Supports
multiple household profiles, each with isolated data.

**Stack**: FastAPI · SQLAlchemy 2.0 · SQLite · Jinja2 · Chart.js (vendored, zero build step).

| Document | Purpose |
|---|---|
| This README | Install, configure, and run |
| [CHANGELOG.md](CHANGELOG.md) | What changed and **why** *(ES)* |
| [ROADMAP.md](ROADMAP.md) | Intentional scope boundaries and accepted debt *(ES)* |
| [ARQUITECTURA.md](ARQUITECTURA.md) | Engineering decisions and the incidents behind them *(ES)* |

## Features

- **Net worth**: four asset classes (bank accounts, equities/ETFs/funds, crypto,
  real estate/other) valued in a shared base currency, with daily & intraday
  history and a portfolio treemap (area = weight, color = daily move).
- **Investments**: positions derived from buy/sell trades with average cost
  basis, realized and unrealized P&L, and FX impact breakdown.
- **Performance**: TWR (measures strategy), XIRR (measures your capital by
  weighting every cash flow by time), CAGR, and calendar-year returns compared
  against chosen benchmarks.
- **Income & expenses**: categorized transactions, category budgets, recurring
  rules with catch-up, and bank CSV import.
- **Trade importers**: Trade Republic, Revolut (CSV & PDF), OKX, and a generic
  format, with fingerprint-based deduplication.
- **Analytics**: allocations by currency/region/sector, cumulative fees, and a
  risk X-Ray (concentration, FX exposure, stale prices).
- **Rebalancing**: target weight per asset, cash deviation, and contribution
  allocation toward underweight holdings (buys only, never suggests sells).
- **Telegram price alerts**: target prices above/below and daily drop percentage
  thresholds; alerts fire once and re-arm when conditions reset.
- **Portfolio hygiene**: duplicate asset detection (same security bought across
  two brokers) with guided merge, per-account position breakdown, and closed
  positions moved out of active totals while preserving full P&L history.
- **Watchlist**: track prices for securities you don't hold yet without
  affecting net worth totals.
- **Spanish voice entry**: via browser or Telegram, with explicit confirmation
  before applying changes.

## Getting Started

### Docker (recommended)

```bash
cp .env.example .env       # optional: starts with sensible defaults without .env
docker compose up -d --build
```

The app runs at <http://localhost:8001> (override with `FINANCE_PORT`). The
`finance-tracker_data` volume stores the SQLite database and daily backups —
that's the only volume you need to back up.

The port binds to **loopback only** (`127.0.0.1`) by default. To reach it from
a phone on your LAN, set `FINANCE_BIND=0.0.0.0` in `.env` and enable
`ENABLE_AUTH=true` with a strong password.

The container starts as root just long enough for the entrypoint to fix
ownership on `/data` (existing Docker volumes retain prior permissions), then
drops privileges immediately via `setpriv`. The app never runs as root.

### Local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
echo "DB_PATH=./finance.db" >> .env        # /data only exists inside Docker
uvicorn app.main:app --reload
```

### Upgrading

```bash
git pull && docker compose up -d --build
```

No manual steps required. The entrypoint runs pending Alembic migrations on
startup, and databases created before Alembic are detected and upgraded
automatically. If a migration fails, startup aborts instead of serving against
a mismatched schema.

Verify container health:

```bash
docker compose ps          # should report healthy, not just running
curl -s localhost:8001/salud
```

`/salud` runs a live database check: if it returns `503` (`esquema desactualizado`
or `consulta de prueba fallida`), check `docker compose logs`.

## Household Accounts

On first visit, the app prompts you to create an account and sign in. Each
person sees **only their own** assets, trades, expenses, categories, alerts, and
net worth — there is no shared view and no admin superuser.

Account passwords are **optional** (one-click login for convenience at home,
configurable anytime in Settings). If you expose the app beyond `localhost`,
you **must** also enable `ENABLE_AUTH` in `.env` as a perimeter gate.

## Configuration

All settings use environment variables (or `.env`), each with a sensible default.
See [`.env.example`](.env.example) and `app/config.py` for the full list. Key
security & deployment variables:

| Variable | Why it matters |
|---|---|
| `FINANCE_BIND` | Bind address. `127.0.0.1` by default; `0.0.0.0` exposes to the LAN. |
| `ENABLE_AUTH` | Disabled by default. **Enable it** if reachable outside `localhost`, even over VPN. |
| `AUTH_PASSWORD` | Defaults to `changeme`; with `ENABLE_AUTH=true` the app refuses to start if left unchanged or under 8 characters. |
| `DB_PATH` | Must point to a persistent volume. |
| `BASE_CURRENCY` | Reporting currency for totals. Changing it after loading data does not retroactively convert historical entries. |

### CSRF

Every state-changing request requires a CSRF token (`csrftoken` cookie + `_csrf`
form field or `X-CSRF-Token` header). Handled automatically in the browser; for
manual API calls:

```bash
curl -c cookies.txt -s http://localhost:8000/ | grep csrf-token
curl -b cookies.txt -X POST http://localhost:8000/api/refresh-prices \
     -H "X-CSRF-Token: <token>"
```

If serving behind HTTPS, set `secure=True` on the cookie in `app/csrf.py`.

### Telegram Bot (optional)

Create a bot with [@BotFather](https://t.me/BotFather), set `TELEGRAM_BOT_TOKEN`,
and start the app. Message the bot on Telegram to receive your `chat_id`, set
`TELEGRAM_CHAT_ID`, and restart. Supports text/voice trade & expense commands
and daily portfolio summaries via long polling (no public webhook needed).

## Development

### Real Financial Data Policy

**This repository is public.** `.gitignore` blocks databases, `.env`, and bank
CSV/PDF statements, and all sample tickers in tests and docs (`AAPL`, `VWCE.DE`,
`bitcoin`) are synthetic placeholders. Never include real balances, holdings,
ISINs, or broker account details in commits, PRs, or changelogs.

### Tests

```bash
pip install pytest
pytest -q
```

Tests run offline against an in-memory SQLite database with external APIs mocked
across three layers:

- **Services** (`tests/test_*.py`): position math, history, recurring rules,
  voice parser, importers, classification, performance metrics.
- **HTTP integration** (`tests/test_endpoints.py`, etc.): route responses, auth
  enforcement across every endpoint, HTML escaping.
- **Visual invariants** (`tests/test_sistema_visual.py`): enforces CSS design
  system constraints and dialog visibility rules.

### Continuous Integration

Two workflows, four checks:

| Check | Purpose |
|---|---|
| `lint` | `ruff check .` |
| `pytest` | Full test suite with `--cov-fail-under=74` |
| `auditoria` | `pip-audit` against `requirements.txt` |
| `contenedor` | Builds Docker image and tests empty volume, pre-Alembic DB upgrade, root-owned `/data` recovery, non-root PID 1, and `/salud` 503 failure detection |

### Migrations

Schema changes are managed with Alembic:

```bash
alembic upgrade head
alembic revision --autogenerate -m "..."
```

The app runs `alembic upgrade head` automatically on startup. Never run
`alembic stamp head` on a legacy pre-Alembic database — `init_db()` detects and
upgrades it automatically.

## Data & Backups

- A daily job writes consistent SQLite backups to `<DB_PATH dir>/backups/finance-YYYYMMDD.db`
  and rotates older files according to `BACKUP_KEEP`.
- Fresh backups can also be downloaded from the dashboard UI.
- Transactions can be exported to CSV from `/transacciones`.

## Data Sources

All public, no API keys required:

| Source | Usage |
|---|---|
| [Frankfurter](https://frankfurter.dev) (ECB) | Current and historical FX rates |
| Yahoo Finance (chart API) | Stock/ETF/fund prices and benchmark indices |
| [CoinGecko](https://coingecko.com) | Crypto prices and history |

If a source is unavailable, the app never fabricates prices: unconvertible assets
are excluded from totals with an on-screen warning, and incomplete historical
snapshots are skipped.

## License

MIT — see [`LICENSE`](LICENSE).

Vendored assets (Chart.js and Lucide icons under MIT, Inter font under SIL Open
Font License 1.1) are included so the app works offline with no CDN or build
step. See [`NOTICE`](NOTICE) for full attributions.
