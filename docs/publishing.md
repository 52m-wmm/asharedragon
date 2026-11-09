# Publication boundary

This public repository contains source code, dependency manifests, migrations, tests, methodological documentation and aggregate research summaries. It does not grant redistribution rights to provider or purchased data.

## Kept outside GitHub

- `.env`, tokens, passwords, private keys and authenticated proxy settings.
- Purchased and provider market data, Parquet partitions, archives and raw API responses.
- DuckDB/PostgreSQL databases, backups, exports and materialized research snapshots.
- Stock-level study CSV/JSON outputs, raw evidence and unlicensed research datasets.
- Virtual environments, dependency directories, caches, logs and temporary diagnostics.
- Prior research screenshots containing market records.

Generated study folders under `docs/` are local-only. Public Markdown retains methods and aggregate conclusions; links to excluded outputs are labelled local-only rather than presented as downloadable public datasets. Private original report copies remain in the ignored `data/release_document_originals/` directory.

The sole published screenshot, `docs/screenshots/homepage.png`, is an actual homepage showing aggregate counts, coverage and streak-height trends, published at the project owner’s request. It contains no stock-level records, price candles, raw responses or credentials. README explicitly states this context.

`.env.example` contains blank optional credentials and a password-free localhost database URL. `.gitignore` only protects untracked files: it neither removes tracked content nor erases history. Release checks must inspect the Git index and historical blobs, not merely the working directory. Stop publication if an actual credential or prohibited dataset is found; do not silently rewrite shared history.

No licensing claim is made for third-party data. Free API access is not permission to redistribute it. Do not run collection or reconstruction scripts solely to populate a public demonstration.
