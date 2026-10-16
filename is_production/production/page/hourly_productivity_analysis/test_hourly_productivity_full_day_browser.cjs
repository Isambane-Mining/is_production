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
      label:start.toISOString().slice(11,16)+'â€“'+end.toISOString().slice(11,16),category,
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
const hours = Array.from({length:12}, (_, index) => {
  const target = index === 0 ? 110 : index === 11 ? 330 : 440;
  const actual = index % 2 ? 0 : target;
  return {label:`${String(6 + index).padStart(2,'0')}:00`, record_status:index % 2 ? 'missing' : 'recorded',
    actual_bcm:actual, target_bcm:target, achievement:actual / target * 100,
    available:2.5, utilised:2, difference:.5, excavator_available:1.5, excavator_utilised:1,
    excavator_unavailable:.5, excavator_pbm_count:1, unpaired_available:0, unpaired_utilised:0,
    unpaired_unavailable:0, presentation_colour:actual ? 'green' : 'red',
    presentation_segments:[{start_percent:0,end_percent:100,category:actual ? 'green' : 'red'}]};
});
Object.assign(hours[2], {fatigue_minutes:30,presentation_segments:[
  {start_percent:0,end_percent:50,category:'blue'}, {start_percent:50,end_percent:100,category:'green'}]});
Object.assign(hours[3], {non_working_minutes:60,presentation_colour:'grey',
  presentation_segments:[{start_percent:0,end_percent:100,category:'grey'}]});
Object.assign(hours[5], {fatigue_minutes:30,presentation_segments:[
  {start_percent:0,end_percent:50,category:'blue'}, {start_percent:50,end_percent:100,category:'red'}]});
Object.assign(hours[6], {fatigue_minutes:60,presentation_colour:'blue',
  presentation_segments:[{start_percent:0,end_percent:100,category:'blue'}]});
Object.assign(hours[7], {fatigue_minutes:30,non_working_minutes:30,presentation_colour:'blue',presentation_segments:[
  {start_percent:0,end_percent:50,category:'blue'}, {start_percent:50,end_percent:100,category:'grey'}]});
