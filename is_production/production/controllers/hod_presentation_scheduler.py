"""Configurable HOD PowerPoint delivery; use run_now(name, send=0) for a private preview."""
from datetime import datetime
from html import escape
from io import BytesIO
from pathlib import Path
import uuid

import frappe
from . import hod_schedule_policy as policy

SCHEDULE = 'HOD Presentation Schedule'
DELIVERY = 'HOD Presentation Delivery'


def _now():
    from frappe.utils import now_datetime
    return now_datetime()


def _schedule(name):
    return frappe.get_doc(SCHEDULE, name)


def check_environment(doc):
    if doc.get('delivery_site') != frappe.local.site:
        raise ValueError('Delivery Frappe Site must match this Frappe site before email can be queued.')


def validate_rows(rows, expected_sites):
    actual = [row.get('site') for row in rows]
    if len(actual) != len(set(actual)) or set(actual) != set(expected_sites):
        raise ValueError('Incomplete or duplicate site dashboards; refusing a partial export.')
    if any(not isinstance(row.get('engineering'), dict) for row in rows):
        raise ValueError('A site is missing its engineering snapshot.')


def validate_schedule(doc):
    from frappe.utils import get_system_timezone
    from is_production.production.report.hod_presentation import hod_presentation as report
    if get_system_timezone() != 'Africa/Johannesburg':
        raise ValueError('HOD schedules require site timezone Africa/Johannesburg.')
    policy.sites(doc)
    policy.recipients(doc)
    policy.send_time(doc.get('send_time'))
    frequency = doc.get('frequency') or 'Daily'
    if frequency not in ('Daily', 'Weekly'):
        raise ValueError('Select Daily or Weekly.')
    if frequency == 'Weekly' and doc.get('weekday') not in policy.WEEKDAYS:
        raise ValueError('Select a weekday.')
    for field, choices in [('summary_type',report.SUMMARY_TYPES),('machine_scope',report.MACHINE_SCOPES),
            ('asset_ownership',report.ASSET_OWNERSHIP_OPTIONS),('au_target_filter',report.AU_TARGET_FILTERS)]:
        if doc.get(field) not in choices:
            raise ValueError('Invalid '+field)
    user = doc.get('run_as')
    account = frappe.db.get_value('User', user, ['enabled','user_type'], as_dict=True) if user else None
    if not account or not account.enabled or account.user_type != 'System User' or user == 'Guest':
        raise ValueError('Run As must be an enabled System User with report permissions.')
    previous = frappe.session.user
    try:
        frappe.set_user(user)
        report.check_report_access()
    finally:
        frappe.set_user(previous)


def dispatch_due():
    """Minute hook; each configuration has one unique delivery slot per scheduled day/time."""
    if not frappe.db.exists('DocType', SCHEDULE):
        return
    now = _now()
    for name in frappe.get_all(SCHEDULE, filters={'enabled':1}, pluck='name'):
        try:
            doc = _schedule(name)
            if doc.get('delivery_site') != frappe.local.site:
                continue
            slot = policy.due_slot(doc, now)
            if not slot:
                continue
            key = _claim_delivery(doc, slot, 'Scheduled')
            if key:
                frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.log_error(title='HOD schedule dispatch failed', message=frappe.get_traceback())
    reconcile_pending(now)


