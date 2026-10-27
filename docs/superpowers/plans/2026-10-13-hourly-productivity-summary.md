# Hourly Productivity Summary Implementation Plan

**Goal:** Add a compact read-only productivity page in LAB, as approved in chat on 13 October 2026.

**Architecture:** A permission-aware loader supplies a pure calculation function. Reuse Engineering ADT Hourly Productivity's calculation function, never its unrestricted SQL loader. Stored BCM is summed unchanged. A separate singleton configures colour thresholds; existing reports and production documents are untouched.

**Tech stack:** Frappe Python, Desk JavaScript, CSS pie chart, unittest.

## Approved calculation rules

- Target count is the covering MPP's saved Number of Production Excavators. Unique persisted excavator/truck pairs identify teams; unpaired excavators are spare unless the saved count indicates unresolved production identities. MPP's empty-production marker is browser-local and cannot be recovered server-side. Expose this limitation rather than infer a roster.
- Every planned excavator receives 55, ten times 220, and 165 BCM: 2,420 per shift. PBM never reduces targets. All recorded excavator BCM, including spare output, contributes to actual; spare output is also listed separately. Dozer BCM is excluded.
- Calendar date plus hour slot determines source datetime; the selected date is the shift start date. Missing hours display 0 with a missing-record indicator, future hours are pending, and current hours are provisional.
- Existing minute-weighted ADT availability, distinct utilisation, PBM exclusions, interval merging, and recorded first/last setup time correction order are reused.
- Pie uses completed hours. Achieved is capped at target; remaining shortfall includes missing-record deficits but is explicitly unattributed. PBM-supported availability estimate is calculated only in completed hours with records and an unambiguous roster. For each team, integrate excavator-down or assigned-ADT-down fraction, counting their overlap once. Multiply by that hour's fixed target (uniform productive-time approximation), cap by that team's recorded deficit and then by the site's hourly deficit. No PBM means no availability attribution. Targets remain fixed. This estimates capacity exposure, not proven causation.
- Colour defaults: green >=100%, orange >=80%, red <80%; configurable singleton validates 0 <= orange < green.

## Tasks

1. Write failing calculation tests: target 4,840 for two planned excavators; spare BCM without spare targets; zero/missing distinction; night boundary exclusion; overlapping PBM cap; no evidence/no attribution; roster mismatch; future exclusion; thresholds.
2. Implement pure report builder alongside new page, calling existing ADT builder with bounded source rows.
3. Write loader tests for denied access, calendar-window loading, latest permitted covering MPP, child parent scoping, and PBM filtering. Implement read-only endpoint with document permissions and paginated get_list, plus settings and Page metadata.
4. Implement compact responsive UI: filters, KPI cards, pie and explanatory legend, hourly table, first/last times and spare summary; escape labels and suppress stale responses.
5. Run new and reused calculation suites, Python/JS/JSON validation and rendered mobile checks. Register only the new Page/DocType in LAB if access permits; verify route and real data without touching source records. Record authentication blockers accurately.

## Review focus

- Persisted count/identity mismatch disables breakdown attribution and flags unidentified production teams.
- Inaccessible inputs fail closed rather than silently lower target or infer full availability.
- Missing records never count as verified zero or support availability attribution.
- Midnight hours use the next calendar date; wrong-date slots are excluded.
- Total pie slices equal completed-hour target; overlaps, spare output, and overproduction cannot double-count shortfalls.

## Execution ledger

- Approved for direct implementation in LAB; no further approval gate required.
- Local is_production checkout clean before changes, branch juan-production-summaries; Engineering has unrelated modifications and is read-only for this task.
- LAB database authentication fails even outside the sandbox; do not alter credentials or permissions to bypass it.
- Tasks 1–4 complete; task 5 local checks complete. Live LAB schema/data/route validation is blocked by error 1045 and explicitly held by the user. See `docs/hourly-productivity-summary-validation.md`.
- Fixed the existing Hourly Dashboard test's stale two-date SQLite fixture after reproducing its failure. No report source or behaviour changed.
