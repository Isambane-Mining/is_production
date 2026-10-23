# Hourly Productivity Analysis and excavator availability

Implemented locally on `juan-hourly-productivity-summary`. No database writes, migration, deployment, or push was performed. Live LAB validation remains held while MariaDB authentication is unresolved.

## Changes

- Existing `/app/hourly-productivity-summary` filters remain. Its hourly table adds production excavators available, utilised, and unavailable due to PBM. Availability/unavailability are minute-weighted; a separate affected count reports excavators with any PBM overlap. Unpaired excavator metrics are shown separately and explicitly remain production/spare unknown.
- New `/app/hourly-productivity-analysis` has Site/From Date/To Date, operational-date rows, 24 hours from 06:00 to next-day 06:00, grouped shift headers, frozen date/day/month columns, month dividers, horizontal scrolling, and a five-category hour-count pie below the table.
- Shared permission-aware loading fetches source documents once for the selected range, including the final following morning. Every parent passes document/field permissions; the endpoint does not call the unrestricted ADT SQL loader.
- The saved MPP production count controls fixed 55/220/165 targets. Unique excavator/truck pairs identify known production teams. MPP JavaScript/Python/tests and Excavator Truck Link have been restored byte-for-byte to the committed pre-development versions; no field was added or database data changed. The pages do not read or require the reverted field.
- Both pages sum stored Truck Loads BCM, excluding dozers. All recorded excavator output contributes to actual; no additional targets are inferred from unpaired identities. Repeated parent/child identities are counted once. Distinct persisted source records are summed, matching existing production reporting; they are not silently discarded as competing versions.
- PBM interval union and availability weighting reuse Engineering ADT Hourly Productivity's overlap helper. No changes were made to Engineering in this task.

## Calculation decisions and limitations

- The latest modified permitted non-cancelled MPP covering each **operational date** is selected; name breaks ties and draft plans remain applicable. A Night shift's after-midnight hours use its starting operational date's MPP, even across a month boundary.
- Original MPP `getActiveEmptyExcavators()` reads `mpp_<plan-name>_active_empty_excavators` from browser localStorage. `update_equipment_counts()` saves the count of distinct paired excavators plus these browser-selected identities as `num_excavators`. The selected identities are not stored in Excavator Truck Link, so an unpaired saved row cannot reliably be classified as production or spare. Both pages explain this limitation, leave unpaired identities unknown, and do not read localStorage to invent a server-side roster.
- Missing/unknown MPP is an explicit error in Analysis, never grey zero deployment. Saved count/paired-identity mismatches are flagged; total production availability is unknown and PBM attribution is disabled. Analysis cannot assign bright red without known production availability. When paired identities match the saved count, availability describes that saved truck-assigned roster only.
- Analysis uses 100% green and 85% amber, independent of Summary's configurable thresholds (default 100/80).
- Positive target with zero BCM is bright red only for a completed hour with known planned excavator availability after PBM. Availability here means the existing PBM-derived excavator model, not proof of production readiness or actual work. Positive target with zero or unknown availability is ordinary red. A saved zero MPP target is grey, without classifying any unpaired identities as spare.
- Both missing records and recorded zero show 0, but `has_record` and `record_status` remain distinct; missing cells carry `*`. Future/current cells carry `†`, do not receive bright-red readiness claims, and are warned about.
- Pie denominator is **every requested date × 24 cells**, including missing/unfinished cells, as specified. Each percentage is raw category count / denominator × 100; displayed percentages are rounded. This is a distribution of hours, not BCM.
- Default range runs from month start through the last fully completed operational day. Ranges are bounded to 366 days to limit endpoint work.
- Current Asset site membership supplies the ADT fleet as before; historical transfers are not reconstructed. Planned excavator identities come from the relevant MPP.
- No Excel screenshots were available in this conversation; layout follows the written specification. Exact screenshot comparison remains outstanding.

## Local validation

Final results after restoring MPP: all detected local Python unit suites plus the reused ADT suite ran 109 tests: 108 passed and one existing live integration class was skipped. All 19 local JavaScript unit tests passed, including the original restored MPP tests. Python/JavaScript syntax, Page JSON and `git diff --check` passed. No changes were committed or pushed by this task.

Python unit suites cover fixed saved-count targets, explicit unknown classification for unpaired production/spare identities, PBM overlaps, unique utilisation, repeated source identities, BCM/dozer separation, midnight/month boundaries, MPP selection, missing/zero distinction, categories, percentages, range/default boundaries and permissions. Existing production-summary and Hourly Dashboard suites are included. Live Frappe integration tests require restored LAB connectivity.

JavaScript tests cover rendering, separate spare/production metrics, grouping, month dividers, missing markers, escaped source values and pie legends. Headless Chromium runs use synthetic data and Frappe UI boundary doubles: 390/768/1280px, horizontal scrolling/frozen columns, mobile shift labels, stale responses, invalid range and access-error clearing.

Preview files: `/tmp/hpa-preview-390.png`, `/tmp/hpa-preview-1280.png`, `/tmp/hps-preview-390.png`. These are synthetic-data previews, not live LAB screenshots.

After DB connectivity is restored, register the new Page in LAB only and test live day/night samples, multi-month saved counts, stored BCM parity and actual user permissions. No MPP schema changes are required or authorised by these pages. No production deployment is authorised.

Separate existing Engineering ADT SQL loader code still references the reverted field. This task does not modify that app; both new pages reuse its pure overlap/build helpers, not its SQL loader. Review that separate loader's compatibility before any deployment.
