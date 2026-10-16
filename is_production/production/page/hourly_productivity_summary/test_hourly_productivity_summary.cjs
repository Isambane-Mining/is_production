const test = require('node:test');
const assert = require('node:assert/strict');
const { renderSummary } = require('./hourly_productivity_summary.js');

function fixture() {
  return {
    plan_name: '<img src=x onerror=alert(1)>', production_excavator_count: 2,
    summary: {actual_bcm: 100, target_bcm: 4840, achievement: 90.909,
      completed_target_bcm: 110, completed_actual_bcm: 100, missing_hours: 1,
      adts_at_site: 3, average_available: 2.5, adts_utilised: 2, total_loads: 6},
    thresholds: {green: 100, orange: 80}, warnings: ['Missing <script>'],
    pie: {target: 110, achieved: 100, availability_estimate: 10, remaining: 0},
    hours: [{label: '06:00–07:00', record_status: 'missing', actual_bcm: 0,
      target_bcm: 110, achievement: 0, colour: 'neutral', available: 2.5, utilised: 2, difference: .5}],
    excavators: [{excavator: '<EX1>', first_load_time: '06:15', last_load_time: null}],
    production_excavators: [{label: 'EX1', actual_bcm: 100}], spare_excavators: [{label: 'EX2', actual_bcm: 20, classification: 'Spare'}],
    unplanned_excavators: [], details: []
  };
}

test('render distinguishes missing zero and escapes source labels', () => {
  const html = renderSummary(fixture());
  assert.ok(html.includes('No record · 0 shown'));
  assert.ok(html.includes('&lt;img'));
  assert.ok(!html.includes('<img src=x'));
  assert.ok(html.includes('&lt;EX1&gt;'));
  assert.ok(html.includes('<tr class="hps-red"'));
});

