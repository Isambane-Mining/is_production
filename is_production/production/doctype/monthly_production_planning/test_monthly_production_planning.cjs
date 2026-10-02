// Run with: node --test path/to/test_monthly_production_planning.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function planning({ assignments = [], dozers = [], active = [], assets = [], roles = ['Production Area Manager'], call } = {}) {
  const handlers = {};
  const storage = new Map();
  const requests = [];
  const messages = [];
  const cleared = [];
  const renders = [];
  let serial = 0;
  const doc = { name: 'test-plan', location: 'Koppie', excavator_truck_assignments: assignments, dozer_table: dozers };
  for (const [field, rows] of Object.entries({ excavator_truck_assignments: assignments, dozer_table: dozers })) {
    rows.forEach((row, idx) => Object.assign(row, { name: `${field}-${idx}`, doctype: field, idx: idx + 1 }));
  }
  const frm = {
    doc, dirtyCount: 0,
    dirty() { this.dirtyCount++; },
    refresh_field() {}, refresh_fields() {},
    set_value(values) { Object.assign(this.doc, values); },
    add_child(field) {
      const row = { name: `new-${++serial}`, doctype: field, idx: this.doc[field].length + 1 };
      this.doc[field].push(row);
      return row;
    },
    trigger(event) { return handlers['Monthly Production Planning'][event](this); }
  };
  const context = {
    console: { log() {}, error() {} },
    __: (value, args = []) => value.replace(/\{(\d+)\}/g, (_, idx) => args[idx]),
    localStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
    frappe: {
      user_roles: roles,
      ui: { form: { on: (name, events) => { handlers[name] = events; } } },
      msgprint: message => messages.push(message),
      model: { clear_doc(doctype, name) {
        cleared.push(name);
        // Match Frappe's removal from the parent array and reindexing.
        doc[doctype] = doc[doctype].filter(row => row.name !== name);
        doc[doctype].forEach((row, idx) => { row.idx = idx + 1; });
      } },
      call: async options => {
        requests.push(options);
        if (call) return call(options, context, frm);
        const { limit_start, limit_page_length } = options.args;
        return { message: assets.slice(limit_start, limit_start + limit_page_length) };
      }
    }
  };
  vm.runInNewContext(fs.readFileSync(`${__dirname}/monthly_production_planning.js`, 'utf8'), context);
  // Exercise the actual handler and count calculation; isolate DOM rendering.
  context.renderTruckAssignmentUI = () => renders.push('trucks');
  context.renderDozerAssignmentUI = () => renders.push('dozers');
  context.setActiveEmptyExcavators(frm, active);
  return { frm, context, requests, messages, cleared, renders,
    sync: () => frm.trigger('refresh_machines_from_assets'),
    active: () => Array.from(context.getActiveEmptyExcavators(frm)) };
}
const asset = (name, asset_category) => ({ name, asset_category, item_name: `Model ${name}` });

test('site sync preserves onsite allocations and planning, removes offsite references and adds spare machines', async () => {
  const pair = { excavator: 'E1', truck: 'T1', excavator_model: 'saved E', truck_model: 'saved T', sort_order: 7 };
  const spareE = { excavator: 'E2', truck: null };
  const spareT = { truck: 'T2', excavator: null };
  const emptyProduction = { excavator: 'E3' };
  const lostTruck = { excavator: 'E4', truck: 'T-off', sort_order: 9 };
  const lostExcavator = { excavator: 'E-off', truck: 'T3', truck_model: 'keep', sort_order: 4 };
  const bothOffsite = { excavator: 'E-off2', truck: 'T-off2' };
  const productionDozer = { asset_name: 'D1', dozing_type: 'Production', item_name: 'saved model' };
  const tipDozer = { asset_name: 'D2', dozing_type: 'Tip' };
  const spareDozer = { asset_name: 'D3', dozing_type: '' };
  const app = planning({
    assignments: [pair, spareE, spareT, emptyProduction, lostTruck, lostExcavator, bothOffsite],
    dozers: [productionDozer, tipDozer, spareDozer, { asset_name: 'D-off', dozing_type: 'Production' }],
    active: ['E3', 'E-off'],
    assets: [ ...['E1', 'E2', 'E3', 'E4', 'E-new'].map(id => asset(id, 'Excavator')),
      ...['T1', 'T2', 'T3', 'T-new'].map(id => asset(id, 'ADT')),
      ...['D1', 'D2', 'D3', 'D-new'].map(id => asset(id, 'Dozer')) ]
  });
  const snapshots = [pair, spareE, spareT, emptyProduction, productionDozer, tipDozer, spareDozer].map(row => ({ ...row }));
  await app.sync();
  [pair, spareE, spareT, emptyProduction, productionDozer, tipDozer, spareDozer].forEach((row, idx) => {
    assert.deepEqual(row, snapshots[idx]);
    assert.ok([...app.frm.doc.excavator_truck_assignments, ...app.frm.doc.dozer_table].includes(row));
  });
  assert.equal(lostTruck.truck, null);
  assert.equal(lostTruck.sort_order, 9);
  assert.equal(lostExcavator.excavator, null);
  assert.equal(lostExcavator.truck_model, 'keep');
  assert.equal(lostExcavator.sort_order, 4);
  assert.ok(!app.frm.doc.excavator_truck_assignments.includes(bothOffsite));
  assert.equal(app.cleared.length, 2);
  assert.deepEqual(app.active().sort(), ['E3', 'E4']);
  const newE = app.frm.doc.excavator_truck_assignments.find(row => row.excavator === 'E-new');
  const newT = app.frm.doc.excavator_truck_assignments.find(row => row.truck === 'T-new');
  assert.equal(newE.truck, null);
  assert.equal(newT.excavator, null);
  assert.equal(app.frm.doc.dozer_table.find(row => row.asset_name === 'D-new').dozing_type, '');
  assert.equal(app.frm.doc.num_excavators, 3);
  assert.equal(app.frm.doc.num_trucks, 1);
  assert.equal(app.frm.doc.num_dozers, 2);
  assert.match(app.messages.at(-1), /3 added, 5 removed, 10 preserved/);
  assert.deepEqual(app.renders, ['trucks', 'dozers']);
  const rows = [...app.frm.doc.excavator_truck_assignments, ...app.frm.doc.dozer_table];
  await app.sync();
  assert.deepEqual([...app.frm.doc.excavator_truck_assignments, ...app.frm.doc.dozer_table], rows);
  assert.equal(app.frm.dirtyCount, 1);
  assert.match(app.messages.at(-1), /0 added, 0 removed, 13 preserved/);
});