def reconcile_pending(now):
    """Durable outbox: retry Redis enqueue; make interrupted workers visible without resending."""
    pending = frappe.get_all(DELIVERY,filters={'status':['in',['Queued','Generating']]},
        fields=['name','schedule','status','scheduled_slot','trigger_type','started_at','creation'],
        order_by='creation asc',limit_page_length=100)
    for entry in pending:
        try:
            if entry.status == 'Generating':
                started = datetime.fromisoformat(str(entry.started_at)) if entry.started_at else now
                if (now-started).total_seconds() > 2100:
                    _pending_result(entry,'Interrupted',now,
                        'Worker exceeded 35 minutes or stopped. Check Email Queue before explicitly resending.')
                continue
            if entry.trigger_type != 'Scheduled':
                created = datetime.fromisoformat(str(entry.get('creation'))) if entry.get('creation') else now
                if (now-created).total_seconds() > 2100:
                    _pending_result(entry,'Interrupted',now,'Manual run stopped before generation. Review and run again explicitly.')
                continue
            scheduled = datetime.fromisoformat(entry.scheduled_slot)
            if not 0 <= (now-scheduled).total_seconds() < 900:
                _pending_result(entry,'Failed',now,'Scheduled job did not start within the 15-minute window. No automatic late send.')
                continue
            doc = _schedule(entry.schedule)
            if not doc.get('enabled'):
                _pending_result(entry,'Cancelled',now,'Schedule was disabled before generation.')
                continue
            check_environment(doc)
            # The DB claim is committed before Redis is contacted. If Redis fails,
            # the next minute retries this outbox entry. Duplicate queue entries are
            # harmless: deliver() locks the row and only Queued can begin work.
            frappe.enqueue('is_production.production.controllers.hod_presentation_scheduler.deliver',
                queue='long', timeout=1800, enqueue_after_commit=False, delivery_name=entry.name)
        except Exception:
            frappe.db.rollback()
            frappe.log_error(title='HOD pending delivery needs attention',message=frappe.get_traceback())


def _pending_result(entry,status,now,message):
    current = frappe.db.sql('SELECT status FROM `tabHOD Presentation Delivery` WHERE name=%s FOR UPDATE', (entry.name,))
    if not current or current[0][0] != entry.status:
        frappe.db.commit()
        return
    frappe.db.set_value(DELIVERY,entry.name,{'status':status,'completed_at':now,'error':message},update_modified=False)
    frappe.db.set_value(SCHEDULE,entry.schedule,{'last_run':now,'last_status':status,'last_error':message},update_modified=False)
    frappe.db.commit()


def _claim_delivery(doc, slot, trigger):
    key = policy.delivery_key(doc.name, slot)
    frappe.db.savepoint('hod_delivery_claim')
    try:
        frappe.get_doc({'doctype':DELIVERY,'delivery_key':key,'schedule':doc.name,
            'scheduled_slot':slot,'trigger_type':trigger,'status':'Queued'}).insert(ignore_permissions=True)
    except frappe.DuplicateEntryError:
        frappe.db.rollback(save_point='hod_delivery_claim')
        return None
    return key


def _generate(doc):
    from is_production.production.page.hod_presentation import hod_presentation as page
    from is_production.production.report.hod_presentation import hod_presentation as report
    from .hod_schedule_renderer import capture

    validate_schedule(doc)
    filters = policy.report_filters(doc, _now().date())
    previous = frappe.session.user
    try:
        frappe.set_user(doc.run_as)
        result = page.get_data(filters)
        rows = result.get('result') or []
        validate_rows(rows, filters['site'])
        app = Path(frappe.get_app_path('is_production')).resolve()
        bench = Path(frappe.get_site_path()).resolve().parents[1]
        no_sandbox = bool(frappe.conf.get('hod_browser_no_sandbox', False))
        slides = capture(rows, bench, app, no_sandbox=no_sandbox)
        if len(slides) != len(rows):
            raise ValueError('A dashboard capture is missing; refusing a partial presentation.')
        output = BytesIO()
        report.build_hod_presentation({'site':' / '.join(filters['site']),
            'generated_by':doc.run_as,'captured_slides':slides},output)
        content = output.getvalue()
        if not content.startswith(b'PK'):
            raise ValueError('PowerPoint builder did not produce a PPTX file.')
    finally:
        frappe.set_user(previous)
    filename = 'HOD_Presentation_to_'+filters['end_date']+'.pptx'
    return content, filename, filters


def _save_presentation(doc, content, filename):
    from frappe.utils.file_manager import save_file
    return save_file(filename, content, SCHEDULE, doc.name, is_private=1)


