# HOD Scheduler Simplification Implementation Plan

Goal: reuse the approved child DocTypes and leave automatic HOD delivery disabled.

Architecture: one optional minute hook dispatches due configurations to one long-queue
worker. Retain the existing delivery log and one-attempt-per-slot claim; remove the
reconciliation/recovery scheduler. Manual previews and explicit test sends remain.

- [x] Test shared recipient User lookup, row/user enablement, and location field mapping.
- [x] Retarget schedule tables to Production Notification Recipient and WearCheck Users;
  populate each location row's required User from Run As; preserve LAB rows in a
  pre-model-sync migration before removing the two redundant child DocTypes.
- [x] Remove reconciliation, queue each newly claimed slot once, record enqueue failure.
- [x] Reject expired scheduled workers and recheck configuration with a current locking
  read immediately before email creation; retain manual send behavior.
- [x] Comment the HOD hook inside the existing scheduler_events cron mapping, preserving
  all other hooks. Add the Engineering dependency required by WearCheck Users.
- [x] Run focused tests and independent review; commit only to the existing PR branch.

Constraints: no changes to Engine calculations, dashboard rendering, existing shared
child DocTypes, other schedulers, LIVE site configuration, or outgoing email setup.
Automatic HOD runs are inactive after migration synchronizes the commented-out hook.

Verification limitation: real MariaDB migration and concurrent-connection tests require
LAB; local mocked Frappe tests cannot substitute for those runtime checks.
