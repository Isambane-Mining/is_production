# Diesel Cap Implementation Plan

**Goal:** Create the requested Diesel Cap Desk page using the existing Diesel Cap Report as its source of truth.
**Architecture:** Call the permission-checked Frappe report runner; render its columns and rows with Frappe DataTable. Keep existing report files unchanged. Use native Date/Location controls and page-scoped CSS.
**Design:** Navy hero, white filter card, five KPI cards, scrollable report table, comparison help and backend execution time. Match report defaults. Cap cards use L/BCM and L/m³; multi-site summaries divide returned diesel totals by returned volume totals, never sum ratios. Keep all 11 report columns including survey datetime. Add a workspace link alongside the report.

- [x] Add regression tests for weighted summaries, zero volumes, report request wiring, errors, and overlapping requests.
- [x] Implement the standard Page JSON, controller, scoped CSS and workspace link.
- [x] Run Node checks, browser rendering checks, real report comparison, page sync/cache clear and app build.

Review focus: zero denominators; stale requests after filter changes; loading/error state clearing old data; preserving field precision and datetime; server-side report permissions.

Validation: 4 Node regression tests passed; browser integration passed with real Frappe DataTable and 9 live report rows; Page JavaScript and CSS loaded through Frappe; app build passed; cache cleared. No report source files changed.
