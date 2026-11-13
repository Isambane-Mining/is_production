// Local browser verification using the real DataTable, with Frappe boundaries supplied locally.
// DIESEL_PLAYWRIGHT=/path/to/playwright-core node test_diesel_cap_browser.cjs
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const {chromium} = require(process.env.DIESEL_PLAYWRIGHT || 'playwright-core');
const frappeRoot = path.resolve(__dirname, '../../../../../frappe');
const reportDefinition = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../report/diesel_cap_report/diesel_cap_report.json')));
(async () => {
  const browser = await chromium.launch({headless:true, args:['--no-sandbox'], ...(process.env.DIESEL_BROWSER_EXECUTABLE ? {executablePath:process.env.DIESEL_BROWSER_EXECUTABLE} : {})});
  try {
    const page = await browser.newPage({viewport:{width:1600,height:1000}});
    const errors = [];
    page.on('pageerror',error=>errors.push(error.message));
    await page.setContent('<html><head></head><body style="margin:0;padding:24px;font-family:Arial;background:#f7f9fc"><main></main></body></html>');
    await page.addScriptTag({path:path.join(frappeRoot,'node_modules/jquery/dist/jquery.js')});
    await page.addScriptTag({path:path.join(frappeRoot,'node_modules/sortablejs/Sortable.min.js')});
    await page.addScriptTag({path:path.join(frappeRoot,'node_modules/frappe-datatable/dist/frappe-datatable.js')});
    await page.addStyleTag({path:path.join(frappeRoot,'node_modules/frappe-datatable/dist/frappe-datatable.css')});
    await page.addStyleTag({content:'.btn{border:0;border-radius:8px;padding:10px}.btn-primary{background:#0877ed;color:white}input{width:100%;height:36px;box-sizing:border-box;border:1px solid #dfe6ed;border-radius:6px}label{display:block;margin-bottom:8px;font-size:12px}.icon{width:24px;height:24px} :root{--text-color:#0a2340;--text-muted:#65758a;--card-bg:white;--subtle-fg:#f2f5f8}'});
    await page.addStyleTag({path:path.join(__dirname,'diesel_cap.css')});
    await page.evaluate(() => {
      window.__ = (text,args=[]) => text.replace(/\{(\d+)\}/g,(_,i)=>args[i]);
      window.requests=[];
      window.frappe={pages:{'diesel-cap':{}},boot:{lang:'en'},
        datetime:{month_start:()=> '2026-11-01',month_end:()=> '2026-11-30'},
        utils:{icon:()=>'<svg class="icon" viewBox="0 0 24 24"><path d="M5 20V4h10v16M3 20h14M7 6h6v5H7z" fill="none" stroke="currentColor"/></svg>',
          is_rtl:()=>false,datatable:{get_translations:()=>({})}},
        format:(value,column)=> column.fieldtype==='Float' ? Number(value||0).toLocaleString('en-US',{minimumFractionDigits:column.precision,maximumFractionDigits:column.precision}) : $('<span>').text(value||'').html(),
        require:async()=>{},
        ui:{make_app_page:()=>({main:$('main'),add_menu_item:()=>{}}),form:{make_control:({parent,df})=>{
          const input=$('<input>').attr('data-field',df.fieldname);
          parent.append($('<label>').text(df.label),input);
          input.on('change',()=>df.onchange());
          return {get_value:()=>input.val(),set_value:async value=>{input.val(value);await df.onchange();}};
        }}},
        call:async args=>{requests.push(args);if(window.fail)throw Error('Denied');return {message:window.report};}
      };
      window.report={columns:[
        {fieldname:'site',label:'Site',fieldtype:'Link',options:'Location',width:180},
        ...['start_date','end_date'].map(fieldname=>({fieldname,label:fieldname,fieldtype:'Date',width:110})),
        ...['survey_base_bcm','hourly_after_survey_bcm','month_to_date_bcm','selected_period_bcm','total_tallies_cubes','total_diesel_litres','diesel_cap','tallies_fuel_cap'].map(fieldname=>({fieldname,label:fieldname,fieldtype:'Float',precision:fieldname==='total_diesel_litres'?1:2,width:150,hidden:fieldname==='selected_period_bcm'?1:0})),
        {fieldname:'survey_date_used',label:'Survey Date Used',fieldtype:'Datetime',width:160}],
        result:[{site:'Koppie',month_to_date_bcm:600,selected_period_bcm:100,total_tallies_cubes:50,total_diesel_litres:200,diesel_cap:2,tallies_fuel_cap:4,survey_date_used:'2026-11-01 06:00:00'},
          {site:'Kriel',month_to_date_bcm:1400,selected_period_bcm:900,total_tallies_cubes:450,total_diesel_litres:900,diesel_cap:1,tallies_fuel_cap:2}],execution_time:0.123};
    });
    await page.addScriptTag({path:path.join(__dirname,'diesel_cap.js')});
    await page.evaluate(async()=>{window.wrapper={};frappe.pages['diesel-cap'].on_page_load(wrapper);const render=wrapper.diesel_cap.render.bind(wrapper.diesel_cap);wrapper.diesel_cap.render=(...args)=>{try{return render(...args);}catch(error){window.renderError=error.stack;throw error;}};await wrapper.diesel_cap.ready;});
    assert.equal(await page.evaluate(()=>window.renderError||null),null);
    assert.equal(await page.locator('[data-kpi="month_to_date_bcm"]').textContent(),'2,000.00');
    assert.equal(await page.locator('[data-kpi="diesel_cap"]').textContent(),'1.10');
    assert.equal(await page.locator('[data-kpi="total_diesel_litres"]').textContent(),'1,100.0');
    assert.equal(await page.locator('[data-field="start_date"]').inputValue(),'2026-11-01');
    assert.equal(await page.locator('.dc-execution').textContent(),'Execution Time: 0.123 sec');
    assert.equal(await page.evaluate(()=>wrapper.diesel_cap.datatable.options.columns.length),11);
    assert.equal(await page.evaluate(()=>requests[0].args.filters.end_date),'2026-11-30');
    await page.waitForTimeout(150);
    await page.screenshot({path:'/tmp/diesel-cap-desktop.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= window.innerWidth),true,'table must scroll within the page on mobile');
    await page.screenshot({path:'/tmp/diesel-cap-mobile.png',fullPage:true});
    await page.evaluate(async()=>{report.result=[];await wrapper.diesel_cap.refresh();});
    assert.match(await page.locator('.dc-status').textContent(),/No data/);
    assert.equal(await page.locator('[data-kpi="diesel_cap"]').textContent(),'0.00');
    await page.evaluate(async()=>{window.fail=true;await wrapper.diesel_cap.refresh();});
    assert.match(await page.locator('.dc-status').textContent(),/Unable/);
    assert.equal(await page.locator('[data-kpi="diesel_cap"]').textContent(),'—');
    assert.equal(await page.locator('.dc-refresh').isEnabled(),true);
    await page.evaluate(async()=>{window.fail=false;await wrapper.diesel_cap.controls.start_date.set_value('');});
    assert.match(await page.locator('.dc-status').textContent(),/required/);
    if(process.env.DIESEL_REPORT_FIXTURE) {
      const fixture=JSON.parse(fs.readFileSync(process.env.DIESEL_REPORT_FIXTURE));
      await page.evaluate(async data=>{report=data;await wrapper.diesel_cap.controls.start_date.set_value('2026-01-01');},fixture);
      assert.deepEqual(await page.evaluate(()=>wrapper.diesel_cap.datatable.options.data),fixture.result.map(row=>fixture.columns.filter(column=>!column.hidden).map(column=>row[column.fieldname]??null)));
    }
    const pageDefinition=JSON.parse(fs.readFileSync(path.join(__dirname,'diesel_cap.json')));
    assert.deepEqual(pageDefinition.roles,reportDefinition.roles);
    assert.deepEqual(errors,[]);
    console.log('PASS: DataTable rendering, report precision, defaults, KPI ratios, desktop/mobile bounds, empty/error states and report roles.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
