// Portable local UI integration: synthetic data and Frappe UI boundary doubles only.
// HPS_PLAYWRIGHT may point to playwright-core. HPS_BROWSER_EXECUTABLE is optional.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.HPS_PLAYWRIGHT || 'playwright-core');
const keys=['green','amber','red','bright_red','grey'];
const rows=['2026-09-30','2026-10-01'].map((date,rowIndex)=>{
  const cells=Array.from({length:24},(_,index)=>{
    const start=new Date(date+'T06:00:00Z');start.setUTCHours(start.getUTCHours()+index);
    const end=new Date(start.getTime()+3600000);
    const category=index===18?'red':keys[index%5];
    const actual={green:240,amber:200,red:150,bright_red:0,grey:0}[category];
    return {start:start.toISOString().slice(0,19).replace('T',' '),end:end.toISOString().slice(0,19).replace('T',' '),
      label:start.toISOString().slice(11,16)+'–'+end.toISOString().slice(11,16),category,
      actual_bcm:actual,target_bcm:220,achievement:actual/220*100,excavator_available:index===18?null:1.5,
      completed:true,has_record:index!==3,record_status:index===3?'missing':'recorded',non_working_minutes:category==='grey'?60:0};
  });
  Object.assign(cells[0],{category:'bright_red',actual_bcm:0,achievement:0,non_working_minutes:30,
    segments:[{start_percent:0,end_percent:50,category:'grey'},{start_percent:50,end_percent:100,category:'bright_red'}]});
  return {date,day:rowIndex?'Thu':'Wed',month:rowIndex?'October 2026':'September 2026',month_key:date.slice(0,7),plan_name:'SYNTHETIC',cells,
    load_times:{Day:[{excavator:'EX1',first_load_time:'06:10',last_load_time:'17:55'}],
      Night:[{excavator:'EX1',first_load_time:'18:10',last_load_time:'05:50'}]}};
});
const counts=Object.fromEntries(keys.map(key=>[key,0]));
for(const cell of rows.flatMap(row=>row.cells)) {
  if(cell.segments)for(const segment of cell.segments)counts[segment.category]+=(segment.end_percent-segment.start_percent)/100;
  else counts[cell.category]++;
}
const fixture={site:'Synthetic Koppie',from_date:'2026-09-30',to_date:'2026-10-01',rows,
  hours:rows[0].cells.map(cell=>cell.label),warnings:[],pie:{total_hours:48,
    categories:Object.fromEntries(keys.map(key=>[key,{hours:counts[key],percent:counts[key]/48*100}]))}};
const luminance=rgb=>rgb.map(channel=>channel/255).map(value=>value<=.04045?value/12.92:((value+.055)/1.055)**2.4)
  .reduce((total,value,index)=>total+value*[.2126,.7152,.0722][index],0);
const contrast=(foreground,background)=>{
  const values=[luminance(foreground),luminance(background)].sort((a,b)=>b-a);
  return (values[0]+.05)/(values[1]+.05);
};

