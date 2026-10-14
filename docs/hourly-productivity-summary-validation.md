# Hourly Productivity Summary — LAB redesign validation, 14 October 2026

The existing Summary page has been redesigned in the LAB checkout on `juan-hourly-productivity-summary`. No commit, push, migration, deployment, credentials change or production access was performed. MPP, Excavator Truck Link and the separate Analysis page source files are unchanged by this redesign.

## Changes and calculation definitions

- Filters: Site, From Date, To Date, Day/Night/Full Day. A production day runs from 06:00 to the following 06:00. Maximum range is 366 dates. Navigation changes selected-day details without refetching or changing range KPIs/chart. Refresh preserves the selected day when it is still in the result.
- `hourly_productivity_summary.py` contains the range dashboard and composes the existing `build_summary`, `load_sources`, latest-covering-MPP selector and Engineering ADT builder. It buckets source documents once by operational date, retaining the original parent/child deduplication. Stored Truck Loads BCM is summed; dozer production is excluded. All recorded excavator output remains in Actual BCM.
- Saved MPP `num_excavators` sets targets. Each standard planned shift uses 55 + 10 × 220 + 165 = 2,420 BCM per excavator. Full Day adds the two shifts. PBM never reduces the target. The saved child `production_excavators` daily count is not substituted for the requested parent count.
- Saved MPP `month_prod_days` rows supply each date's Day/Night required hours. An unambiguous saved zero-hour shift has zero target and the not-planned category. A saved 12-hour shift uses the fixed target profile. No weekday/weekend defaults are regenerated or inferred.
- **Reduced hours remain unclassified by user approval.** MPP stores shift-hour totals, not start/end windows for a shortened shift. Missing/duplicate date rows, missing required-hour values, non-12-hour positive schedules and incompatible 3×8-hour plans cannot safely locate hourly targets. Their targets/achievement are unavailable rather than guessed. Startup/fatigue configuration and captured production times do not prove planned work windows.
- Achievement keeps the completed-hour formula: recorded completed BCM / completed target BCM. Completed working hours without records still contribute their fixed target to this KPI; their displayed BCM is an unverified zero. Future/current targets do not contribute. If a completed target cannot be established, combined achievement is unavailable. All-range Actual BCM can include current recorded output; it is distinct from the completed numerator.
- Availability and utilisation cards show **separately labelled hourly averages**, over the same completed, recorded, planned working hours. Available machines use PBM minutes / 60; utilisation counts distinct machines with at least one recorded load in each hour, then averages those counts. Missing records are excluded from these averages; they do not prove zero utilisation. No observations gives `—`. Any unidentified complete production roster gives `—` for the excavator averages rather than a partial-roster average.
- Complete production excavator identities are verified only when persisted truck-assigned teams match the saved MPP count and assignments are unambiguous. Unpaired/otherwise captured machines remain unverified and cannot alter targets. A small status replaces the former technical panels; no machine is guessed to be spare. Identified teams can still show their own PBM status when the overall roster is incomplete.
- Five chart categories count **hours**, not BCM: green ≥100%, amber ≥85% and <100%, light red <85%, bright red recorded zero with known positive production excavator availability, grey explicitly zero required work hours. Bright red requires supporting production availability; unknown rosters cannot prove it. Recorded zero with no available excavators uses ordinary red.
- Chart denominator is the sum of these five completed, classifiable categories. Completed explicit non-working hours are included. Missing working records, unknown schedules/targets and future/incomplete periods are excluded and counted in a compact status line. Future planned non-working hours remain visible in grey in the table but do not enter completed percentages. A grey hour expresses the plan, even if actual production was recorded there; that BCM remains in Actual.
- ADT timeline cells reuse Engineering available minutes and recorded loads. Colour precedence: missing/unfinished, zero available (PBM unavailable), partial availability, full available with loads, full available without loads. Missing records display `—` loads; PBM-based minutes may still be shown. Timeline cells are selected-day only; names remain sticky during horizontal scrolling.
- First Load and Last Load retain the existing corrected helper: earliest valid captured start and latest valid captured end, including restarts and after-midnight events. Missing values display `—`. Full Day displays separate Day/Night excavator rows.
- Startup/fatigue indicators read the existing **Startup and Fatigue spesification** DocType. Exact site/shift/day-type matches take priority; the Engineering legacy fallback is used only when one site/shift specification exists. Times are anchored across midnight and clipped to the shift. They are labels only and do not add target exclusions. Users without read access see no indicators; no permission is bypassed.
- Permission-filtered parent loading, document permissions and field-level permissions are preserved. Masked MPP schedule or specification calculation fields fail closed. No schema changes or writes were added.
- Historical fleet membership still follows the reused current Asset location logic; transfers cannot be reconstructed from these sources. This limitation is documented here, without a technical dashboard paragraph.
- The dashboard no longer requests loss estimates. The legacy single-shift endpoint and helper remain compatible for existing consumers; the separate Analysis page retains its existing behaviour.

## Validation

