const test = require('node:test');
const assert = require('node:assert/strict');
const {renderAnalysis, HourlyProductivityAnalysisPage} = require('../hourly_productivity/hourly_productivity.js');

function fixture() {
  const cells = Array.from({length: 24}, (_, i) => ({label: `${(i+6)%24}:00`, category: 'unclassified',
    actual_bcm: 0, target_bcm: 220, has_record: false, record_status: 'missing', excavator_available: 1, schedule:'working', completed:true}));
  return {site: '<Koppie>', warnings: ['Missing <records>'], hours: cells.map(x=>x.label),
    rows: [{date: '2026-09-30', day: 'Wed', month: 'September 2026', month_key: '2026-09', plan_name: 'SEP', production_excavator_count:2, cells},
      {date: '2026-10-01', day: 'Thu', month: 'October 2026', month_key: '2026-10', plan_name: 'OCT', production_excavator_count:3, cells}],
    pie: {total_hours: 4, categories: Object.fromEntries(['green','amber','red','bright_red','grey'].map(key=>
      [key,{hours:key==='green'?4:0,percent:key==='green'?100:0}]))}};
}

test('matrix renders grouped shifts, 24 hours per row, and month dividers', () => {
  const html = renderAnalysis(fixture());
  assert.ok(html.includes('DAY SHIFT'));
  assert.ok(html.includes('NIGHT SHIFT'));
  assert.equal((html.match(/class="hpa-cell /g) || []).length, 48);
  assert.equal((html.match(/class="hpa-month-divider"/g) || []).length, 2);
  assert.ok(html.includes('hpa-sticky-date'));
});

test('legend uses hour counts and percentages and escapes sources', () => {
  const html = renderAnalysis(fixture());
  assert.ok(html.includes('4 included hours'));
  assert.ok(html.includes('100%'));
  assert.ok(html.includes('No record; displayed zero is unverified'));
  assert.ok(html.includes('&lt;Koppie&gt;'));
  assert.ok(!html.includes('hpa-warning'));
  assert.ok(!html.includes('Pie percentage'));
  assert.ok(!html.includes('Scroll sideways for all'));
  assert.ok(html.includes('Not planned to work'));
  assert.ok(html.includes('role="img"'));
});

test('cell tooltips show captured load times for their own shift and unknown dash', () => {
  const data = fixture();
  data.rows[0].load_times = {Day:[{excavator:'EX1',first_load_time:'06:15',last_load_time:'17:45'}],
    Night:[{excavator:'EX1',first_load_time:'18:20',last_load_time:'05:50'},
      {excavator:'EX2',first_load_time:null,last_load_time:null}]};
  const html = renderAnalysis(data);
  assert.ok(html.includes('EX1 First 06:15, Last 17:45'));
  assert.ok(html.includes('EX1 First 18:20, Last 05:50'));
  assert.ok(html.includes('EX2 First —, Last —'));
});


test('compact target section distinguishes covering MPP counts across months', () => {
  const html = renderAnalysis(fixture());
  for (const text of ['Production Targets', '15 min', '60 min', '45 min', '55 BCM', '220 BCM', '165 BCM',
    'Normal hourly target: 440 BCM', 'Normal hourly target: 660 BCM', 'SEP', 'OCT']) assert.ok(html.includes(text), text);
});

test('empty pie and unclassified cells do not claim poor performance', () => {
  const data = fixture();
  data.pie.total_hours = 0;
  data.excluded = {missing: 40, schedule_unknown: 8};
  const html = renderAnalysis(data);
  assert.ok(html.includes('No eligible completed hours'));
  assert.ok(!html.includes('role="img"'));
  assert.ok(html.includes('hpa-unclassified'));
  assert.ok(html.includes('Excluded: 40 missing · 8 schedule unclassified'));
});

test('investigation uses Frappe internal navigation to the production Summary page', () => {
  const previous = global.frappe;
  const routes = [];
  global.frappe = {set_route: route => routes.push(route)};
  try {
    HourlyProductivityAnalysisPage.prototype.openInvestigation();
    assert.deepEqual(routes, ['/desk/production/hourly-productivity-summary']);
  } finally {global.frappe = previous;}
});

test('displayed percentage rounding reconciles to 100 without assigning empty slices', () => {
  const {displayPercentages} = require('../hourly_productivity/hourly_productivity.js');
  const percentages = displayPercentages({total_hours:3, categories:Object.fromEntries(
    ['green','amber','red','bright_red','grey'].map((key,i)=>[key,{hours:i<3?1:0}]))});
  assert.equal(Object.values(percentages).reduce((a,b)=>a+Math.round(b*10),0),1000);
  assert.equal(percentages.grey,0);
  assert.equal(percentages.bright_red,0);
  assert.equal(percentages.green,33.4);
});
