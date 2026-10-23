// Local fixture integration only: no live site, credentials, or source data writes.
// HPS_PLAYWRIGHT=/path/to/playwright-core HPA_FIXTURE=/path/to/report.json node this-file
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.HPS_PLAYWRIGHT || 'playwright-core');
const fixture = JSON.parse(fs.readFileSync(process.env.HPA_FIXTURE, 'utf8'));

(async () => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  try {
    const page = await browser.newPage({hasTouch:true});
    page.setDefaultTimeout(5000);
    const errors = [];
    page.on('pageerror',e=>errors.push(e.message));
    await page.setContent('<html><body style="margin:0;padding:12px;font-family:Arial"><main id="main"></main></body></html>');
    await page.addScriptTag({path:path.resolve(__dirname,'../../../../../frappe/node_modules/jquery/dist/jquery.js')});
    await page.evaluate(data => {
      window.fixture=data;window.wrapper={};window.calls=[];
      window.frappe={pages:{'hourly-productivity-analysis':{}},
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
    await page.addScriptTag({path:path.join(__dirname,'hourly_productivity_analysis.js')});
    await page.evaluate(()=>frappe.pages['hourly-productivity-analysis'].on_page_load(wrapper));
    await page.waitForFunction(()=>wrapper.hourlyProductivityAnalysis.initializing===false);
    await page.evaluate(()=>wrapper.hourlyProductivityAnalysis.site.set_value('Koppie'));
    assert.equal(await page.locator('.hpa-cell').count(),fixture.rows.length*24);
    assert.equal(await page.locator('.hpa-legend>div').count(),5);
    assert.equal(await page.locator('.hpa-hour').count(),24);
    for (const width of [390,768,1280]) {
      await page.setViewportSize({width,height:1000});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      if (width === 390) {
        assert.ok(await page.locator('.hpa-mobile').isVisible());
        assert.equal(await page.locator('.hpa-mobile-cell').count(),24);
        assert.equal(await page.locator('.hpa-mobile-grid').count(),2);
        assert.equal(await page.locator('.hpa-key-item').count(),5);
      } else {
        const before=await page.locator('tbody .hpa-sticky-date').first().boundingBox();
        await page.locator('.hpa-scroll').evaluate(el=>{el.scrollLeft=700;});
        const after=await page.locator('tbody .hpa-sticky-date').first().boundingBox();
        assert.ok(Math.abs(before.x-after.x)<1,JSON.stringify({before,after}));
        await page.locator('.hpa-scroll').evaluate(el=>{el.scrollLeft=0;});
      }
      await page.screenshot({path:`/tmp/hpa-preview-${width}.png`,fullPage:true});
    }
    const partial = fixture.rows.flatMap(row=>row.cells).findIndex(cell=>cell.segments?.length > 1);
    assert.ok(partial >= 0, 'Fixture must exercise a partial-hour cell');
    const partialCell = page.locator('.hpa-cell').nth(partial);
    assert.ok((await partialCell.evaluate(el=>getComputedStyle(el).backgroundImage)).includes('linear-gradient'));
    assert.equal(await partialCell.getAttribute('title'),null);
    await partialCell.hover();
    const card = page.getByRole('dialog', {name:'Hourly production details'});
    await card.waitFor({state:'visible'});
    assert.ok((await card.innerText()).includes('30 minutes'));
    for (const label of ['Actual Production','Production Target','Target Achieved','Excavators Available','Recording Status']) {
      assert.ok((await card.innerText()).includes(label), label);
    }
    await card.hover();
    await page.waitForTimeout(250);
    assert.ok(await card.isVisible(), 'Card remains open while reading it');
    await page.keyboard.press('Escape');
    assert.ok(await card.isHidden());
    assert.equal(await partialCell.getAttribute('aria-expanded'),'false');
    await partialCell.focus();
    await page.keyboard.press('Enter');
    assert.ok(await card.isVisible());
    await page.getByRole('button',{name:'Close hourly details'}).click();
    assert.ok(await card.isHidden());
    await page.setViewportSize({width:390,height:700});
    await page.locator('.hpa-mobile-date').selectOption(String(Math.floor(partial/24)));
    const phoneCell=page.locator(`.hpa-mobile-cell[data-hpa-hour="${partial%24}"]`);
    await phoneCell.tap();
    assert.equal(await phoneCell.getAttribute('aria-pressed'),'true');
    assert.ok((await page.locator('.hpa-mobile-detail').innerText()).includes('30 minutes'));
    assert.ok(await card.isHidden(), 'Phone uses inline detail rather than a floating popup');
    await page.screenshot({path:'/tmp/hpa-hover-mobile.png'});
    await page.setViewportSize({width:1280,height:1000});
    await page.mouse.move(5,5);await page.mouse.move(30,20,{steps:3});
    await partialCell.hover();
    await card.waitFor({state:'visible'});
    await page.screenshot({path:'/tmp/hpa-hover-desktop.png',fullPage:true});
    await page.locator('.hpa-scroll').evaluate(el=>{el.scrollLeft+=100;});
    await card.waitFor({state:'hidden'});
    assert.ok(!(await page.locator('.hpa-content').innerText()).includes('Pie percentage ='));
    const expectedActual = fixture.rows.flatMap(row=>row.cells)[partial].actual_bcm;
    assert.equal((await partialCell.innerText()).replace(/[*†]/g,''),
      Number(expectedActual).toLocaleString(undefined,{maximumFractionDigits:1}));
    await page.evaluate(async()=>{
      const original=frappe.call;
      frappe.call=async()=>({message:{rows:[],planning_error:{title:'Monthly Production Planning Not Found',issues:[
        {message:'No accessible production plan was found for Koppie for September 2026.'},
        {message:'No accessible production plan was found for Koppie for October 2026.'}]}}});
      await wrapper.hourlyProductivityAnalysis.load();
      frappe.call=original;
    });
    assert.equal(await page.locator('.hpa-cell').count(),0);
    assert.equal(await page.locator('.hpa-pie').count(),0);
    assert.ok((await page.locator('.hpa-message').innerText()).includes('September 2026'));
    assert.ok((await page.locator('.hpa-message').innerText()).includes('October 2026'));
    await page.evaluate(()=>wrapper.hourlyProductivityAnalysis.load());
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
    console.log('PASS: desktop 24-hour matrix, phone Day/Night heatmap and inline details, 5-category pie/key, responsive 390/768/1280px, frozen date column, hover/tap/keyboard, partial-hour gradient/BCM, missing-month message, invalid range, stale response, access-error clearing');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
