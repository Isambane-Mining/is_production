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
  assert.ok(html.includes('hps-neutral'));
});

test('render pie exposes estimate and completed-hour basis', () => {
  const html = renderSummary(fixture());
  assert.ok(html.includes('PBM-backed availability estimate'));
  assert.ok(html.includes('Completed hours'));
  assert.ok(html.includes('Remaining shortfall'));
  assert.ok(html.includes('role="img"'));
});

test('render no-target data has no achievement colour or misleading pie', () => {
  const data = fixture();
  data.pie = {target: 0, achieved: 0, availability_estimate: 0, remaining: 0};
  data.summary.target_bcm = null;
  data.summary.achievement = null;
  assert.ok(renderSummary(data).includes('No completed-hour target'));
});