def _email_ready():
    from frappe.utils import cint
    if cint(frappe.conf.get('mute_emails')) or cint(frappe.conf.get('disable_email_queue')):
        raise ValueError('Email is muted/disabled on this site; preview is still available.')
    if not frappe.db.exists('Email Account', {'enable_outgoing':1,'default_outgoing':1}):
        raise ValueError('Configure a default outgoing Email Account before sending.')


def deliver(delivery_name):
    """Claim once, generate, then atomically queue email and mark Email Queued."""
    row = frappe.db.sql('SELECT status FROM `tabHOD Presentation Delivery` WHERE name=%s FOR UPDATE',
                        (delivery_name,))
    if not row or row[0][0] != 'Queued':
        frappe.db.commit()
        return
    entry = frappe.get_doc(DELIVERY, delivery_name)
    frappe.db.set_value(DELIVERY, delivery_name, {'status':'Generating','started_at':_now()}, update_modified=False)
    frappe.db.commit()
    try:
        doc = _schedule(entry.schedule)
        if entry.trigger_type == 'Scheduled' and not doc.enabled:
            frappe.db.set_value(DELIVERY,delivery_name,{'status':'Cancelled','completed_at':_now()},update_modified=False)
            frappe.db.commit()
            return {'status':'Cancelled'}
        check_environment(doc)
        _email_ready()
        content, filename, filters = _generate(doc)
        # Recheck immediately before queuing in case the schedule changed during capture.
        fresh = _schedule(entry.schedule)
        if fresh.modified != doc.modified or (entry.trigger_type == 'Scheduled' and not fresh.enabled):
            raise ValueError('Schedule changed during generation. No email was queued; run again explicitly.')
        addresses = policy.recipients(doc)
        file = _save_presentation(doc, content, filename)
        subject = 'HOD Presentation · '+filters['end_date']+' · '+' / '.join(filters['site'])
        message = ('Please find attached the HOD Presentation for '+escape(' / '.join(filters['site']))+
                   ', through '+escape(filters['end_date'])+'.<br>Start dates follow each site’s production planning period.')
        frappe.sendmail(recipients=addresses, subject=subject, message=message,
            attachments=[{'fname':filename,'fcontent':content}], delayed=True,
            reference_doctype=SCHEDULE, reference_name=doc.name)
        frappe.db.set_value(DELIVERY,delivery_name,{'status':'Email Queued','completed_at':_now(),
            'recipients_json':frappe.as_json(addresses),'filters_json':frappe.as_json(filters),
            'presentation':file.name},update_modified=False)
        frappe.db.set_value(SCHEDULE,doc.name,{'last_run':_now(),'last_status':'Email Queued',
            'last_presentation':file.name,'last_error':''},update_modified=False)
        frappe.db.commit()
        return {'status':'Email Queued','file_url':file.file_url,'recipients':addresses}
    except Exception:
        details = frappe.get_traceback()
        frappe.db.rollback()
        frappe.db.set_value(DELIVERY,delivery_name,{'status':'Failed','completed_at':_now(),'error':details},update_modified=False)
        frappe.db.set_value(SCHEDULE,entry.schedule,{'last_run':_now(),'last_status':'Failed','last_error':details},update_modified=False)
        frappe.log_error(title='HOD PowerPoint delivery failed',message=details)
        frappe.db.commit()
        raise


def run_now(schedule_name, send=0):
    """Bench execute entry point. send=0 saves a private preview; send=1 explicitly queues email."""
    doc = _schedule(schedule_name)
    doc.check_permission('write')
    if str(send) not in ('0','1','False','True'):
        raise ValueError('send must be 0 (preview) or 1 (queue email).')
    if str(send) in ('1','True'):
        check_environment(doc)
        _email_ready()
        key = _claim_delivery(doc, 'manual-'+uuid.uuid4().hex, 'Manual')
        frappe.db.commit()
        return deliver(key)
    content, filename, filters = _generate(doc)
    file = _save_presentation(doc,content,filename)
    frappe.db.commit()
    return {'status':'Preview only; no email queued','file_url':file.file_url,'filters':filters}