- Python unit regression run: **138 tests; 137 passed, one existing live-site integration test skipped** (`PRODUCTION_SUMMARY_TEST_SITES` unset). Includes new range/schedule/permission tests, existing Summary/Analysis, Engineering ADT, Hourly Dashboard and production-summary tests.
- JavaScript regression run: **21 passed**, including Summary rendering/navigation, Analysis, Hourly Dashboard and existing MPP tests.
- Headless Chromium fixture validation: Full Day 24 rows, five KPI cards, five-category chart, captured timings, selected-day ADT timeline, bounded navigation without refetch, refresh preserving date, stale-response rejection, invalid-range rejection and access-error clearing. Responsive checks at **390 / 768 / 1280px** pass with horizontal table/timeline scrolling and sticky ADT names.
- Browser previews use synthetic data and Frappe UI boundary doubles: `/tmp/hps-preview-390.png`, `/tmp/hps-preview-768.png`, `/tmp/hps-preview-1280.png`. They are not screenshots of a logged-in LAB site.
- Python/JavaScript syntax checks and `git diff --check` pass. No live-site asset build, cache reload, migration or deployment was performed.

Reproduce fixture generation without a database:

```sh
/home/juan/juan-bench/env/bin/python -c 'import json; from pathlib import Path; from is_production.production.page.hourly_productivity_summary.test_hourly_productivity_summary import browser_fixture; Path("/tmp/hps-dashboard.json").write_text(json.dumps(browser_fixture()))'
```

Run the browser test with the installed Playwright core and Chromium paths supplied through `HPS_PLAYWRIGHT`, `HPS_CHROMIUM` and the generated `HPS_FIXTURE`. The script is `test_hourly_productivity_browser.cjs` in the Summary page directory.

## LAB connectivity blocker and remaining checks

The read-only standard bench connection was checked again:

```sh
bench --site juan.isambane.co.za execute frappe.db.sql --args '["SELECT 1 AS connection_ok"]'
```

Exact error remains:

```text
MySQLdb.OperationalError: (1045, "Access denied for user 'juan'@'localhost' (using password: YES)")
```

No secrets were printed, and credentials/configuration/permissions were not changed. The authorised administrator should verify the intended MariaDB instance, account host matching and authentication plugin, then privately compare the existing site credentials with approved LAB configuration. Repeat the read-only connection test before migration/deployment.

Still unvalidated: actual saved LAB MPP schedule coverage, real production/PBM parity, live user permissions, saved startup/fatigue records and the logged-in Desk route. These require restored LAB connectivity. Reduced schedules and incomplete saved excavator identities remain explicit data-model limitations; this task introduces no fields or guesses to resolve them.


## Combined-page structural refactor, 14 October 2026

Canonical Page: `/app/hourly-productivity`, Summary first/default and Analysis second. Old Page JSON/roles remain; old Page scripts redirect internally with a tab selection, including cached route revisits. Both report UI classes and the host live in the single standard `production/page/hourly_productivity/hourly_productivity.js` file. No extra JavaScript runtime assets are required. All report styles are scoped in the existing `public/css/dashboards.css` Desk asset; only data-dependent pie stops are supplied as CSS property values.

Each report mounts once. The second report copies Site/From/To only on first opening after the entry report finishes initialising its filters (without waiting for its data request); subsequent tab switches retain controls, cached DOM and Summary date navigation without another data request. The Analysis investigation button selects the cached Summary tab.

Range calculations moved unchanged into the standard Summary Page Python module. The former `summary_dashboard.py` shim is removed following explicit approval to retire its direct Python import path. Its legacy HTTP method is preserved through `override_whitelisted_methods`, pointing to the existing standard Summary Page `get_dashboard` function. Backend calculations are unchanged. Test cases and browser-fixture generation moved into the standard Summary test module without removing coverage.

Synthetic baselines: `/tmp/hp-structure-baselines`. Visual comparisons align report viewport origin (excluding the new tab strip) and match the baseline's button interaction state. Report dimensions remain unchanged at 390/768/1280px. Summary captures are byte-identical; Analysis comparisons have no perceptual differences, with minor antialias rasterisation differences. Full combined-page screenshots retain the tab strip. Live Page registration, real routes, permissions and asset delivery still require restored LAB connectivity and separately authorised registration/deployment; none was performed here.


## Eben structure consolidation — 14 October 2026

Removed the four additional runtime files: `summary_dashboard.py`, `hourly_productivity.bundle.js`, `public/js/hourly_productivity_summary.js` and `public/js/hourly_productivity_analysis.js`. The two report IIFEs moved into the canonical Page JavaScript, retaining separate scopes and their existing controller implementations. Legacy Page JS/JSON files remain for bookmarked URLs; their standard Python modules remain for existing endpoints. Each Page has one standard JS, one JSON and at most one standard Page Python module, alongside normal package `__init__.py` and excluded test files.

The old Python import path is intentionally retired by approval. The old HTTP method maps to `is_production.production.page.hourly_productivity_summary.hourly_productivity_summary.get_dashboard` through the app's standard Frappe hook. Compatibility tests verify hook resolution before module import, unchanged callable/whitelist identity and absence of the retired import path. Frappe v1 and v2 method dispatchers both apply this override before resolving the function. Live HTTP invocation remains blocked by LAB database authentication.

Checks: 145 Python tests run, 144 passed and one existing live-site integration test skipped; 30 JavaScript tests passed; all three browser suites passed, including six visual comparisons at 390/768/1280px. Both report controller source strings and rendered fixture HTML are identical to their pre-consolidation versions; the Summary calculation JSON is byte-identical. Production Page Python modules, stylesheet, MPP and Excavator Truck Link have no diff. No tests were removed; only the explicitly retired import-compatibility contract was replaced with HTTP-alias and import-retirement checks. No migration, deployment, commit or push was performed.
