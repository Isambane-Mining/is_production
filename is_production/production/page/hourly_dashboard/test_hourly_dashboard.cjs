// Run with: node --test path/to/test_hourly_dashboard.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const flush = () => new Promise(resolve => setImmediate(resolve));

function dashboard(clock = "2026-09-30T10:05:00") {
  let day = '2026-09-30';
  let field;
  let timer;
  const requests = [];
  const elements = [];
  const context = {
    console, Promise, Map,
    Date: class extends Date {
      constructor(...args) { super(...(args.length ? args : [clock])); }
    },
    __: value => value,
    setTimeout: (callback, delay) => { timer = { callback, delay }; return 1; },
    clearTimeout: () => { timer = null; },
    $: () => {
      const element = {
        content: '', appendTo() { return this; },
        text(value) { this.content = value; }, html(value) { this.content = value; }
      };
      elements.push(element);
      return element;
    },
    frappe: {
      pages: { 'hourly-dashboard': {} },
      utils: { escape_html: value => String(value) },
      show_alert() {},
      ui: { make_app_page: () => ({
        main: {}, add_field: options => {
          field = { options, value: '', get_value() { return this.value; },
            set_value(value) { this.value = value; options.change(); return Promise.resolve(); }
          };
          return field;
        }
      }) },
      call: async ({ method, args }) => {
        if (method.endsWith('get_operational_day')) return { message: day };
        if (method.endsWith('get_site_colour_map')) return { message: { Koppie: '#feff8d' } };
        assert.equal(method, 'frappe.desk.query_report.run');
        requests.push(args.filters);
        return { message: { result: [
          { site: 'Koppie', production_day: args.filters.production_date, excavator: 'EX-1', slot_01: 230 },
          { site: 'Gwab', production_day: args.filters.production_date, is_empty_site: 1 }
        ] } };
      }
    }
  };
  vm.runInNewContext(fs.readFileSync(`${__dirname}/hourly_dashboard.js`, 'utf8'), context);
  context.frappe.pages['hourly-dashboard'].on_page_load({});
  return { requests, elements, get field() { return field; }, get timer() { return timer; },
    setDay(value) { day = value; }, unload() { context.frappe.pages['hourly-dashboard'].on_page_unload(); }
  };
}

test('initial date comes from server; renders production and empty planned sites', async () => {
  const app = dashboard();
  await flush();
  assert.equal(app.field.options.fieldtype, 'Date');
  assert.equal(app.field.value, '2026-09-30');
  assert.equal(app.requests.length, 1);
  assert.equal(app.requests[0].production_date, '2026-09-30');
  assert.match(app.elements[2].content, /Site: Koppie/);
  assert.match(app.elements[2].content, /Site: Gwab/);
  assert.match(app.elements[2].content, /230/);
  assert.match(app.elements[2].content, /background:#feff8d/);
});

test('date changes reload immediately and historical selection survives auto refresh', async () => {
  const app = dashboard();
  await flush();
  await app.field.set_value('2026-09-29');
  await flush();
  assert.equal(app.requests.at(-1).production_date, '2026-09-29');
  app.setDay('2026-10-01');
  app.timer.callback();
  await flush();
  assert.equal(app.requests.at(-1).production_date, '2026-09-29');
});

test('aligned live refresh advances operational day and reschedules; unload clears timer', async () => {
  const app = dashboard();
  await flush();
  assert.equal(app.timer.delay, 5 * 60 * 1000);
  app.setDay('2026-10-01');
  app.timer.callback();
  await flush();
  assert.equal(app.field.value, '2026-10-01');
  assert.equal(app.requests.at(-1).production_date, '2026-10-01');
  assert.ok(app.timer);
  app.unload();
  assert.equal(app.timer, null);
});

for (const [clock, minutes] of [
  ['2026-09-30T10:10:00', 20], ['2026-09-30T10:30:00', 40]
]) {
  test(`refresh scheduling at ${clock}`, async () => {
    const app = dashboard(clock);
    await flush();
    assert.equal(app.timer.delay, minutes * 60 * 1000);
  });
}
