// Synthetic boundary integration and exact report-region visual regression.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const playwright=process.env.HPS_PLAYWRIGHT||'playwright-core';
const {chromium}=require(playwright);
const lib=path.dirname(require.resolve(playwright));
const {PNG}=require(path.join(lib,'lib/utilsBundle'));
const {compare}=require(path.join(lib,'lib/server/utils/image_tools/compare'));
const root=path.resolve(__dirname,'../../..');
const baseline=process.env.HP_BASELINE||'/tmp/hp-structure-baselines';
const summary=JSON.parse(fs.readFileSync(path.join(baseline,'hps-dashboard.json'),'utf8'));
const analysis=JSON.parse(fs.readFileSync(path.join(baseline,'hpa-fixture.json'),'utf8'));


(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.HPS_CHROMIUM?{executablePath:process.env.HPS_CHROMIUM}:{}),args:['--no-sandbox']});
 const errors=[];
 try {
  const page=await browser.newPage();page.on('pageerror',e=>errors.push(e.message));
  async function setup(tab, defer=false) {
   await page.setContent('<html><body style="margin:0;padding:12px;font-family:Arial"><main id="main"></main></body></html>');
   await page.addScriptTag({path:path.resolve(__dirname,'../../../../../frappe/node_modules/jquery/dist/jquery.js')});
   await page.addStyleTag({path:path.join(root,'public/css/dashboards.css')});
   await page.evaluate(({summary,analysis,tab,defer})=>{
    window.wrapper={};window.calls=[];window.assets=[];
    window.frappe={pages:{'hourly-productivity':{}},route_options:tab?{tab}:{},
     require:async asset=>{assets.push(asset);},
     ui:{make_app_page:()=>({main:$('#main')}),form:{make_control:({parent,df})=>{
      const input=df.fieldtype==='Select'?$('<select>'):$('<input>');
      if(df.fieldtype==='Select')df.options.split('\n').forEach(value=>input.append($('<option>').text(value)));
      parent.append($('<label>').text(df.label),input);
      input.css({width:'100%',boxSizing:'border-box',height:'32px'});
      return {get_value:()=>input.val(),set_value:async value=>{input.val(value);await df.onchange();}};
     }}},
     call:async request=>{
      calls.push(request);
      if(defer && request.method.endsWith('get_defaults')) {
       defer=false;return new Promise(resolve=>{window.releaseDefaults=()=>resolve({message:{date:summary.from_date,shift:'Full Day'}});});
      }
      if(request.method.endsWith('get_defaults'))return {message:request.method.includes('hourly_productivity_analysis')?
       {from_date:analysis.from_date,to_date:analysis.to_date}:{date:summary.from_date,shift:'Full Day'}};
      if(window.deferData && request.method.endsWith('get_dashboard')) {
       window.deferData=false;return new Promise(resolve=>{window.releaseData=()=>resolve({message:summary});});
      }
      return {message:request.method.endsWith('get_analysis')?analysis:summary};
     }};
   },{summary,analysis,tab,defer});
   await page.addScriptTag({path:path.join(__dirname,'hourly_productivity.js')});
   await page.evaluate(()=>frappe.pages['hourly-productivity'].on_page_load(wrapper));
   if(!defer) await page.evaluate(()=>wrapper.hourlyProductivity.ready);
  }
  const host=()=>page.evaluate(()=>({active:wrapper.hourlyProductivity.activeTab,
   filters:Object.fromEntries(Object.entries(wrapper.hourlyProductivity.controllers).map(([tab,c])=>
    [tab,Object.fromEntries(['site','from_date','to_date'].map(name=>[name,c[name].get_value()]))])),calls:calls.length}));
  await setup();
  assert.equal((await host()).active,'summary');
  assert.equal(await page.locator('[role=tab]').count(),2);
  assert.deepEqual(await page.evaluate(()=>assets),[],'No extra runtime bundle is requested');
  assert.equal(await page.locator('.hpa-shell').count(),0,'Second tab is lazy');
  assert.equal(await page.locator('style').count(),1,'Only the externally loaded application stylesheet');
  await page.evaluate(async()=>{
   const s=wrapper.hourlyProductivity.controllers.summary;
   await s.to_date.set_value('2026-10-13');await s.site.set_value('Koppie');s.navigate(1);
   await wrapper.hourlyProductivity.show('analysis');
  });
  let state=await host();
  assert.deepEqual(state.filters.analysis,state.filters.summary,'First opening synchronises three filters');
  assert.equal(await page.locator('.hpa-cell').count(),48);
  const dashboardMethods=await page.evaluate(()=>calls.filter(request=>request.method.endsWith('get_dashboard')).map(request=>request.method));
  assert.ok(dashboardMethods.length>0);
  assert.ok(dashboardMethods.every(method=>method==='is_production.production.page.hourly_productivity_summary.hourly_productivity_summary.get_dashboard'),
    'Summary calls its standard Page endpoint directly');
  await page.evaluate(async()=>{
   const a=wrapper.hourlyProductivity.controllers.analysis;
   await a.site.set_value('Gwab');await a.from_date.set_value('2026-10-01');
  });
  const count=(await host()).calls;
  await page.evaluate(()=>wrapper.hourlyProductivity.show('summary'));
  assert.equal((await host()).calls,count,'Switching does not reload');
  assert.equal((await host()).filters.summary.site,'Koppie');
  assert.ok((await page.locator('.hps-selected-date').innerText()).includes('2026-10-13'),'Daily navigation preserved');
  await page.evaluate(()=>wrapper.hourlyProductivity.show('analysis'));
  assert.equal((await host()).filters.analysis.site,'Gwab');
  assert.equal((await host()).filters.analysis.from_date,'2026-10-01');
  assert.equal((await host()).calls,count);
  await page.locator('.hpa-investigate').click();
  await page.waitForFunction(()=>wrapper.hourlyProductivity.activeTab==='summary');
  assert.equal((await host()).calls,count,'Investigation switches to cached Summary');
  // Restore original report filters and daily selection solely for visual comparison.
  await page.evaluate(async()=>{
   wrapper.hourlyProductivity.controllers.summary.navigate(-1);
   const a=wrapper.hourlyProductivity.controllers.analysis;
   await a.site.set_value('Koppie');await a.from_date.set_value('2026-10-12');
  });
  for(const [tab,prefix] of [['summary','hps'],['analysis','hpa']]) {
   await page.evaluate(tab=>wrapper.hourlyProductivity.show(tab),tab);
   for(const width of [390,768,1280]) {
    await page.setViewportSize({width,height:1000});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No page overflow');
    if(tab==='analysis') {
      await page.locator('.hpa-investigate').focus();
    }
    const dimensions=await page.locator('.'+prefix+'-shell').boundingBox();
    await page.locator('.'+prefix+'-shell').screenshot({path:`/tmp/hp-combined-${prefix}-${width}.png`});
    // Compare the report at its original viewport origin. The added tab strip otherwise
    // shifts native-control rasterisation slightly, without changing the report layout.
    await page.locator('.hp-tabs').evaluate(element=>{element.style.display='none';});
    const aligned=await page.locator('.'+prefix+'-shell').boundingBox();
    assert.equal(aligned.width,dimensions.width);
    assert.equal(aligned.height,dimensions.height);
    await page.evaluate(()=>window.scrollTo(0,0));
    if(tab==='analysis') {
      await page.locator('.hpa-scroll').evaluate(element=>{element.scrollLeft=700;});
      await page.locator('tbody .hpa-sticky-date').first().boundingBox();
      await page.locator('.hpa-scroll').evaluate(element=>{element.scrollLeft=0;});
    }
    if(tab==='analysis' && width===1280) await page.locator('.hpa-investigate').hover();
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const region=await page.locator('.'+prefix+'-shell').screenshot({path:`/tmp/hp-comparison-${prefix}-${width}.png`});
    await page.locator('.hp-tabs').evaluate(element=>{element.style.display='';});
    const original=fs.readFileSync(path.join(baseline,`${prefix}-report-${width}.png`));
    const actualPNG=PNG.sync.read(region),expectedPNG=PNG.sync.read(original);
    assert.equal(actualPNG.width,expectedPNG.width);assert.equal(actualPNG.height,expectedPNG.height);
    // Playwright's image comparator excludes rasterisation-only antialias differences.
    assert.equal(compare(actualPNG.data,expectedPNG.data,null,actualPNG.width,actualPNG.height),0,
      `${tab} ${width}px must have no visual layout/colour differences`);
    console.log(`Visual ${tab} ${width}px: no perceptual differences; byte-identical=${region.equals(original)}`);
    await page.screenshot({path:`/tmp/hp-combined-${tab}-${width}.png`,fullPage:true});
   }
  }
  // Both legacy URLs explicitly select the corresponding cached tab.
  for(const tab of ['summary','analysis']) {
   await page.evaluate(tab=>{frappe.route_options={tab};frappe.pages['hourly-productivity'].on_page_show(wrapper);},tab);
   await page.waitForFunction(tab=>wrapper.hourlyProductivity.activeTab===tab,tab);
  }
  // Initial entry via Analysis reverses the one-time synchronisation direction.
  await setup('analysis');
  assert.equal((await host()).active,'analysis');
  await page.evaluate(async()=>{
   const a=wrapper.hourlyProductivity.controllers.analysis;
   await a.site.set_value('Gwab');await a.from_date.set_value('2026-10-05');await a.to_date.set_value('2026-10-10');
   await wrapper.hourlyProductivity.show('summary');
  });
  state=await host();assert.deepEqual(state.filters.summary,state.filters.analysis);
  assert.equal(await page.evaluate(()=>wrapper.hourlyProductivity.controllers.summary.shift.get_value()),'Full Day');
  await page.evaluate(()=>Promise.all([wrapper.hourlyProductivity.show('analysis'),wrapper.hourlyProductivity.show('summary')]));
  assert.equal((await host()).active,'summary','Latest tab request wins');
  // The second tab must wait for first-tab defaults before taking its initial snapshot.
  await setup('summary',true);
  await page.waitForFunction(()=>typeof releaseDefaults==='function');
  await page.evaluate(async()=>{
   window.deferData=true;window.secondComplete=false;
   window.secondOpening=wrapper.hourlyProductivity.show('analysis').then(()=>{window.secondComplete=true;});
   await wrapper.hourlyProductivity.controllers.summary.site.set_value('Koppie');
  });
  assert.equal(await page.evaluate(()=>Boolean(wrapper.hourlyProductivity.controllers.analysis)),false);
  await page.evaluate(()=>releaseDefaults());
  await page.waitForFunction(()=>secondComplete,{},{timeout:1500});
  assert.equal(await page.evaluate(()=>wrapper.hourlyProductivity.controllers.summary.data),null,'Slow first request must not block alternate tab');
  await page.evaluate(async()=>{releaseData();await wrapper.hourlyProductivity.controllers.summary.ready;});
  state=await host();assert.deepEqual(state.filters.analysis,state.filters.summary);
  assert.equal(state.filters.analysis.site,'Koppie');
  assert.equal(await page.evaluate(()=>calls.filter(r=>r.method.endsWith('get_dashboard')||r.method.endsWith('get_analysis')).length),2);
  await page.locator('[data-tab=analysis]').focus();await page.keyboard.press('Home');
  await page.waitForFunction(()=>wrapper.hourlyProductivity.activeTab==='summary');
  assert.deepEqual(errors,[]);
  console.log('PASS: default order, lazy tabs, bidirectional initial sync, independent filters, cached date navigation, no switch reload, investigation, cached aliases, latest-switch wins, standard Page script/CSS loading, six visual comparisons with unchanged report dimensions');
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
