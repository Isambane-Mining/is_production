# Hourly Productivity Summary — LAB validation, 13 October 2026

Implementation is present in the LAB app checkout. It has **not been registered in the site database, migrated, or deployed**. Production has not been accessed or modified. Existing report source files and production documents are unchanged.

## Behaviour

- Route after registration: `/app/hourly-productivity-summary`.
- Site, calendar-today shift-start date, Day/Night filters. Night ends at 06:00 the following calendar date.
- Latest permitted non-cancelled MPP covering the shift-start date supplies its saved `num_excavators`: target is count × 55, 220 (ten hours), 165 = count × 2,420. Breakdown never reduces target.
- Distinct MPP truck-assigned excavators identify production teams; unpaired excavators appear separately. MPP's active-empty production identity is stored only in browser localStorage. A saved-count mismatch is flagged, unpaired identities remain unresolved, and availability attribution is disabled. No roster is invented.
- Sum stored Truck Loads BCM, including spare output; dozer output is excluded. Spare output is displayed separately and does not add target.
- Call the existing Engineering ADT builder for minute-weighted availability, unique utilised ADTs, detail status, and corrected first/last recorded times. Calendar-bound source loading replaces the old ADT loader's single-date assumption for this page only.
- An absent record displays zero with an explicit unverified/missing label. Current/future hours remain neutral and are excluded from the completed-hour pie.
- Pie achieved slice is capped at completed-hour target. PBM-backed estimate integrates excavator downtime OR the fraction of assigned trucks down, avoiding overlap. Equal truck contributions and uniform downtime across productive minutes are explicit assumptions. Estimate is capped by team deficit, hourly site deficit, and shift deficit. Missing records and ambiguous rosters cannot support attribution; remaining shortfall is unattributed. This is capacity exposure, not proven causation.
- Thresholds default to 100% green and 80% orange in the new Hourly Productivity Settings singleton; System Manager may edit. Other page users can read settings.
- Permission-filtered parent lists, document checks and field-level read/masking rules apply. Restricted calculation fields fail closed. Children come from authorised parent documents; no unrestricted SQL loader is exposed.
- Historical ADT fleet membership uses current Asset locations, matching the existing ADT calculation. The page explicitly states that transfers are not reconstructed.

## Checks completed

- Relevant Python suites: **74 tests, 73 passed; one existing live-site integration class skipped** because `PRODUCTION_SUMMARY_TEST_SITES` is not configured. New page, reused ADT, Hourly Dashboard, and production-summary suites included.
- JavaScript suites: **16 passed**, including new rendering, existing Hourly Dashboard and MPP tests.
- Headless Chromium local fixture test: 12 hourly rows, accessible pie, missing-record label, expandable explanation, no page overflow at 390/768/1280 pixels, stale response rejection, access-error clearing. This uses Frappe UI boundary doubles, not a live logged-in site.
- Python syntax, JSON metadata, JavaScript syntax, and `git diff --check` passed.
- `bench build --app is_production` completed using Node 24.18.0. Its incidental tracked node_modules symlink change was restored.
- Corrected an existing Hourly Dashboard SQLite test fixture to bind both calendar dates and place overnight loads on the following date; report behaviour was not changed.
- Preview: `/tmp/hps-preview-390.png`, `/tmp/hps-preview-768.png`, `/tmp/hps-preview-1280.png`. These contain synthetic data.

## LAB connectivity blocker

Standard bench command:

```sh
bench --site juan.isambane.co.za execute frappe.db.sql --args '["SELECT 1 AS connection_ok"]'
```

Exact error:

```text
MySQLdb.OperationalError: (1045, "Access denied for user 'juan'@'localhost' (using password: YES)")
```

Effective Frappe configuration: MariaDB, host `127.0.0.1`, port `3306`, database/user `juan`; password configured; no `FRAPPE_DB_*` environment overrides. The standard bench path and direct configured connection both reach MariaDB and fail authentication. An outside-sandbox check has the same result. No passwords were printed or credentials/permissions changed. The LAB HTTP endpoint currently returns 500 too; its root cause has not been confirmed independently.

Recommended safe resolution: the LAB owner/DBA should verify that `127.0.0.1:3306` is the intended LAB instance and inspect the existing `juan` account's host match and authentication plugin using authorised administration access. Compare site credentials with the known-good LAB configuration privately. If configuration drift is confirmed, restore the approved known-good configuration. Do not reset credentials or change grants speculatively. Repeat the read-only bench `SELECT 1` before any schema registration.

## Still unvalidated

New DocType/Page registration, real MPP roster coverage and BCM parity, permission behaviour for actual LAB users, and the logged-in Desk route. No migration or deployment should run until LAB connectivity is restored.

After connectivity is restored and LAB-only registration is authorised, reload the new DocType and Page, then validate real day/night samples, role/site restrictions, threshold persistence and the actual Desk page. A full historical recovery or production deployment is outside this task.
