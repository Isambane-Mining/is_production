"""Preserve pre-release LAB configurations when switching to existing child DocTypes."""
import hashlib

import frappe

SCHEDULE = 'HOD Presentation Schedule'


def recipient_user(email, users):
    matches = {row['name'] for row in users
               if str(row.get('email') or row['name']).casefold() == str(email).strip().casefold()}
    if len(matches) != 1:
        raise ValueError('HOD recipient needs exactly one matching User before migration: ' + str(email))
    return matches.pop()


def execute():
    if not frappe.db.table_exists(SCHEDULE):
        return
    legacy = (
        ('HOD Presentation Recipient', 'Production Notification Recipient', 'email'),
        ('HOD Presentation Schedule Site', 'WearCheck Users', 'site'),
    )
    rows = []
    for old, target, field in legacy:
        if frappe.db.table_exists(old):
            rows.extend((old, target, field, row) for row in frappe.db.sql(
                f'SELECT name, parent, parentfield, idx, `{field}` FROM `tab{old}` '
                'WHERE parenttype=%s', (SCHEDULE,), as_dict=True))
    if not rows:
        return
    if not frappe.db.exists('DocType', 'WearCheck Users'):
        raise ValueError('Install/migrate Engineering before migrating HOD schedule locations.')
    frappe.reload_doc('production', 'doctype', 'production_notification_recipient')
    frappe.reload_doc('engineering', 'doctype', 'wearcheck_users')
    users = frappe.get_all('User', fields=['name', 'email'])
    # Preflight every mapping before inserting any rows or disabling schedules.
    converted = []
    for old, target, field, row in rows:
        values = dict(doctype=target, parent=row.parent, parenttype=SCHEDULE,
                      parentfield=row.parentfield, idx=row.idx)
        values['name'] = 'hod-migrated-' + hashlib.sha256((old + row.name).encode()).hexdigest()[:24]
        if field == 'email':
            values.update(user=recipient_user(row.email, users), user_email=row.email,
                          enabled=1, receive_all_sites=0)
        else:
            runner = frappe.db.get_value(SCHEDULE, row.parent, 'run_as')
            if not runner or not frappe.db.exists('User', runner):
                raise ValueError('Set a valid Run As user on HOD schedule: ' + row.parent)
            values.update(user=runner, location=row.site)
        converted.append(values)
    for values in converted:
        if not frappe.db.exists(values['doctype'], values['name']):
            frappe.get_doc(values).insert(ignore_permissions=True, set_name=values['name'])
    for parent in {row.parent for _, _, _, row in rows}:
        frappe.db.set_value(SCHEDULE, parent, 'enabled', 0, update_modified=False)
    # Preserve legacy tables as backup; model sync removes their obsolete DocType definitions.
    # The enclosing patch transaction commits all converted rows together.