const target = hours.reduce((total,hour)=>total + hour.target_bcm,0);
const actual = hours.reduce((total,hour)=>total + hour.actual_bcm,0);
const summaryFixture = {plan_name:'Synthetic LAB preview',production_excavator_count:2,warnings:[
  "MPP specifies 9.0 working hours for 2026-06-16 Day, without clock times. Only configured fatigue is shaded; the shorter shift's timing is unknown.",
  'Unpaired MPP excavators are production/spare unknown: the original MPP stores empty production assignments in browser localStorage, not on these rows. They are not labelled as confirmed spares.',
  'ADT fleet uses current Asset site assignments; historical transfers are not reconstructed.'],hours,
  pie:{target,achieved:actual,availability_estimate:352,remaining:target - actual - 352},
  summary:{actual_bcm:actual,target_bcm:target,achievement:actual / target * 100,
    completed_actual_bcm:actual,completed_target_bcm:target,average_available:2.5,adts_utilised:2,missing_hours:6},
  excavators:[{excavator:'EX1',first_load_time:'06:15',last_load_time:'17:45'},
    {excavator:'EX2',first_load_time:null,last_load_time:null}],
  production_excavators:[{label:'EX1',actual_bcm:1200},{label:'EX2',actual_bcm:actual-1200}],
  unpaired_excavators:[{label:'EX3',actual_bcm:0,classification:'Production/spare unknown'}],
  unplanned_excavators:[{label:'Unassigned',actual_bcm:0}],details:[]};


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
    await page.setContent('<html><head><base href="https://lab.test/"></head><body style="margin:0;padding:12px;font-family:Arial"><main></main></body></html>');
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
      window.fixture=data;window.calls=[];window.wrapper={};window.appPages=0;
      window.frappe={pages:{'hourly-productivity-analysis':{},'hourly-productivity-summary':{}},ui:{
        make_app_page:()=>{appPages++;return {main:wrap([document.querySelector('main')])};},
        form:{make_control:({parent,df})=>{
          const label=document.createElement('label');label.textContent=df.label;
          const input=document.createElement('input');input.style.cssText='width:100%;box-sizing:border-box;height:36px';
          label.append(input);parent[0].append(label);
          return {get_value:()=>input.value,set_value:async value=>{input.value=value;await df.onchange();}};
        }}},call:async request=>{calls.push(request);return {message:request.method.endsWith('get_defaults')?
          {from_date:data.from_date,to_date:data.to_date,date:data.from_date,shift:'Day'}:request.method.endsWith('get_summary')?window.shiftFixtures[request.args.shift]:data};}};
    },fixture);
    await page.evaluate(data=>{window.summaryFixture=data;},summaryFixture);
    await page.evaluate(()=>{
      window.shiftFixtures={};
      for(const [shift,actual] of [['Day',1],['Night',2]]){
        const fixture=structuredClone(summaryFixture);
        fixture.hours.forEach((h,i)=>{h.actual_bcm=actual;h.achievement=actual/h.target_bcm*100;h.record_status='recorded';h.label=String((i+(shift==='Day'?6:18))%24).padStart(2,'0')+':00–'+String((i+(shift==='Day'?7:19))%24).padStart(2,'0')+':00';h.presentation_colour='red';h.presentation_segments=h.presentation_segments.map(segment=>({...segment,category:segment.category==='green'?'red':segment.category}));});
        fixture.summary.actual_bcm=actual*12;fixture.summary.missing_hours=0;
        fixture.summary.completed_actual_bcm=actual*12;fixture.summary.achievement=actual*12/fixture.summary.completed_target_bcm*100;
        fixture.pie.achieved=actual*12;
        window.shiftFixtures[shift]=fixture;
      }
    });
    let assetLoads=0;
    await page.route('**/assets/is_production/js/hourly_productivity_summary_view.js*',route=>{
      assetLoads++;return route.fulfill({contentType:'application/javascript',body:fs.readFileSync(path.resolve(__dirname,'../../../public/js/hourly_productivity_summary_view.js'),'utf8')});
    });
    await page.addScriptTag({path:path.join(__dirname,'hourly_productivity_analysis.js')});
    await page.evaluate(()=>frappe.pages['hourly-productivity-analysis'].on_page_load(wrapper));
    await page.waitForFunction(()=>!wrapper.hourlyProductivityAnalysis.initializing);
    await page.evaluate(()=>wrapper.hourlyProductivityAnalysis.site.set_value('Synthetic Koppie'));
    assert.equal(await page.evaluate(()=>appPages),1);
    assert.equal(assetLoads,0);
    assert.equal(await page.locator('[role=tab][aria-selected=true]').innerText(),'Hourly Productivity Analysis');
    await page.locator('#hpt-summary-tab').click();
    await page.waitForFunction(()=>wrapper.hourlyProductivitySummary && !wrapper.hourlyProductivitySummary.initializing);
    assert.equal(assetLoads,1);
    await page.evaluate(async()=>{
      await wrapper.hourlyProductivitySummary.site.set_value('Summary Site');
      await wrapper.hourlyProductivitySummary.shift.set_value('Night');
    });
    assert.ok((await page.locator('.hps-kpi').first().innerText()).includes('24'));
    // Desktop has only the selected-shift calls. Narrowing fetches the companion once.
    const before=await page.evaluate(()=>calls.length);
    await page.setViewportSize({width:390,height:1100});
    await page.waitForFunction(()=>document.querySelectorAll('.hps-day-hour').length===24);
    assert.equal(await page.evaluate(()=>calls.length),before+1);
    assert.equal(await page.locator('.hps-day-hour[data-hps-shift=Day]').count(),12);
    assert.equal(await page.locator('.hps-day-hour[data-hps-shift=Night]').count(),12);
    const kpis=await page.locator('.hps-kpis').innerText();
    const pie=await page.locator('.hps-pie').getAttribute('aria-label');
    await page.locator('.hps-day-hour[data-hps-shift=Night][data-hps-hour="6"]').click();
    assert.ok((await page.locator('.hps-day-detail').innerText()).includes('2026-10-01'));
    assert.ok((await page.locator('.hps-day-detail').innerText()).includes('00:00'));
    await page.locator('.hps-day-hour[data-hps-shift=Day][data-hps-hour="0"]').focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('.hps-day-hour[data-hps-shift=Day][data-hps-hour="0"]').getAttribute('aria-pressed'),'true');
    assert.equal(await page.locator('.hps-kpis').innerText(),kpis);
    assert.equal(await page.locator('.hps-pie').getAttribute('aria-label'),pie);
    const after=await page.evaluate(()=>calls.length);
    for(const width of [320,390,600,601,768,1440,390]){
      await page.setViewportSize({width,height:1100});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      assert.equal(await page.locator('.hps-mobile-hours').isVisible(),width<=600);
      assert.equal(await page.locator('.hps-desktop-hours').isVisible(),width>600);
    }
    assert.equal(await page.evaluate(()=>calls.length),after);
    await page.evaluate(()=>{
      window.originalCall=frappe.call;window.denyDay=true;window.deferDay=false;
      frappe.call=async request=>{
        if(request.method.endsWith('get_summary') && request.args.shift==='Day'){
          if(window.denyDay)throw new Error('Permission denied');
          if(window.deferDay)return await new Promise(resolve=>{window.releaseDay=()=>resolve({message:shiftFixtures.Day});});
        }
        return originalCall(request);
      };
    });
    await page.evaluate(()=>wrapper.hourlyProductivitySummary.load());
    assert.equal(await page.locator('.hps-day-hour').count(),12);
    assert.equal(await page.locator('[data-hps-retry]').count(),1);
    assert.equal(await page.locator('.hps-kpis').innerText(),kpis);
    await page.evaluate(()=>{window.denyDay=false;});
    await page.locator('[data-hps-retry]').click();
    await page.waitForFunction(()=>document.querySelectorAll('.hps-day-hour').length===24);
    // Stale companion cannot return old full-day data after filters become incomplete.
    await page.evaluate(()=>{window.deferDay=true;wrapper.hourlyProductivitySummary.load();});
    await page.waitForFunction(()=>typeof releaseDay==='function');
    await page.evaluate(()=>wrapper.hourlyProductivitySummary.site.set_value(''));
    await page.evaluate(()=>releaseDay());
    await page.waitForFunction(()=>!document.querySelector('.hps-day-hour'));
    assert.ok((await page.locator('.hps-content').innerText()).includes('Select a site'));
    // Capture the real rendered phone result, then verify a selected-shift denial clears everything.
    await page.evaluate(async()=>{window.deferDay=false;await wrapper.hourlyProductivitySummary.site.set_value('Summary Site');});
    await page.waitForFunction(()=>document.querySelectorAll('.hps-day-hour').length===24);
    await page.screenshot({path:path.resolve(__dirname,'../../../../../full-day-phone-check.png'),fullPage:true});
    await page.evaluate(async()=>{frappe.call=async()=>{throw new Error('Denied');};await wrapper.hourlyProductivitySummary.load();});
    assert.equal(await page.locator('.hps-day-hour').count(),0);
    assert.equal(await page.locator('.hps-kpi').count(),0);
    assert.ok((await page.locator('.hps-content').innerText()).includes('Unable to load this shift'));
    assert.deepEqual(errors,[]);
    console.log('PASS: selected-shift totals unchanged; full-day companion uses existing endpoint; both shifts; overnight dates; keyboard details; responsive layouts; no duplicate resize fetch; denied companion/retry; stale response; selected denial clears data');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
