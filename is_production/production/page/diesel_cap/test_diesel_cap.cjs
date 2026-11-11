const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const context = {frappe: {pages: {'diesel-cap': {}}}, __: x => x};
vm.createContext(context);
const source = path.join(__dirname, 'diesel_cap.js');
if (fs.existsSync(source)) vm.runInContext(fs.readFileSync(source, 'utf8'), context);
const Page = context.frappe.DieselCapPage;

test('summaries use returned totals and volume-weighted fuel ratios', () => {
  assert.ok(Page, 'Diesel Cap page must exist');
  const summary = Page.prototype.summarize([
    {actual_bcm: 100, total_tallies_cubes: 50, total_diesel_litres: 200, diesel_cap: 2},
    {actual_bcm: 900, total_tallies_cubes: 450, total_diesel_litres: 900, diesel_cap: 1},
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(summary)), {
    actual_bcm: 1000, total_tallies_cubes: 500, total_diesel_litres: 1100,
    diesel_cap: 1.1, tallies_fuel_cap: 2.2,
  });
  assert.equal(Page.prototype.summarize([]).diesel_cap, 0);
  assert.equal(Page.prototype.summarize([{total_diesel_litres: 50}]).tallies_fuel_cap, 0);
});

function harness() {
  assert.ok(Page, 'Diesel Cap page must exist');
  const page = Object.create(Page.prototype);
  page.request_id = 0;
  page.controls = Object.fromEntries(Object.entries({start_date:'2026-09-01', end_date:'2026-09-30', site:'Koppie'})
    .map(([key,value]) => [key,{get_value:()=>value}]));
  page.clear_results = () => {page.rendered = null;};
  page.set_busy = value => {page.busy = value;};
  page.show_status = message => {page.status = message;};
  page.render = result => {page.rendered = result;};
  page.ensure_table = async () => {};
  return page;
}

test('uses permission-checked report runner with unchanged filters and fresh execution', async () => {
  const page = harness();
  let request;
  context.frappe.call = async args => {request = args; return {message:{result:[{site:'Koppie'}],columns:[]}};};
  await page.refresh();
  assert.equal(request.method, 'frappe.desk.query_report.run');
  assert.deepEqual(JSON.parse(JSON.stringify(request.args)), {
    report_name:'Diesel Cap Report', filters:{start_date:'2026-09-01',end_date:'2026-09-30',site:'Koppie'},
    ignore_prepared_report:1,
  });
  assert.equal(page.rendered.result[0].site, 'Koppie');
  assert.equal(page.busy, false);
});

test('latest filter request wins even if older report finishes later', async () => {
  const page = harness();
  const pending = [];
  context.frappe.call = () => new Promise(resolve => pending.push(resolve));
  const first = page.refresh();
  await Promise.resolve();
  const second = page.refresh();
  await Promise.resolve();
  pending[1]({message:{result:[{site:'new'}], columns:[]}});
  await second;
  pending[0]({message:{result:[{site:'old'}], columns:[]}});
  await first;
  assert.equal(page.rendered.result[0].site, 'new');
});

test('errors and missing filters clear obsolete results and release loading state', async () => {
  const page = harness();
  page.rendered = {old:true};
  context.frappe.call = async () => {throw new Error('Denied');};
  await page.refresh();
  assert.equal(page.rendered, null);
  assert.match(page.status, /Unable/);
  assert.equal(page.busy, false);
  page.controls.start_date.get_value = () => '';
  await page.refresh();
  assert.match(page.status, /Start Date/);
});
