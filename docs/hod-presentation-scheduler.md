# HOD Presentation scheduler

Configure recipients, sites, Run As user and delivery time in
HOD Presentation Schedule. New schedules are disabled by default.

Defaults: daily 06:00 Africa/Johannesburg; planning-period start through
previous Sunday; Average Per Machine; Include Swing/Spare;
All Assets; 85% A & U. Sunday uses the preceding Sunday.

## Deployment
Install app dependencies and, as the Unix user running the workers:
    env/bin/python -m playwright install chromium

Migrate the target site and restart its web and worker processes.
Verify scheduler and long-queue workers are running.

Use an enabled System User with HOD permissions. Delivery Frappe Site
must match the target site exactly. Verify its default outgoing Email
Account and current production plans for every selected mining site.

Create the LIVE schedule disabled. Generate a preview with:
    bench --site isambane.co.za execute is_production.production.controllers.hod_presentation_scheduler.run_now --kwargs '{"schedule_name":"Daily HOD","send":0}'

After checking the preview, send one test using send=1.
Each manual send=1 invocation requests a separate email.
Verify receipt before enabling daily delivery.

Email Queued means queued, not confirmed delivered.
Review failed or interrupted deliveries before manually retrying.
Keep LAB email muted and its schedule disabled.

## Tests
    env/bin/python -m unittest discover -s apps/is_production/tests/hod_scheduler -v