test('pie shows only achieved and full shortfall on the completed BCM target basis', () => {
  const data = fixture();
  data.pie = {target: 1000, achieved: 400, availability_estimate: 352, remaining: 248};
  const before = JSON.stringify(data);
  const html = renderSummary(data);
  const chart = html.match(/<section class="hps-chart-section"[\s\S]*?<\/section>/)[0];
  assert.ok(chart.includes('Target Achieved <b>400 BCM</b>'));
  assert.ok(chart.includes('Target Not Achieved <b>600 BCM</b>'));
  assert.equal((chart.match(/class="hps-dot /g) || []).length, 2);
  assert.ok(!chart.includes('PBM-backed'));
  assert.ok(!chart.includes('352'));
  assert.ok(!chart.includes('Remaining shortfall'));
  assert.ok(chart.includes('var(--hps-green) 0% 40%, var(--hps-red) 40% 100%'));
  assert.ok(chart.includes('Target Achieved 400 BCM; Target Not Achieved 600 BCM'));
  assert.ok(html.includes('Completed hours'));
  assert.ok(html.includes('role="img"'));
  assert.equal(JSON.stringify(data), before, 'Rendering must preserve all source and availability data');
});

test('pie handles zero production and capped achievement without changing actual BCM', () => {
  for (const achieved of [0, 110]) {
    const data = fixture();
    data.pie = {target: 110, achieved, availability_estimate: 0, remaining: 110 - achieved};
    data.summary.actual_bcm = achieved ? 150 : 0;
    const html = renderSummary(data);
    assert.ok(html.includes(`Target Not Achieved <b>${110 - achieved} BCM</b>`));
    assert.ok(html.includes(`var(--hps-red) ${achieved ? 100 : 0}% 100%`));
    assert.equal(data.summary.actual_bcm, achieved ? 150 : 0);
  }
});

test('render no-target data has no achievement colour or misleading pie', () => {
  const data = fixture();
  data.pie = {target: 0, achieved: 0, availability_estimate: 0, remaining: 0};
  data.summary.target_bcm = null;
  data.summary.achievement = null;
  assert.ok(renderSummary(data).includes('No completed-hour target'));
});

test('render production metrics and retain unpaired data without the availability disclosure', () => {
  const data = fixture();
  Object.assign(data.hours[0], {excavator_available: 1.25, excavator_utilised: 1,
    excavator_unavailable: .75, excavator_pbm_count: 1, unpaired_available: 1, unpaired_utilised: 1, unpaired_unavailable: 0});
  data.unpaired_excavators = [{label: 'EX2', actual_bcm: 20, classification: 'Production/spare unknown'}];
  const html = renderSummary(data);
  assert.ok(html.includes('Excavators Available'));
  assert.ok(html.includes('Excavators Utilised'));
  assert.ok(html.includes('Excavators PBM'));
  assert.ok(html.includes(Number(1.25).toLocaleString(undefined, {maximumFractionDigits: 2})));
  assert.ok(!html.includes('Unpaired excavator availability'));
  assert.ok(html.includes('Production/spare unknown'));
  assert.ok(!html.includes('Spare excavators · excluded from targets'));
});

test('working colours use actual target comparison regardless of configured thresholds', () => {
  const data = fixture();
  data.thresholds = {green:110,orange:80};
  data.hours = [
    {...data.hours[0],actual_bcm:110,target_bcm:110,colour:'orange'},
    {...data.hours[0],actual_bcm:109,target_bcm:110,colour:'green'}];
  const html=renderSummary(data);
  assert.equal((html.match(/<tr class="hps-green"/g)||[]).length,1);
  assert.equal((html.match(/<tr class="hps-red"/g)||[]).length,1);
});

test('partial scheduled fatigue is blue alongside working red and keeps target visible', () => {
  const data=fixture();
  Object.assign(data.hours[0], {presentation_colour:'red',fatigue_minutes:30,non_working_minutes:0,
    presentation_segments:[{start_percent:0,end_percent:50,category:'blue'},
      {start_percent:50,end_percent:100,category:'red'}]});
  const html=renderSummary(data);
  assert.ok(html.includes('linear-gradient(to right,var(--hps-hour-blue) 0% 50%,var(--hps-hour-red) 50% 100%)'));
  assert.ok(!html.includes('30 minutes scheduled fatigue'));
  assert.ok(html.includes('<td>110</td>'));
});

test('partial red and blue retain their split and share white text without backings', () => {
  const data = fixture();
  data.hours[0].presentation_segments = [
    {start_percent:0,end_percent:50,category:'blue'},
    {start_percent:50,end_percent:100,category:'red'}];
  const html = renderSummary(data);
  assert.ok(html.includes('var(--hps-hour-blue) 0% 50%,var(--hps-hour-red) 50% 100%'));
  assert.equal((html.match(/class="hps-mixed-text"/g) || []).length, 0);
  assert.ok(html.includes('<td>110</td>'));
  assert.ok(html.includes('No record · 0 shown'));
});

test('mixed light grey and dark blue preserve the split with readable text backings', () => {
  const data = fixture();
  Object.assign(data.hours[0], {presentation_colour:'blue', presentation_segments:[
    {start_percent:0,end_percent:50,category:'blue'},
    {start_percent:50,end_percent:100,category:'grey'}]});
  const html = renderSummary(data);
  assert.ok(html.includes('var(--hps-hour-blue) 0% 50%,var(--hps-hour-grey) 50% 100%'));
  assert.equal((html.match(/class="hps-mixed-text"/g) || []).length, 9);
  assert.ok(html.includes('<span class="hps-mixed-text">110</span>'));
});

test('target panel presents the existing per-excavator basis and escapes the MPP name', () => {
  const data = fixture();
  const html = renderSummary(data);
  assert.ok(html.includes('aria-label="Production targets per excavator"'));
  assert.ok(html.includes('<h3>Production targets</h3>'));
  for (const [label,value] of [['First hour',55],['Normal hour',220],['Last hour',165]]) {
    assert.ok(html.includes(`<dt>${label}</dt><dd>${value} <span>BCM</span></dd>`));
  }
  assert.ok(html.includes('Per production excavator'));
  assert.ok(html.includes('Targets stay fixed during breakdowns.'));
  assert.ok(html.includes('MPP: &lt;img'));
  data.plan_name = null;
  assert.ok(renderSummary(data).includes('No covering MPP'));
});

test('only the two requested notices are hidden while source warnings and missing-record labels remain', () => {
  const data = fixture();
  const schedule = 'MPP specifies 9.0 working hours for 2026-06-16 Day, without clock times. Only configured fatigue is shaded; the shorter shift\'s timing is unknown.';
  const fleet = 'ADT fleet uses current Asset site assignments; historical transfers are not reconstructed.';
  data.warnings = [schedule, fleet, 'No accessible covering MPP: targets are unavailable.', 'Missing <script>'];
  const before = JSON.stringify(data);
  const html = renderSummary(data);
  assert.ok(!html.includes('Only configured fatigue is shaded'));
  assert.ok(!html.includes('historical transfers are not reconstructed'));
  assert.ok(html.includes('No accessible covering MPP: targets are unavailable.'));
  assert.ok(html.includes('Missing &lt;script&gt;'));
  assert.ok(html.includes('completed hours have no production record'));
  assert.equal(JSON.stringify(data),before);
});

test('recorded and fatigue labels and requested explanations are removed without changing source data', () => {
  const data=fixture();
  Object.assign(data.hours[0], {record_status:'recorded',fatigue_minutes:60,presentation_colour:'blue',
    presentation_segments:[{start_percent:0,end_percent:100,category:'blue'}]});
  const before=JSON.stringify(data);
  const html=renderSummary(data);
  const table=html.match(/<table class="hps-table hps-hourly-table">[\s\S]*?<\/table>/)[0];
  assert.ok(!table.includes('Recorded'));
  assert.ok(!table.includes('minutes scheduled fatigue'));
  assert.ok(table.includes('hps-blue'));
  assert.ok(!html.includes('Available ADTs are weighted by available minutes'));
  assert.ok(!html.includes('Production excavator availability and PBM unavailability use the same minute weighting'));
  assert.ok(!html.includes('Unpaired excavator availability'));
  assert.ok(html.includes('ADT availability detail'));
  assert.equal(JSON.stringify(data),before);
});

test('pending and incomplete recording labels are still distinguishable from captured production', () => {
  const data=fixture();
  data.hours=['pending','in_progress','in_progress_missing'].map(record_status=>({...data.hours[0],record_status}));
  const html=renderSummary(data);
  assert.ok(html.includes('Pending'));
  assert.ok(html.includes('In progress'));
  assert.ok(html.includes('In progress · no record'));
});

test('production teams and unpaired excavators display as separate escaped asset cards', () => {
  const data=fixture();
  data.production_excavators=[{label:'EX1',actual_bcm:100},{label:'<EX2>',actual_bcm:0}];
  data.unpaired_excavators=[{label:'EX3',actual_bcm:0,classification:'Production/spare unknown'}];
  const before=JSON.stringify(data);
  const html=renderSummary(data);
  assert.equal((html.match(/class="hps-asset-card"/g)||[]).length,3);
  assert.equal((html.match(/class="hps-fleet-grid"/g)||[]).length,2);
  assert.ok(html.includes('<strong>&lt;EX2&gt;</strong>'));
  assert.ok(html.includes('class="hps-asset-bcm">100 <span>BCM</span>'));
  assert.ok(html.includes('Production/spare unknown'));
  assert.ok(html.includes('MPP truck-assigned production teams'));
  assert.ok(html.includes('Unpaired excavators · production/spare unknown'));
  assert.equal(JSON.stringify(data),before);
});

test('requested notes and other-recorded fleet are hidden without removing output from the KPI or source', () => {
  const data=fixture();
  data.unplanned_excavators=[{label:'Unassigned',actual_bcm:25}];
  data.summary.actual_bcm=125;
  const before=JSON.stringify(data);
  const html=renderSummary(data);
  for(const text of ['First is the earliest captured First Load','Spare classification is unavailable',
    'Other recorded excavators · MPP classification unknown','All recorded excavator output is included in Actual BCM']) {
    assert.ok(!html.includes(text),text);
  }
  assert.ok(html.includes('First / last recorded load times'));
  assert.ok(html.includes('<strong>125</strong>'));
  assert.equal(JSON.stringify(data),before);
});

test('first and last recorded load cards follow targets and precede the pie and hourly table', () => {
  const data=fixture();
  const html=renderSummary(data);
  const loads=html.indexOf('First / last recorded load times');
  assert.ok(loads>html.indexOf('aria-label="Production targets per excavator"'));
  assert.ok(loads<html.indexOf('class="hps-chart-section"'));
  assert.ok(loads<html.indexOf('class="hps-table hps-hourly-table"'));
  assert.equal((html.match(/First \/ last recorded load times/g)||[]).length,1);
  assert.ok(html.includes('06:15'));
  assert.ok(html.includes('&lt;EX1&gt;'));
  assert.ok(!html.includes('Achieved is capped at target; excess BCM remains in the KPI.'));
  assert.equal(data.pie.achieved,100);
});

test('unpaired MPP explanation is hidden while asset cards, other warnings and source classifications remain', () => {
  const data=fixture();
  data.warnings=[
    'Unpaired MPP excavators are production/spare unknown: the original MPP stores empty production assignments in browser localStorage, not on these rows. They are not labelled as confirmed spares.',
    'An ADT is assigned to multiple MPP teams; availability attribution is disabled.'];
  data.unpaired_excavators=[{label:'EX018',actual_bcm:0,classification:'Production/spare unknown'}];
  const before=JSON.stringify(data);
  const html=renderSummary(data);
  assert.ok(!html.includes('Unpaired MPP excavators are production/spare unknown:'));
  assert.ok(html.includes('An ADT is assigned to multiple MPP teams; availability attribution is disabled.'));
  assert.ok(html.includes('Unpaired excavators · production/spare unknown'));
  assert.ok(html.includes('<strong>EX018</strong>'));
  assert.ok(html.includes('Production/spare unknown'));
  assert.equal(JSON.stringify(data),before);
});

test('phone compact list keeps each hour summary with target and all availability metrics in its disclosure', () => {
  const data=fixture();
  Object.assign(data.hours[0], {excavator_available:1.25,excavator_utilised:1,excavator_unavailable:.75,excavator_pbm_count:1});
  const before=JSON.stringify(data);
  const html=renderSummary(data);
  const mobile=html.match(/<section class="hps-mobile-hours"[\s\S]*?<\/section>/)?.[0] || '';
  assert.ok(mobile.includes('class="hps-mobile-hour"'));
  assert.ok(mobile.includes('<summary'));
  for(const text of ['06:00–07:00','No record · 0 shown','Target BCM','ADTs Available','ADTs Utilised',
    'Excavators Available','Excavators Utilised','Excavators PBM','PBM affected excavators','Δ Available − utilised']) {
    assert.ok(mobile.includes(text),text);
  }
  assert.equal(JSON.stringify(data),before);
});

test('phone list preserves unknown targets, fatigue, missing status and escapes hour labels', () => {
  const data=fixture();
  Object.assign(data.hours[0], {label:'<img src=x onerror=alert(1)>',target_bcm:null,achievement:null,
    presentation_colour:'blue',fatigue_minutes:60,presentation_segments:[{start_percent:0,end_percent:100,category:'blue'}]});
  const html=renderSummary(data);
  const mobile=html.match(/<section class="hps-mobile-hours"[\s\S]*?<\/section>/)?.[0] || '';
  assert.ok(mobile.includes('Scheduled fatigue'));
  assert.ok(mobile.includes('No record · 0 shown'));
  assert.ok(mobile.includes('&lt;img'));
  assert.ok(!mobile.includes('<img'));
  assert.ok(mobile.includes('<dd>—</dd>'));
  assert.ok(!mobile.includes('null%'));
});
