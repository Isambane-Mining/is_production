# HOD Presentation scheduler

Automatic HOD delivery is paused: its only cron entry is commented out in
hooks.py. Other applications' existing schedules are unchanged. New HOD
schedule records also default to disabled.

Configure recipients, sites, Run As user and delivery time in HOD Presentation
Schedule. Recipients reuse Production Notification Recipient (select Users);
locations reuse Engineering's WearCheck Users. The required User on each
location row follows Run As automatically. Real sends resolve email directly
from User and skip disabled rows/accounts. Enabled reflects User account status
and is read-only on this form; remove a recipient row to exclude that User.
All recipients receive only the
schedule's selected locations; receive_all_sites does not add other locations.

Defaults: daily 06:00 Africa/Johannesburg; planning-period start through
previous Sunday; Average Per Machine; Include Swing/Spare;
All Assets; 85% A & U. Sunday uses the preceding Sunday.

## Deployment
Engineering must be installed before this app is installed/migrated because
it owns WearCheck Users. Install app dependencies and, as the worker Unix user:
    env/bin/python -m playwright install chromium

Migrate the target site and restart its web and worker processes. Migration
preserves pre-release LAB recipient/site rows using the shared child tables
and disables those migrated schedules. Missing or ambiguous recipient Users
stop migration for correction; records are never silently dropped. Legacy SQL
tables remain as backup. No new recipient or location child DocTypes are added.
Migration also synchronizes jobs and removes the old uncommented HOD hook.

Use an enabled System User with HOD permissions. Delivery Frappe Site
must match the target site exactly. Verify its default outgoing Email
Account and current production plans for every selected mining site.

Create the LIVE schedule disabled. Generate a preview with:
    bench --site isambane.co.za execute is_production.production.controllers.hod_presentation_scheduler.run_now --kwargs '{"schedule_name":"Daily HOD","send":0}'

After checking the preview, send one test using send=1.
Each manual send=1 invocation requests a separate email.
Verify receipt before enabling daily delivery. Automatic delivery additionally
requires uncommenting the single HOD cron entry and migrating the site to sync
jobs. Leave it commented for now. Do not create another Scheduled Job Type.

There is one due-schedule dispatcher and one long-queue send worker, not two
schedulers. There is no reconciliation/recovery loop or automatic resend.
Each schedule/time slot has one delivery claim. Enqueue errors are marked
Failed; worker timeouts may leave Generating and need manual review of RQ and
Email Queue. Review uncertain deliveries before explicitly sending again.
Scheduled workers starting 15 minutes late are rejected before rendering.
Configuration is checked with a current locking read immediately before the
email is queued; edits/disablement during capture abort the send.

Email Queued means queued, not confirmed delivered.
Review failed or interrupted deliveries before manually retrying.
Keep LAB email muted and its schedule disabled.

## Tests
    env/bin/python -m unittest discover -s apps/is_production/tests/hod_scheduler -v

    node --test apps/is_production/tests/hod_scheduler/test_form.cjs
