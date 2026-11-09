# Stage release validation

Release scope: documentation and publication hygiene, existing source/tests/UI, a data-free homepage screenshot, Git commit and normal push. No research algorithm or original market data was changed, no snapshot was rebuilt, and no collector or paid API was called for this release.

## Actual checks

- `git status --short`, `git diff --check`, `git diff --stat`, `git remote -v`, and `git ls-files` inspected before staging.
- Branch: `main`; remote: `https://github.com/52m-wmm/asharedragon.git`.
- Fetched remote history before publication; no incoming commits at the initial check.
- Reviewed both existing commits: 70 unique historical blobs across 69 paths. Pattern scans and comparisons against actual local secret values found no actual credentials. An additional scan covered all 497 local Git blobs present at that check, including unreachable objects. All four generic token matches were versions of the explicit `fixture-private-token` test fixture; none matched an actual local secret. No history rewrite was performed. `frontend/src/app/data/page.tsx` is source code, not a data archive.
- Reviewed prospective source and Markdown files; removed personal absolute paths from public documents and generalized the local browser audit output path. No business logic changed during release preparation.
- Generated research artifacts, stock-level evidence exports and prior market-data screenshots are excluded. Public reports retain methods, aggregate results and limitations. See [publication policy](publishing.md).
- The index was separately checked for prohibited file extensions, actual credentials and ignored-but-tracked content. `.gitignore` is not treated as a history-cleaning mechanism.

These checks are a scoped publication review, not a guarantee that a pattern scanner detects every possible secret. A real finding would stop the push; shared Git history is preserved.

## Tests run on the existing local environment

| Check | Result |
|---|---|
| `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests scripts -q --disable-warnings` | **294 passed**, 9 existing dependency deprecation warnings, 9.25 seconds |
| `npm run typecheck --prefix frontend` | Passed (`tsc --noEmit`) |
| `NEXT_PUBLIC_API_URL=http://127.0.0.1:8001 npm run build --prefix frontend` | Passed; TypeScript and all 8 static routes generated |
| Live API smoke checks against the existing local service | **7 passed**, all HTTP 200 |
| `git diff --check` / staged whitespace review | Passed |

API checks: health; 2026 ≥5-board rankings; 2017 ≥10-board IPO-excluded list (4 remaining observations); non-IPO ≥5 study (1,740 segments / 1,633 trusted starts); Shenglong 14-board September 2023 case; Aiai 13-board March 2024 case; unified research summary. All were read-only requests, not data collection.

These tests used the existing PostgreSQL installation and authorized local snapshots. They are not evidence that a fresh clone contains the reference data. Some tests skip without local artifacts; older audit scripts require local inputs. No private test output payload is published.

## Screenshot

The initial release embedded `docs/screenshots/homepage.png`, captured from the actual homepage at 1440×900 with December 2026 selected (outside the current snapshot). The selected list had zero stock rows and no daily metrics; there are no market-price records or populated market plots in the image. It was visually inspected before staging. This is an empty-month UI screenshot, not simulated market performance.

## Reproducibility and publication limits

Source, migrations, tests and methodological summaries are shipped. Local `.env`, provider credentials, raw responses, licensed archives, Parquet/DuckDB files, PostgreSQL contents, generated row-level studies, cached images and execution logs are not shipped. Aggregate findings do not establish full-market independent verification or validated seasonality.

The release commit hash and final remote verification are reported after the commit/push; this document does not claim success in advance.

## Screenshot follow-up

At the project owner’s request, the initial empty-month image was replaced with a 1600×1250 capture of the actual homepage showing aggregate counts, coverage and streak-height trends. Browser zoom was adjusted for framing; data and application code were unchanged. The image was visually inspected and contains no stock-level records, price candles, raw responses or credentials. Only the screenshot and its documentation changed; no business logic tests needed to be repeated.
