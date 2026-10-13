const test = require('node:test');
const assert = require('node:assert/strict');
const {renderAnalysis} = require('./hourly_productivity_analysis.js');

function fixture() {
  const cells = Array.from({length: 24}, (_, i) => ({label: `${(i+6)%24}:00`, category: 'bright_red',
    actual_bcm: 0, target_bcm: 220, has_record: false, record_status: 'missing', excavator_available: 1}));
  return {site: '<Koppie>', warnings: ['Missing <records>'], hours: cells.map(x=>x.label),
    rows: [{date: '2026-09-30', day: 'Wed', month: 'September 2026', month_key: '2026-09', plan_name: 'SEP', cells},
      {date: '2026-10-01', day: 'Thu', month: 'October 2026', month_key: '2026-10', plan_name: 'OCT', cells}],
    pie: {total_hours: 48, categories: Object.fromEntries(['green','amber','red','bright_red','grey'].map(key=>
      [key,{hours:key==='bright_red'?48:0,percent:key==='bright_red'?100:0}]))}};
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
  assert.ok(html.includes('48 hours'));
  assert.ok(html.includes('100%'));
  assert.ok(html.includes('No record; displayed zero is unverified'));
  assert.ok(html.includes('&lt;records&gt;'));
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
