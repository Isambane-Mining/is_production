const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture() {
    const events = {}, changes = [], properties = [];
    const rows = [{doctype: 'WearCheck Users', name: 'row-1', user: ''}];
    const context = {frappe: {
        ui: {form: {on: (doctype, handlers) => {events[doctype] = handlers;}}},
        model: {set_value: async (doctype, name, field, value) => {
            changes.push({doctype, name, field, value});
            rows.find(row => row.name === name)[field] = value;
        }},
    }};
    const source = fs.readFileSync(path.join(__dirname,
        '../../is_production/production/doctype/hod_presentation_schedule/hod_presentation_schedule.js'), 'utf8');
    vm.runInNewContext(source, context);
    const grid = name => ({update_docfield_property: (...args) => properties.push([name, ...args])});
    const frm = {doc: {doctype: 'HOD Presentation Schedule', run_as: 'runner@example.com', sites: rows},
        fields_dict: {sites: {grid: grid('sites')}, recipients: {grid: grid('recipients')}}};
    return {events, frm, changes, properties, rows};
}

test('adding a location fills the required User before client validation', async () => {
    const f = fixture();
    await f.events['WearCheck Users'].sites_add(f.frm);
    assert.equal(f.rows[0].user, 'runner@example.com');
});

test('changing Run As updates all location rows', async () => {
    const f = fixture();
    f.rows.push({doctype: 'WearCheck Users', name: 'row-2', user: 'old'});
    await f.events['HOD Presentation Schedule'].run_as(f.frm);
    assert.equal(f.changes.length, 2);
    assert.ok(f.rows.every(row => row.user === 'runner@example.com'));
});

test('shared child behavior is scoped to the HOD parent', async () => {
    const f = fixture();
    f.frm.doc.doctype = 'Some Other Parent';
    await f.events['WearCheck Users'].sites_add(f.frm);
    assert.equal(f.changes.length, 0);
});

test('Enabled is displayed as User account status, not a separate recipient toggle', async () => {
    const f = fixture();
    await f.events['HOD Presentation Schedule'].refresh(f.frm);
    assert.ok(f.properties.some(p => p[0] === 'recipients' && p[1] === 'enabled' && p[2] === 'read_only' && p[3] === 1));
});
