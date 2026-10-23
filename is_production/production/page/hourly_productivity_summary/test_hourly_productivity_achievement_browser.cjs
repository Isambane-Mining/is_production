// Local rendering regression: synthetic data only; no site login or database access.
// HPS_PLAYWRIGHT may point to playwright-core; HPS_BROWSER_EXECUTABLE is optional.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.HPS_PLAYWRIGHT || 'playwright-core');

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
const fixture = {plan_name:'Synthetic LAB preview',production_excavator_count:2,warnings:[
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

const contrast = (foreground, background) => {
  const luminance = rgb => rgb.map(channel=>channel / 255)
    .map(value=>value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
    .reduce((total,value,index)=>total + value * [.2126,.7152,.0722][index],0);
  const values = [luminance(foreground),luminance(background)].sort((a,b)=>b-a);
  return (values[0] + .05) / (values[1] + .05);
};

(async () => {
  const browser = await chromium.launch({headless:true,
    ...(process.env.HPS_BROWSER_EXECUTABLE ? {executablePath:process.env.HPS_BROWSER_EXECUTABLE} : {})});
  try {
    const page = await browser.newPage({locale:'en-US'});
    const errors = [];
    page.on('pageerror',error=>errors.push(error.message));
    await page.setContent('<html><body style="margin:0;padding:12px;font-family:Arial"><main></main></body></html>');
    await page.evaluate(()=>{window.module={exports:{}};});
    await page.addScriptTag({path:path.resolve(__dirname,'../../../public/js/hourly_productivity_summary_view.js')});
    await page.evaluate(data=>{
      const updateOverview=module.exports.setupResponsiveOverview(document.querySelector('main'));
      window.render = data => {
        document.querySelector('main').innerHTML = module.exports.styles +
          `<div class="hps-shell">${module.exports.renderSummary(data)}</div>`;
        updateOverview();
      };
      render(data);
    },fixture);
    assert.equal(await page.locator('.hps-dot').count(),2);
    assert.equal(await page.locator('.hps-hourly-table tbody tr').count(),12);
    assert.equal(await page.locator('.hps-hourly-table tbody tr').first().locator('th small').count(),0);
    const tableText = await page.locator('.hps-hourly-table').innerText();
    assert.ok(!tableText.includes('Recorded') && !tableText.includes('minutes scheduled fatigue'));
    assert.ok(tableText.includes('No record · 0 shown'));
    assert.ok(!(await page.locator('main').innerText()).includes('Unpaired excavator availability'));
    assert.equal(await page.locator('.hps-shell').evaluate(el=>getComputedStyle(el).maxWidth),'none');
    assert.equal(await page.locator('.hps-asset-card').count(),3);
    assert.equal(await page.locator('.hps-fleet-grid').count(),2);
    assert.ok(!(await page.locator('main').innerText()).includes('Other recorded excavators'));
    assert.equal(await page.locator('.hps-footnote').count(),0);
    assert.equal(await page.locator('.hps-excavators article').count(),2);
    assert.ok((await page.locator('.hps-excavators').innerText()).includes('06:15'));
    assert.ok(!(await page.locator('main').innerText()).includes('Achieved is capped at target'));
    assert.equal(await page.locator('.hps-targets').count(),1);
    assert.deepEqual(await page.locator('.hps-target-values dd').allTextContents(),['55 BCM','220 BCM','165 BCM']);
    assert.equal(await page.locator('.hps-warning').count(),1, 'Only missing-record warning remains');
    const chart = await page.locator('.hps-chart-section').innerText();
    assert.ok(chart.includes('Target Achieved') && chart.includes('Target Not Achieved'));
    assert.ok(!chart.includes('PBM-backed') && !chart.includes('352'));
    for (const theme of ['light','dark']) {
      await page.emulateMedia({colorScheme:theme});
      await page.evaluate(theme=>{
        document.documentElement.style.setProperty('--card-bg',theme === 'dark' ? '#171717' : '#fff');
        document.documentElement.style.setProperty('--text-color',theme === 'dark' ? '#fff' : '#243447');
        document.body.style.backgroundColor = theme === 'dark' ? '#171717' : '#fff';
      },theme);
      for (const [category,rgb] of [['green','rgb(0, 128, 60)'],['red','rgb(214, 60, 75)']]) {
        await page.evaluate(({data,category})=>{
          data.summary.achievement = category === 'green' ? 100 : 50;
          render(data);
        },{data:fixture,category});
        const kpi = page.locator(`.hps-kpi.hps-${category}`);
        assert.equal(await kpi.evaluate(el=>getComputedStyle(el).backgroundColor),rgb);
        assert.equal(await kpi.evaluate(el=>getComputedStyle(el).borderTopColor),rgb);
        assert.equal(await kpi.evaluate(el=>getComputedStyle(el).color),'rgb(255, 255, 255)');
        assert.equal(await page.locator(`.hps-dot.${category}`).evaluate(el=>getComputedStyle(el).backgroundColor),rgb);
        assert.ok((await page.locator('.hps-pie').evaluate(el=>getComputedStyle(el).backgroundImage)).includes(rgb));
        const row = page.locator(`.hps-hourly-table tr.hps-${category}`).first();
        assert.ok((await row.evaluate(el=>getComputedStyle(el).backgroundImage)).includes(rgb));
        assert.equal(await row.evaluate(el=>getComputedStyle(el).color),'rgb(255, 255, 255)');
      }
      const partial = page.locator('.hps-hourly-table tbody tr').nth(2);
      const image = await partial.evaluate(el=>getComputedStyle(el).backgroundImage);
      assert.ok(image.includes('rgb(36, 95, 158) 0%') && image.includes('rgb(0, 128, 60) 50%'));
      const grey = page.locator('.hps-hourly-table tbody tr').nth(3);
      assert.ok((await grey.evaluate(el=>getComputedStyle(el).backgroundImage)).includes('rgb(245, 245, 245)'));
      const mixed = page.locator('.hps-hourly-table tbody tr').nth(5);
      const mixedImage = await mixed.evaluate(el=>getComputedStyle(el).backgroundImage);
      assert.ok(mixedImage.includes('rgb(36, 95, 158) 0%') && mixedImage.includes('rgb(214, 60, 75) 50%'));
      assert.equal(await mixed.locator('.hps-mixed-text').count(),0);
      assert.equal(await mixed.evaluate(el=>getComputedStyle(el).color),'rgb(255, 255, 255)');
      const blue = page.locator('.hps-hourly-table tbody tr').nth(6);
      assert.ok((await blue.evaluate(el=>getComputedStyle(el).backgroundImage)).includes('rgb(36, 95, 158)'));
      assert.equal(await blue.evaluate(el=>getComputedStyle(el).color),'rgb(255, 255, 255)');
      const lightMixed = page.locator('.hps-hourly-table tbody tr').nth(7);
      assert.equal(await lightMixed.locator('.hps-mixed-text').count(),9);
      for (const label of await lightMixed.locator('.hps-mixed-text').all()) {
        assert.equal(await label.evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(255, 255, 255)');
        assert.equal(await label.evaluate(el=>getComputedStyle(el).color),'rgb(23, 42, 26)');
      }
      for (const cell of await partial.locator('th,td').all()) {
        assert.equal(await cell.evaluate(el=>getComputedStyle(el).backgroundColor),'rgba(0, 0, 0, 0)');
        assert.equal(await cell.evaluate(el=>getComputedStyle(el).color),'rgb(255, 255, 255)');
      }
      for (const width of [320,390,768,1280,1920]) {
        await page.setViewportSize({width,height:1000});
        await page.waitForFunction(mobile=>Array.from(document.querySelectorAll('.hps-responsive-fold')).every(el=>el.open !== mobile),width<=600);
        assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth + 1));
        const order=await page.evaluate(()=>{
          const targets=document.querySelector('.hps-targets').getBoundingClientRect();
          const loads=document.querySelector('.hps-load-times').getBoundingClientRect();
          const pie=document.querySelector('.hps-chart-section').getBoundingClientRect();
          return targets.bottom<=loads.top && loads.bottom<=pie.top;
        });
        assert.ok(order,'Load times follow targets and precede the pie');
        assert.equal(await page.locator('.hps-mobile-hours').isVisible(),width<=600);
        assert.equal(await page.locator('.hps-desktop-hours').isVisible(),width>600);
        if(width<=600) {
          assert.equal(await page.locator('.hps-mobile-hour').count(),12);
          const phoneRed=page.locator('.hps-mobile-hour').nth(1);
          const phoneSummary=phoneRed.locator('summary');
          assert.ok(await phoneSummary.evaluate(el=>el.getBoundingClientRect().height>=44));
          assert.equal(await phoneSummary.evaluate(el=>getComputedStyle(el).color),'rgb(255, 255, 255)');
          assert.ok((await phoneSummary.evaluate(el=>getComputedStyle(el).backgroundImage)).includes('rgb(214, 60, 75)'));
          await phoneSummary.click();
          assert.ok(await phoneRed.getAttribute('open') !== null);
          assert.ok(await phoneRed.locator('.hps-mobile-metrics').isVisible());
          assert.deepEqual(await phoneRed.locator('dt').allTextContents(),['Target BCM','ADTs Available','ADTs Utilised','Δ Available − utilised','Excavators Available','Excavators Utilised','Excavators PBM','PBM affected excavators']);
          await phoneSummary.press('Enter');
          assert.equal(await phoneRed.getAttribute('open'),null);
          const phonePartial=page.locator('.hps-mobile-hour summary').nth(2);
          const phoneGradient=await phonePartial.evaluate(el=>getComputedStyle(el).backgroundImage);
          assert.ok(phoneGradient.includes('rgb(36, 95, 158) 0%') && phoneGradient.includes('rgb(0, 128, 60) 50%'));
          const phoneGrey=page.locator('.hps-mobile-hour summary').nth(3);
          assert.equal(await phoneGrey.evaluate(el=>getComputedStyle(el).color),'rgb(51, 51, 51)');
          const phoneLightMixed=page.locator('.hps-mobile-hour summary').nth(7);
          for(const text of await phoneLightMixed.locator('.hps-mobile-text').all()) {
            assert.equal(await text.evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(255, 255, 255)');
            assert.equal(await text.evaluate(el=>getComputedStyle(el).color),'rgb(23, 42, 26)');
          }
          await page.locator('.hps-targets>summary').click();
          assert.ok(await page.locator('.hps-target-values').isVisible());
          await page.locator('.hps-load-times>summary').click();
          assert.ok(await page.locator('.hps-excavators').isVisible());
          if(process.env.HPS_SCREENSHOT_DIR && width===390) {
            fs.mkdirSync(process.env.HPS_SCREENSHOT_DIR,{recursive:true});
            await page.screenshot({path:path.join(process.env.HPS_SCREENSHOT_DIR,`hps-phone-${theme}.png`),fullPage:true});
          }
          await page.locator('.hps-targets>summary').click();
          await page.locator('.hps-load-times>summary').click();
        }
      }
      assert.ok(await page.locator('.hps-shell').evaluate(el=>el.getBoundingClientRect().width > 1800));
      assert.equal(await page.locator('.hps-kpi strong').first().evaluate(el=>getComputedStyle(el).fontSize),'32px');
      assert.equal(await page.locator('.hps-hourly-table td').first().evaluate(el=>getComputedStyle(el).fontSize),'16px');
      assert.ok(await page.locator('.hps-hourly-table').evaluate(el=>el.scrollWidth <= el.parentElement.clientWidth + 1));
      if (process.env.HPS_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.HPS_SCREENSHOT_DIR,{recursive:true});
        await page.screenshot({path:path.join(process.env.HPS_SCREENSHOT_DIR,`hps-two-category-${theme}.png`),fullPage:true});
      }
    }
    for (const rgb of [[0,128,60],[214,60,75],[36,95,158]]) {
      assert.ok(contrast([255,255,255],rgb) >= 4.5);
    }
    assert.ok(contrast([0,0,0],[245,245,245]) >= 4.5);
    assert.deepEqual(errors,[]);
    console.log('PASS: compact phone list; native tap/keyboard expansion; complete metrics; responsive targets/load times; desktop table preserved; Fresh Crimson; warning hidden; light/dark; 320/390/768/1280/1920px; partial fatigue; grey non-working; contrast >=4.5:1. Synthetic fixtures only.');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
