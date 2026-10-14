// Local fixture integration only: no live site, credentials, or source data writes.
// HPS_PLAYWRIGHT=/path/to/playwright-core HPA_FIXTURE=/path/to/report.json node this-file
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.HPS_PLAYWRIGHT || 'playwright-core');
const fixture = JSON.parse(fs.readFileSync(process.env.HPA_FIXTURE, 'utf8'));

(async () => {
  const browser = await chromium.launch({headless:true,...(process.env.HPS_CHROMIUM ? {executablePath:process.env.HPS_CHROMIUM} : {}),args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror',e=>errors.push(e.message));
    await page.setContent('<html><body style="margin:0;padding:12px;font-family:Arial"><main id="main" class="hourly-productivity"></main></body></html>');
    await page.addScriptTag({path:path.resolve(__dirname,'../../../../../frappe/node_modules/jquery/dist/jquery.js')});
    await page.addStyleTag({path:path.resolve(__dirname,'../../../public/css/dashboards.css')});
    await page.evaluate(data => {
      window.fixture=data;window.wrapper={};window.calls=[];window.routes=[];
      window.frappe={set_route:route=>routes.push(route),pages:{'hourly-productivity-analysis':{}},
        ui:{make_app_page:()=>({main:window.$('#main')}),form:{make_control:({parent,df})=>{
          const input=window.$('<input>').css({width:'100%',boxSizing:'border-box',height:'32px'});
          parent.append(window.$('<label>').text(df.label),input);
          return {get_value:()=>input.val(),set_value:async value=>{input.val(value);await df.onchange();}};
        }}},
        call:async request=>{
          calls.push(request);
          return {message:request.method.endsWith('get_defaults')?{from_date:fixture.from_date,to_date:fixture.to_date}:fixture};
        }};
    },fixture);
    await page.addScriptTag({path:path.resolve(__dirname,'../hourly_productivity/hourly_productivity.js')});
    await page.evaluate(()=>wrapper.hourlyProductivityAnalysis = new is_production.hourly_productivity.Analysis(wrapper));
    await page.waitForFunction(()=>wrapper.hourlyProductivityAnalysis.initializing===false);
    await page.evaluate(()=>wrapper.hourlyProductivityAnalysis.site.set_value('Koppie'));
    assert.equal(await page.locator('.hpa-cell').count(),fixture.rows.length*24);
    assert.equal(await page.locator('.hpa-legend>div').count(),5);
    assert.equal(await page.locator('.hpa-hour').count(),24);
    assert.equal(await page.locator('.hpa-warning').count(),0);
    assert.equal(await page.locator('.hpa-note').count(),0);
    assert.ok((await page.locator('.hpa-targets').innerText()).includes('Normal hourly target: 440 BCM'));
    assert.ok((await page.locator('.hpa-legend').innerText()).includes('Not planned to work'));
    await page.locator('.hpa-investigate').click();
    assert.deepEqual(await page.evaluate(()=>routes), ['/desk/production/hourly-productivity-summary']);
    const classification = await page.evaluate(() => {
      const expected = {green:'rgb(216, 239, 223)',amber:'rgb(255, 240, 199)',red:'rgb(251, 225, 227)',
        bright_red:'rgb(255, 185, 192)',grey:'rgb(247, 248, 250)',unclassified:'rgb(238, 242, 247)'};
      const cells=[...document.querySelectorAll('.hpa-cell')];
      return cells.every((cell,index) => {
        const category=fixture.rows[Math.floor(index/24)].cells[index%24].category;
        return cell.classList.contains('hpa-'+category) && getComputedStyle(cell).backgroundColor === expected[category];
      });
    });
    assert.ok(classification, 'Table colours must match backend categories');
    for (const width of [390,768,1280]) {
      await page.setViewportSize({width,height:1000});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      if (width === 390) {
        assert.equal(await page.locator('.hpa-shift-day span').count(),1);
        const label=await page.locator('.hpa-shift-day span').boundingBox();
        assert.ok(label.x < width,'Day shift label should remain visible on mobile');
      }
      const before=await page.locator('tbody .hpa-sticky-date').first().boundingBox();
      await page.locator('.hpa-scroll').evaluate(el=>{el.scrollLeft=700;});
      const after=await page.locator('tbody .hpa-sticky-date').first().boundingBox();
      assert.ok(Math.abs(before.x-after.x)<1,JSON.stringify({before,after}));
      await page.locator('.hpa-scroll').evaluate(el=>{el.scrollLeft=0;});
      await page.screenshot({path:`/tmp/hpa-preview-${width}.png`,fullPage:true});
    }
    const callsBefore=await page.evaluate(()=>calls.length);
    await page.evaluate(()=>wrapper.hourlyProductivityAnalysis.from_date.set_value('2026-12-31'));
    assert.ok((await page.locator('.hpa-message').innerText()).includes('From Date'));
    assert.equal(await page.evaluate(()=>calls.length),callsBefore);
    await page.evaluate(()=>wrapper.hourlyProductivityAnalysis.from_date.set_value(fixture.from_date));
    await page.evaluate(async()=>{
      let resolveOld;
      const old=new Promise(resolve=>{resolveOld=resolve;});
      const original=frappe.call;let count=0;
      frappe.call=request=>++count===1?old:original(request);
      const first=wrapper.hourlyProductivityAnalysis.load();
      await wrapper.hourlyProductivityAnalysis.load();
      resolveOld({message:{...fixture,site:'STALE-SITE'}});await first;
    });
    assert.ok(!(await page.locator('.hpa-table caption').innerText()).includes('STALE-SITE'));
    await page.evaluate(()=>{frappe.call=async()=>{throw new Error('Access denied');};return wrapper.hourlyProductivityAnalysis.load();});
    assert.equal(await page.locator('.hpa-cell').count(),0);
    assert.ok((await page.locator('.hpa-message').innerText()).includes('Unable to load'));
    assert.deepEqual(errors,[]);
    console.log('PASS: 24-hour matrix, targets, warnings removed, internal investigation navigation, classification colours, 5-category pie, responsive 390/768/1280px, frozen date column, invalid range, stale response, access-error clearing');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
