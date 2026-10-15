// Local Chromium check with a deterministic backend fixture; no live-site writes.
// HPS_PLAYWRIGHT=/path/to/playwright-core HPS_FIXTURE=/path/to/dashboard.json node this-file
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.HPS_PLAYWRIGHT || 'playwright-core');
const fixture = JSON.parse(fs.readFileSync(process.env.HPS_FIXTURE, 'utf8'));
const script = path.join(__dirname, 'hourly_productivity_summary.js');
const jquery = path.resolve(__dirname, '../../../../../frappe/node_modules/jquery/dist/jquery.js');

(async () => {
  const browser = await chromium.launch({headless: true,
    ...(process.env.HPS_CHROMIUM ? {executablePath: process.env.HPS_CHROMIUM} : {}), args: ['--no-sandbox']});
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
            input.css({width: '100%', boxSizing: 'border-box', height: '32px'});
            return {get_value: () => input.val(), set_value: async value => {input.val(value); await df.onchange();}};
          }
        }},
        call: async request => {
          calls.push(request);
          if (request.method.endsWith('get_defaults')) return {message: {date: '2026-10-12', shift: 'Full Day'}};
          return {message: fixture};
        }
      };
    }, fixture);
    await page.addScriptTag({path: script});
    await page.evaluate(() => frappe.pages['hourly-productivity-summary'].on_page_load(wrapper));
    await page.waitForFunction(() => wrapper.hourlyProductivitySummary.initializing === false);
    await page.evaluate(async () => {
      await wrapper.hourlyProductivitySummary.to_date.set_value('2026-10-13');
      await wrapper.hourlyProductivitySummary.site.set_value('Koppie');
    });
    assert.equal(await page.locator('.hps-hour-row').count(), 24);
    assert.equal(await page.locator('.hps-kpi').count(), 5);
    assert.equal(await page.locator('.hps-pie').count(), 1);
    assert.equal(await page.locator('.hps-explanation').count(), 0);
    assert.ok((await page.locator('.hps-content').innerText()).includes('Missing record · 0 shown'));
    assert.equal(await page.locator('.hps-timeline tbody tr').count(), 2);
    assert.equal(await page.locator('.hps-timeline tbody tr').first().locator('td').count(), 24);
    assert.ok(await page.locator('.hps-previous').isDisabled());
    const requestCount = await page.evaluate(() => calls.length);
    const kpis = await page.locator('.hps-kpis').innerText();
    await page.locator('.hps-next').click();
    assert.ok((await page.locator('.hps-selected-date').innerText()).includes('2026-10-13'));
    assert.ok(await page.locator('.hps-next').isDisabled());
    assert.equal(await page.evaluate(() => calls.length), requestCount);
    assert.equal(await page.locator('.hps-kpis').innerText(), kpis);
    await page.locator('.hps-refresh').click();
    assert.ok((await page.locator('.hps-selected-date').innerText()).includes('2026-10-13'));
    await page.locator('.hps-previous').click();
    for (const width of [390, 768, 1280]) {
      await page.setViewportSize({width, height: 1000});
      const layout = await page.evaluate(() => ({viewport: innerWidth, body: document.documentElement.scrollWidth,
        timeline: document.querySelector('.hps-timeline').parentElement.scrollWidth,
        timelineWidth: document.querySelector('.hps-timeline').parentElement.clientWidth}));
      assert.ok(layout.body <= width + 1, JSON.stringify(layout));
      assert.ok(layout.timeline > layout.timelineWidth, JSON.stringify(layout));
      const sticky = await page.locator('.hps-timeline tbody th').first().evaluate(element => getComputedStyle(element).position);
      assert.equal(sticky, 'sticky');
      await page.screenshot({path: `/tmp/hps-preview-${width}.png`, fullPage: true});
    }
    await page.evaluate(async () => {
      let resolveOld;
      const old = new Promise(resolve => {resolveOld = resolve;});
      const original = frappe.call;
      let count = 0;
      frappe.call = request => ++count === 1 ? old : original(request);
      const first = wrapper.hourlyProductivitySummary.load();
      await wrapper.hourlyProductivitySummary.load();
      resolveOld({message: {...fixture, site: 'STALE-SITE'}});
      await first;
    });
    assert.ok(!(await page.locator('.hps-period').innerText()).includes('STALE-SITE'));
    // Range change outside the selected day resets navigation; invalid ranges never call the backend.
    await page.evaluate(async () => {
      fixture = {...fixture, days: [fixture.days[1]], from_date: '2026-10-13'};
      await wrapper.hourlyProductivitySummary.load();
    });
    assert.ok((await page.locator('.hps-selected-date').innerText()).includes('2026-10-13'));
    await page.evaluate(async () => {
      const count = calls.length;
      await wrapper.hourlyProductivitySummary.from_date.set_value('2026-10-15');
      if (calls.length !== count) throw Error('Invalid range requested');
    });
    assert.ok((await page.locator('.hps-message').innerText()).includes('From Date'));
    await page.evaluate(async () => {
      frappe.call = async () => {throw new Error('Access denied');};
      await wrapper.hourlyProductivitySummary.from_date.set_value('2026-10-12');
    });
    assert.equal(await page.locator('.hps-pie').count(), 0);
    assert.ok((await page.locator('.hps-message').innerText()).includes('Unable to load'));
    assert.deepEqual(errors, []);
    console.log('PASS: Full Day, five KPIs, hourly pie, timings, timeline, bounded navigation, refresh, mobile scrolling, stale response, invalid range, denied access');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
