// Optional local Chromium integration check; no live site, login or database writes.
// HPS_PLAYWRIGHT=/path/to/playwright-core HPS_FIXTURE=/path/to/report.json node this-file
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.HPS_PLAYWRIGHT || 'playwright-core');
const fixture = JSON.parse(fs.readFileSync(process.env.HPS_FIXTURE, 'utf8'));
const script = path.join(__dirname, 'hourly_productivity_summary.js');
const jquery = path.resolve(__dirname, '../../../../../frappe/node_modules/jquery/dist/jquery.js');

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.setContent('<html><body style="margin:0;padding:12px;font-family:Arial"><main id="main"></main></body></html>');
    await page.addScriptTag({path: jquery});
    await page.evaluate(data => {
      window.__ = text => text;
      window.fixture = data;
      window.wrapper = {};
      window.calls = [];
      window.frappe = {
        pages: {'hourly-productivity-summary': {}},
        ui: {make_app_page: () => ({main: window.$('#main')}), form: {
          make_control: ({parent, df}) => {
            const input = df.fieldtype === 'Select' ? window.$('<select>') : window.$('<input>');
            if (df.fieldtype === 'Select') df.options.split('\n').forEach(x => input.append(window.$('<option>').text(x)));
            parent.append(window.$('<label>').text(df.label), input);
            input.css({width:'100%', boxSizing:'border-box', height:'32px'});
            return {get_value: () => input.val(), set_value: async value => {input.val(value); await df.onchange();}};
          }
        }},
        call: async request => {
          calls.push(request);
          if (request.method.endsWith('get_defaults')) return {message: {date: '2026-10-12', shift: 'Day'}};
          return {message: fixture};
        }
      };
    }, fixture);
    await page.addScriptTag({path: script});
    await page.evaluate(() => frappe.pages['hourly-productivity-summary'].on_page_load(wrapper));
    await page.waitForFunction(() => wrapper.hourlyProductivitySummary.initializing === false);
    await page.evaluate(() => wrapper.hourlyProductivitySummary.site.set_value('Koppie'));
    assert.equal(await page.locator('.hps-table').first().locator('tbody tr').count(), 12);
    assert.equal(await page.locator('.hps-pie').count(), 1);
    assert.ok(await page.locator('.hps-content').innerText().then(text => text.includes('No record · 0 shown')));
    await page.locator('.hps-explanation summary').click();
    assert.ok(await page.locator('.hps-explanation').getAttribute('open') !== null);
    for (const width of [390, 768, 1280]) {
      await page.setViewportSize({width, height: 1000});
      const layout = await page.evaluate(() => ({viewport: innerWidth, body: document.documentElement.scrollWidth,
        cards: document.querySelector('.hps-kpis').getBoundingClientRect().width,
        chart: document.querySelector('.hps-chart-section').getBoundingClientRect().width}));
      assert.ok(layout.body <= width + 1, JSON.stringify(layout));
      await page.screenshot({path: `/tmp/hps-preview-${width}.png`, fullPage: true});
    }
    // A slow response for an old selection must not replace the current selection.
    await page.evaluate(async () => {
      let resolveOld;
      const old = new Promise(resolve => {resolveOld = resolve;});
      const original = frappe.call;
      let count = 0;
      frappe.call = request => ++count === 1 ? old : original(request);
      const first = wrapper.hourlyProductivitySummary.load();
      await wrapper.hourlyProductivitySummary.load();
      resolveOld({message: {...fixture, plan_name: 'STALE-PLAN'}});
      await first;
    });
    assert.ok(!(await page.locator('.hps-basis').innerText()).includes('STALE-PLAN'));
    await page.evaluate(() => {
      frappe.call = async () => {throw new Error('Access denied');};
      return wrapper.hourlyProductivitySummary.load();
    });
    assert.equal(await page.locator('.hps-pie').count(), 0);
    assert.ok((await page.locator('.hps-message').innerText()).includes('Unable to load'));
    assert.deepEqual(errors, []);
    console.log('PASS: 12 hours, chart, missing-record label, disclosure, responsive 390/768/1280px, stale response, denied access');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