test('empty site removes all machines and cached production excavators', async () => {
  const app = planning({ assignments: [{ excavator: 'E', truck: 'T' }], dozers: [{ asset_name: 'D' }], active: ['E'] });
  await app.sync();
  assert.equal(app.frm.doc.excavator_truck_assignments.length, 0);
  assert.equal(app.frm.doc.dozer_table.length, 0);
  assert.deepEqual(app.active(), []);
  assert.match(app.messages[0], /0 added, 3 removed, 0 preserved/);
});

test('all existing manager roles can synchronise; other roles cannot read or mutate allocations', async () => {
  for (const role of ['Production Area Manager', 'Engineering Area Manager', 'Information Officer', 'Production User', 'System Manager']) {
    const app = planning({ roles: [role], assets: [asset('E', 'Excavator')] });
    await app.sync();
    const authorised = ['Production Area Manager', 'Engineering Area Manager', 'Information Officer'].includes(role);
    assert.equal(app.requests.length, authorised ? 1 : 0);
    assert.equal(app.frm.doc.excavator_truck_assignments.length, authorised ? 1 : 0);
    if (!authorised) assert.equal(app.messages[0].title, 'Permission Required');
  }
});

test('missing location leaves allocations untouched', async () => {
  const app = planning({ assignments: [{ excavator: 'E' }] });
  app.frm.doc.location = '';
  await app.sync();
  assert.equal(app.requests.length, 0);
  assert.equal(app.frm.doc.excavator_truck_assignments.length, 1);
});

test('fetches all pages for the selected site before removing machines', async () => {
  const assets = Array.from({ length: 501 }, (_, idx) => asset(`E${idx}`, 'Excavator'));
  const row = { excavator: 'E500' };
  const app = planning({ assets, assignments: [row] });
  await app.sync();
  assert.equal(app.requests.length, 2);
  assert.equal(app.frm.doc.excavator_truck_assignments.length, 501);
  assert.ok(app.frm.doc.excavator_truck_assignments.includes(row));
  app.requests.forEach(({ method, args }) => {
    assert.equal(method, 'frappe.client.get_list');
    assert.equal(args.filters.location, 'Koppie');
    assert.equal(args.filters.docstatus, 1);
    assert.equal(args.order_by, 'name asc');
  });
  assert.equal(app.requests[1].args.limit_start, 500);
});

test('failed or malformed Asset reads leave existing planning and cache untouched', async () => {
  for (const response of [{ exc: 'PermissionError' }, {}, { message: null }]) {
    const app = planning({ assignments: [{ excavator: 'E', truck: 'T' }], active: ['E'], call: () => response });
    await app.sync();
    assert.equal(app.frm.doc.excavator_truck_assignments[0].truck, 'T');
    assert.deepEqual(app.active(), ['E']);
    assert.equal(app.frm.dirtyCount, 0);
    assert.equal(app.messages.length, 0);
  }
  const app = planning({ assignments: [{ excavator: 'E' }], call: () => { throw new Error('network'); } });
  await assert.rejects(app.sync(), /network/);
  assert.equal(app.frm.doc.excavator_truck_assignments.length, 1);
});

test('site change, document change or revoked role during Asset read cannot apply stale results', async () => {
  for (const change of [
    (context, frm) => { frm.doc.location = 'Gwab'; },
    (context, frm) => { frm.doc = { ...frm.doc, name: 'other-plan' }; },
    context => { context.frappe.user_roles = []; }
  ]) {
    const app = planning({ assignments: [{ excavator: 'E' }], call: (options, context, frm) => {
      change(context, frm);
      return { message: [] };
    } });
    await app.sync();
    assert.equal(app.frm.doc.excavator_truck_assignments.length, 1);
    assert.equal(app.frm.dirtyCount, 0);
  }
});


test('an unchanged valid production plan is not marked dirty or added to empty-excavator cache', async () => {
  const app = planning({ assignments: [{ excavator: 'E', truck: 'T' }],
    assets: [asset('E', 'Excavator'), asset('T', 'ADT')] });
  await app.sync();
  assert.equal(app.frm.dirtyCount, 0);
  assert.deepEqual(app.active(), []);
  assert.match(app.messages[0], /0 added, 0 removed, 2 preserved/);
});
