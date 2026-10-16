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
    page.setDefaultTimeout(5000);
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
          if (request.method.endsWith('get_defaults')) return {message: {date: fixture.date || '2026-10-12', shift: fixture.shift || 'Day'}};
          return {message: fixture};
        }
      };
    }, fixture);
    await page.evaluate(()=>{window.module={exports:{}};});
    await page.addScriptTag({path: path.resolve(__dirname, '../../../public/js/hourly_productivity_summary_view.js')});
    await page.addScriptTag({path: script});
    await page.evaluate(()=>{window.renderSummaryForTest=module.exports.renderSummary;delete window.module;});
    await page.evaluate(() => frappe.pages['hourly-productivity-summary'].on_page_load(wrapper));
    await page.waitForFunction(() => wrapper.hourlyProductivitySummary.initializing === false);
    await page.evaluate(() => wrapper.hourlyProductivitySummary.site.set_value('Koppie'));
    assert.equal(await page.locator('.hps-table').first().locator('tbody tr').count(), 12);
    assert.equal(await page.locator('.hps-pie').count(), 1);
    assert.ok(await page.locator('.hps-content').innerText().then(text => text.includes('No record · 0 shown')));
    if (await page.locator('.hps-explanation').count()) {
      await page.locator('.hps-explanation summary').click();
      assert.ok(await page.locator('.hps-explanation').getAttribute('open') !== null);
    }
    for (const width of [390, 768, 1280]) {
      await page.setViewportSize({width, height: 1000});
      const layout = await page.evaluate(() => ({viewport: innerWidth, body: document.documentElement.scrollWidth,
        cards: document.querySelector('.hps-kpis').getBoundingClientRect().width,
        chart: document.querySelector('.hps-chart-section').getBoundingClientRect().width}));
      assert.ok(layout.body <= width + 1, JSON.stringify(layout));
      await page.screenshot({path: `/tmp/hps-preview-${width}.png`, fullPage: true});
    }
    const partialIndex = fixture.hours.findIndex(hour=>hour.fatigue_minutes > 0 && hour.fatigue_minutes < 60);
    assert.ok(partialIndex >= 0, 'Fixture exercises partial-hour fatigue');
    const row = page.locator('.hps-table').first().locator('tbody tr').nth(partialIndex);
    const background = await row.evaluate(el=>getComputedStyle(el).backgroundImage);
    assert.ok(background.includes('rgb(36, 95, 158) 0%') && background.includes('rgb(36, 95, 158) 50%'),background);
    assert.ok(!(await row.innerText()).includes('minutes scheduled fatigue'));
    assert.equal(await row.locator('td').nth(1).innerText(),Number(fixture.hours[partialIndex].target_bcm).toLocaleString());
    assert.equal(await page.locator('.hps-table').first().locator('tbody tr.hps-orange').count(),0);
    const contrast = (foreground, background) => {
      const luminance = rgb => {
        const channels = rgb.map(channel=>channel/255).map(value=>value <= .04045 ? value/12.92 : ((value+.055)/1.055)**2.4);
        return channels[0]*.2126 + channels[1]*.7152 + channels[2]*.0722;
      };
      const values = [luminance(foreground),luminance(background)].sort((a,b)=>b-a);
      return (values[0]+.05)/(values[1]+.05);
    };
    const chartBefore = await page.locator('.hps-chart-section').evaluate(el=>el.outerHTML);
    for (const theme of ['dark','light']) {
      await page.emulateMedia({colorScheme:theme});
      const red = page.locator('.hps-table').first().locator('tbody tr.hps-red').first();
      const green = page.locator('.hps-table').first().locator('tbody tr.hps-green').first();
      const redImage = await red.evaluate(el=>getComputedStyle(el).backgroundImage);
      const greenImage = await green.evaluate(el=>getComputedStyle(el).backgroundImage);
      assert.ok(redImage.includes('rgb(214, 60, 75)'),`${theme} red: ${redImage}`);
      assert.ok(greenImage.includes('rgb(0, 128, 60)'),`${theme} green: ${greenImage}`);
      assert.equal(await row.evaluate(el=>getComputedStyle(el).color),'rgb(255, 255, 255)');
      const partialImage = await row.evaluate(el=>getComputedStyle(el).backgroundImage);
      assert.ok(partialImage.includes('rgb(36, 95, 158) 0%'),partialImage);
      assert.ok(partialImage.includes('rgb(0, 128, 60) 50%'),partialImage);
      for (const cell of await row.locator('th,td').all()) {
        assert.equal(await cell.evaluate(el=>getComputedStyle(el).backgroundColor),'rgba(0, 0, 0, 0)');
        assert.equal(await cell.evaluate(el=>getComputedStyle(el).color),'rgb(255, 255, 255)');
      }
      const palette = await row.evaluate(el=>{
        const style=getComputedStyle(el);
        return ['green','red','blue','grey'].map(key=>[key,style.getPropertyValue('--hps-hour-'+key).trim()]);
      });
      for (const [key,hex] of palette) {
        const rgb = hex.slice(1).match(/../g).map(channel=>parseInt(channel,16));
        assert.ok(contrast(key === 'grey' ? [0,0,0] : [255,255,255],rgb)>=4.5,`${theme} contrast for ${hex}`);
      }
      assert.equal(await page.locator('.hps-chart-section').evaluate(el=>el.outerHTML),chartBefore);
      await page.locator('.hps-table').first().screenshot({path:`/tmp/hps-hourly-colours-${theme}.png`});
    }
    await page.emulateMedia({colorScheme:'light'});
    if (process.env.HPS_NON_WORKING_FIXTURE) {
      const nonWorking = JSON.parse(fs.readFileSync(process.env.HPS_NON_WORKING_FIXTURE, 'utf8'));
      await page.evaluate(data=>{$('.hps-date input').val(data.date);$('.hps-content').html(renderSummaryForTest(data));},nonWorking);
      assert.equal(await page.locator('.hps-table').first().locator('tbody tr.hps-grey').count(),12);
      const grey = page.locator('.hps-table').first().locator('tbody tr').first();
      for (const theme of ['light','dark']) {
        await page.emulateMedia({colorScheme:theme});
        assert.ok((await grey.evaluate(el=>getComputedStyle(el).backgroundImage)).includes('rgb(245, 245, 245)'));
        assert.equal(await grey.evaluate(el=>getComputedStyle(el).color),'rgb(0, 0, 0)');
      }
      await page.emulateMedia({colorScheme:'light'});
      await page.screenshot({path:'/tmp/hps-non-working.png',fullPage:true});
      await page.evaluate(()=>$('.hps-content').html(renderSummaryForTest(fixture)));
    }
    // Missing MPP must retain an unknown target and readable scheduled-fatigue colour.
    const unknownTarget = JSON.parse(JSON.stringify(fixture));
    Object.assign(unknownTarget.hours[0], {target_bcm:null,achievement:null,presentation_colour:'neutral',fatigue_minutes:30,
      presentation_segments:[{start_percent:0,end_percent:50,category:'blue'},
        {start_percent:50,end_percent:100,category:'neutral'}]});
    await page.emulateMedia({colorScheme:'dark'});
    await page.evaluate(data=>{
      document.documentElement.style.setProperty('--text-color','#fff');
      document.documentElement.style.setProperty('--card-bg','#171717');
      $('.hps-content').html(renderSummaryForTest(data));
    },unknownTarget);
    const unknownRow = page.locator('.hps-table').first().locator('tbody tr').first();
    assert.equal(await unknownRow.evaluate(el=>getComputedStyle(el).color),'rgb(0, 0, 0)');
    assert.ok((await unknownRow.evaluate(el=>getComputedStyle(el).backgroundImage)).includes('rgb(255, 255, 255) 50%'));
    assert.equal(await unknownRow.locator('td').nth(1).innerText(),'—');
    assert.equal(await unknownRow.locator('.hps-mixed-text').count(),9);
    await page.evaluate(()=>{
      document.documentElement.style.removeProperty('--text-color');
      document.documentElement.style.removeProperty('--card-bg');
      $('.hps-content').html(renderSummaryForTest(fixture));
    });
    await page.emulateMedia({colorScheme:'light'});
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
    assert.ok(!(await page.locator('.hps-target-heading').innerText()).includes('STALE-PLAN'));
    await page.evaluate(() => {
      frappe.call = async () => {throw new Error('Access denied');};
      return wrapper.hourlyProductivitySummary.load();
    });
    assert.equal(await page.locator('.hps-pie').count(), 0);
    assert.ok((await page.locator('.hps-message').innerText()).includes('Unable to load'));
    assert.deepEqual(errors, []);
    console.log('PASS: 12 hours, theme-stable chart, white text on dark green/red/blue, partial blue fatigue, light/dark contrast >=4.5:1, grey non-working, missing-record label, disclosure, responsive 390/768/1280px, stale response, denied access');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
