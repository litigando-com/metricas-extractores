# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-page Streamlit dashboard (`app.py`) that shows daily coverage of an Oracle table (`TORRE_ARCHIVOS_AWS`) across processing pipelines: Gargantua, Atriox, Textract, manual review, and direct insertion (SAMAI). It's one file, one query, one chart — there is no app package, no routing, no tests, no lint config.

## Running it

```bash
pip install -r requirements.txt
streamlit run app.py
```

Needs a `.env` (gitignored) with `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` (default `1521`), `DB_SERVICE` for the Oracle connection.

### Standalone deployment (Docker Compose)

`compose.yml` runs two containers: `ssh-tunnel` opens an autossh tunnel to reach the Oracle host, and `dashboard` (built from the root `Dockerfile`) waits on the tunnel's healthcheck before starting. This is for environments where Oracle isn't directly reachable.

Requires `.env` with the DB vars above plus the tunnel vars: `SSH_HOST`, `SSH_PORT`, `SSH_USER`, `DB_REMOTE_HOST`, `DB_REMOTE_PORT`, `DB_LOCAL_PORT` — and `deploy/ssh/id_rsa`, the private key for the SSH bastion (gitignored, never commit it).

```bash
docker compose up -d
```

## Architecture notes

- `load_data()` is the only DB access point. It runs one query (`QUERY`) that UNIONs `TEXTRACT_PDF`/`TEXTRACT_PDF_BKP` to determine which Oracle IDs went through Textract, then classifies every `TORRE_ARCHIVOS_AWS` row into exactly one pipeline category via a `CASE` expression, and finally pivots into one row per day with a count column per category. Streamlit's `@st.cache_data(ttl=300)` caches results per `(desde, hasta_exclusiva)` argument pair for 5 minutes — if you change the query logic, keep it cache-key-compatible (i.e. keep taking `date` args) or the cache stops matching correctly.
- The date range picked in the UI is inclusive (`desde`..`hasta`), but the query's upper bound is exclusive, so `hasta + timedelta(days=1)` is passed to `load_data` — don't remove that offset or the last day drops out.
- `CATEGORIES` and `COLORS` are parallel, fixed-order lists driving both the metric tiles and the `st.area_chart` series/colors. Keep them in sync (same length, same order) if you add or rename a category — the palette is validated for colorblind accessibility, so don't reorder or recycle colors casually.
- The `CASE` classification order in `QUERY` encodes real business precedence between pipelines (e.g. a manual reviewer overrides everything else). If you touch it, preserve the precedence, not just the individual conditions.
