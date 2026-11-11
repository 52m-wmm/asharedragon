# Research workspace stage release

Scope: R2 release/switch/rollback tooling, E4 market aggregation, E5 dashboard, stock scope, Indicator Lab, navigation structure, regression tests and supporting methodological documentation. No dataset or active pointer is part of this commit. README embeds the current real aggregate Market Overview with the three primary navigation entries.

## Publication review

- Reviewed tracked and untracked files, `.gitignore`, index exclusions, remote, current branch and diff whitespace/statistics.
- Scanned the worktree and all 307 reachable pre-release history blobs against credential patterns and actual locally configured secret values, without printing those values. No credential match or ignored-but-tracked file was found.
- No CSV, Parquet, DuckDB, database dump, raw response, purchased archive, node_modules, `.next`, logs or large intermediate snapshots is staged. The sole image is the explicitly requested aggregate homepage screenshot (~165 KB); no stock-level records or credentials appear.
- Personal absolute paths and internal Notion links in new audit documents were generalized. Inline provider/purchased stock-day price examples were removed from the public Inventory report, and the existing price-adjustment audit's purchased price examples were removed from the current document. Original reports remain locally ignored.
- The old price-adjustment examples already exist in earlier remote commits. This release does not rewrite or erase existing public history. They are small audit excerpts, not credential findings or full datasets; no new purchased raw price samples are being added.
- Configuration examples contain blank optional credentials and a password-free localhost database address. Source code uses local/user-configured snapshot paths, not another user's absolute home directory.
- In an isolated empty snapshot root, both Indicator Lab catalog and series return a generic 503. Guards verified no database connection or network connection was attempted for those requests; the temporary filesystem path is not returned. This check is scoped to these endpoints, not a claim that every research endpoint works without snapshots.
- GitHub reports a public repository, default branch main, Pages disabled, zero Actions workflows and no repository webhooks. No deployment command or tag is created.

## Validation

- Backend: `PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests -q` → **492 passed, 5 skipped**, 21 dependency warnings, 131.97 seconds.
- The Indicator Lab real-source assertion was changed to compare against the installed QQQ reader instead of publishing a literal source quote; its six tests were rerun separately: **6 passed**.
- Frontend: `node --experimental-strip-types --test tests/*.test.mjs`: 8 passed.
- `npm run typecheck`: passed.
- `npm run lint`: zero errors, 23 existing warnings; no new lint errors.
- `npm run build`: passed, all 11 existing application routes retained.
- `git diff --check`: passed; staged whitespace and file boundary checked separately before commit.

Tests use locally authorized snapshots and an existing local PostgreSQL instance. A fresh clone includes implementation, not market data; locally dependent tests may require fixture provisioning. No collector, paid API, active-switch operation or dataset reconstruction was run for publication.

## Release boundary

The repository was on main, initially one existing local commit ahead of origin/main with no incoming commits. That unpublished commit was included in the history safety review. Push uses normal fast-forward semantics only; no force push, history rewrite or version tag. The final commit identity and remote SHA verification are reported to the user rather than embedded recursively in this file.

Local-only check logs and security inventory are retained under ignored `data/github_release/`. Methodological historical reports retain their own snapshot-specific scope and should not be mistaken for current full-market verification. QQQ historical-vintage restrictions, candidate values, unknown identities, incomplete supplier events and the absence of a validated predictive model remain explicit.