(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.HPS_BROWSER_EXECUTABLE?{executablePath:process.env.HPS_BROWSER_EXECUTABLE}:{})});
  try{
    const page=await browser.newPage({viewport:{width:1280,height:1000},locale:'en-US',hasTouch:true});
    page.setDefaultTimeout(5000);
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.setContent('<html><body style="margin:0;padding:12px;font-family:Arial"><main></main></body></html>');
    await page.evaluate(data=>{
      // Small jQuery-shaped DOM boundary; the report/controller under test are unmodified.
      const wrap=nodes=>{
        const list=Array.from(nodes);const api={length:list.length};list.forEach((node,index)=>{api[index]=node;});
        api.find=selector=>wrap(list.flatMap(node=>Array.from(node.querySelectorAll(selector))));
        api.html=value=>{if(value===undefined)return list[0]?.innerHTML;list.forEach(node=>{node.innerHTML=value;});return api;};
        api.attr=(name,value)=>{if(value===undefined)return list[0]?.getAttribute(name);list.forEach(node=>node.setAttribute(name,value));return api;};
        api.replaceWith=html=>{list.forEach(node=>{node.insertAdjacentHTML('beforebegin',html);node.remove();});return api;};
        api.on=(events,selector,callback)=>{for(const eventName of events.split(' '))for(const node of list)node.addEventListener(eventName,event=>{
          const target=event.target.closest(selector);if(!target||!node.contains(target))return;
          callback({type:event.type,currentTarget:target,relatedTarget:event.relatedTarget,originalEvent:event,key:event.key,preventDefault:()=>event.preventDefault()});
        });return api;};
        return api;
      };
      window.fixture=data;window.calls=[];window.wrapper={};
      window.frappe={pages:{'hourly-productivity-analysis':{}},ui:{
        make_app_page:()=>({main:wrap([document.querySelector('main')])}),
        form:{make_control:({parent,df})=>{
          const label=document.createElement('label');label.textContent=df.label;
          const input=document.createElement('input');input.style.cssText='width:100%;box-sizing:border-box;height:36px';
          label.append(input);parent[0].append(label);
          return {get_value:()=>input.value,set_value:async value=>{input.value=value;await df.onchange();}};
        }}},call:async request=>{calls.push(request);return {message:request.method.endsWith('get_defaults')?
          {from_date:data.from_date,to_date:data.to_date}:data};}};
    },fixture);
    await page.addScriptTag({path:path.join(__dirname,'hourly_productivity_analysis.js')});
    await page.evaluate(()=>frappe.pages['hourly-productivity-analysis'].on_page_load(wrapper));
    await page.waitForFunction(()=>!wrapper.hourlyProductivityAnalysis.initializing);
    await page.evaluate(()=>wrapper.hourlyProductivityAnalysis.site.set_value('Synthetic Koppie'));
    assert.equal(await page.locator('.hpa-cell').count(),48);
    assert.equal(await page.locator('.hpa-month-divider').count(),2);
    for(const rgb of [[0,176,80],[255,159,28],[255,224,102],[245,245,245],[255,255,255]])assert.ok(contrast([23,42,26],rgb)>=4.5);
    assert.ok(contrast([255,255,255],[214,60,75])>=4.5);
    const colours={amber:'rgb(255, 159, 28)',red:'rgb(255, 224, 102)',bright_red:'rgb(214, 60, 75)'};
    for(const [key,rgb] of Object.entries(colours)) {
      assert.equal(await page.locator(`td.hpa-${key}`).last().evaluate(el=>getComputedStyle(el).backgroundColor),rgb);
      assert.ok((await page.locator('.hpa-pie').evaluate(el=>getComputedStyle(el).backgroundImage)).includes(rgb));
    }
    for(const theme of ['light','dark']) {
      await page.emulateMedia({colorScheme:theme});
      await page.evaluate(theme=>{
        document.documentElement.style.setProperty('--card-bg',theme==='dark'?'#171717':'#fff');
        document.documentElement.style.setProperty('--text-color',theme==='dark'?'#fff':'#243447');
        document.body.style.background=theme==='dark'?'#171717':'#fff';
      },theme);
      for(const width of [320,390,600,601,768,1280]) {
        await page.setViewportSize({width,height:1000});
        assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
        assert.equal(await page.locator('.hpa-mobile').isVisible(),width<=600);
        assert.equal(await page.locator('.hpa-scroll').isVisible(),width>600);
        if(width<=600) {
          assert.equal(await page.locator('.hpa-mobile-cell').count(),24);
          assert.equal(await page.locator('.hpa-mobile-grid').count(),2);
          assert.equal(await page.locator('.hpa-key-item').count(),5);
          const first=page.locator('.hpa-mobile-cell').first();
          const bounds=await first.boundingBox();assert.ok(bounds.width>=44&&bounds.height>=44);
          await first.tap();
          assert.ok((await page.locator('.hpa-mobile-detail').innerText()).includes('30 minutes'));
          assert.ok((await page.locator('.hpa-mobile-detail').innerText()).includes('06:10'));
          const before=await page.evaluate(()=>calls.length);
          await page.locator('.hpa-mobile-date').selectOption('1');
          const midnight=page.locator('.hpa-mobile-cell[data-hpa-hour="18"]');
          await midnight.tap();
          const detail=await page.locator('.hpa-mobile-detail').innerText();
          assert.ok(detail.includes('2026-10-02 · 00:00–01:00'));
          assert.ok(detail.includes('Not available'));
          assert.ok(detail.includes('18:10')&&detail.includes('05:50'));
          await page.locator('.hpa-mobile-cell[data-hpa-hour="3"]').focus();await page.keyboard.press('Enter');
          assert.ok((await page.locator('.hpa-mobile-detail').innerText()).includes('No production record'));
          assert.equal(await page.evaluate(()=>calls.length),before,'Phone navigation must reuse loaded data');
          for(const [key,rgb] of Object.entries(colours))assert.equal(await page.locator(`.hpa-mobile-cell.hpa-${key}`).last().evaluate(el=>getComputedStyle(el).backgroundColor),rgb);
          const backing=page.locator('.hpa-mobile-mixed .hpa-mobile-text').first();
          assert.equal(await backing.evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(255, 255, 255)');
          assert.equal(await backing.evaluate(el=>getComputedStyle(el).color),'rgb(23, 42, 26)');
          if(process.env.HPA_SCREENSHOT_DIR&&width===390){fs.mkdirSync(process.env.HPA_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.HPA_SCREENSHOT_DIR,`analysis-phone-${theme}.png`),fullPage:true});}
          await page.locator('.hpa-mobile-date').selectOption('0');
        } else {
          const before=await page.locator('tbody .hpa-sticky-date').first().boundingBox();
          await page.locator('.hpa-scroll').evaluate(el=>{el.scrollLeft=600;});
          const after=await page.locator('tbody .hpa-sticky-date').first().boundingBox();
          assert.ok(Math.abs(before.x-after.x)<1);
          await page.locator('.hpa-scroll').evaluate(el=>{el.scrollLeft=0;});
        }
      }
    }
    await page.mouse.move(5,5);await page.mouse.move(30,20,{steps:3});
    await page.locator('td.hpa-cell').first().hover();
    await page.locator('.hpa-hour-card').waitFor({state:'visible'});
    assert.ok(await page.locator('.hpa-hour-card').isVisible());
    await page.keyboard.press('Escape');assert.ok(await page.locator('.hpa-hour-card').isHidden());
    const callsBefore=await page.evaluate(()=>calls.length);
    await page.evaluate(()=>wrapper.hourlyProductivityAnalysis.from_date.set_value('2026-12-31'));
    assert.equal(await page.evaluate(()=>calls.length),callsBefore);
    assert.equal(await page.locator('.hpa-mobile-cell').count(),0);
    await page.evaluate(()=>wrapper.hourlyProductivityAnalysis.from_date.set_value(fixture.from_date));
    await page.evaluate(async()=>{
      const original=frappe.call;let resolveOld;const old=new Promise(resolve=>{resolveOld=resolve;});let count=0;
      frappe.call=request=>++count===1?old:original(request);
      const first=wrapper.hourlyProductivityAnalysis.load();await wrapper.hourlyProductivityAnalysis.load();
      resolveOld({message:{...fixture,site:'STALE-SITE'}});await first;frappe.call=original;
    });
    assert.ok(!(await page.locator('.hpa-table caption').innerText()).includes('STALE-SITE'));
    await page.evaluate(()=>{frappe.call=async()=>({message:{rows:[],planning_error:{title:'Monthly Production Planning Not Found',issues:[{message:'No permitted plan'}]}}});return wrapper.hourlyProductivityAnalysis.load();});
    assert.equal(await page.locator('.hpa-mobile-cell').count(),0);assert.equal(await page.locator('.hpa-pie').count(),0);
    await page.evaluate(()=>{frappe.call=async()=>{throw new Error('Access denied');};return wrapper.hourlyProductivityAnalysis.load();});
    assert.ok((await page.locator('.hpa-message').innerText()).includes('Unable to load'));
    assert.equal(await page.locator('.hpa-cell,.hpa-mobile-cell').count(),0);
    assert.deepEqual(errors,[]);
    console.log('PASS: both shifts/24 hours; small key; approved palette; date/tap/keyboard; overnight dates; missing/unknown; partial hour; no extra fetches; 320/390/600/601/768/1280px; light/dark; desktop frozen columns/hover; invalid range; stale response; plan/access-error clearing. Synthetic data only.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
