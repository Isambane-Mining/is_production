const test = require('node:test');
const assert = require('node:assert/strict');
const {renderSummary, selectedIndex, HourlyProductivitySummaryPage} = require('./hourly_productivity_summary.js');

function fixture() {
  const hour = {shift: 'Day', label: '06:00–07:00', schedule: 'working', has_record: false,
    record_status: 'missing', actual_bcm: 0, target_bcm: 110, achievement: 0, category: 'neutral',
    available: 2.5, utilised: 0, excavator_available: 1.25, excavator_utilised: 0, excavator_unavailable: .75};
  const day = {date: '2026-10-12', plan_name: '<img src=x onerror=alert(1)>', production_excavator_count: 2,
    hours: [hour], indicators: [{shift: 'Day', period: '06:10–06:25', label: 'Startup'}],
    excavator_production: [{excavator: '<EX1>', actual_bcm: 20, first_load_time: '06:15', last_load_time: null,
      shift: 'Day', availability_status: 'Unverified'}],
    timeline: [{adt: '<T1>', cells: [{available_minutes: 45, loads: null, state: 'missing', record_status: 'missing'}]}]};
  return {site: 'Koppie', from_date: '2026-10-12', to_date: '2026-10-13', shift: 'Full Day',
    days: [day, {...day, date: '2026-10-13', plan_name: 'MPP-SECOND'}],
    summary: {actual_bcm: 100, target_bcm: 9680, achievement: 90.909, completed_target_bcm: 110,
      completed_actual_bcm: 100, average_adts_available: 2.5, average_adts_utilised: .5,
      average_excavators_available: 1.25, average_excavators_utilised: .25, average_hours: 2},
    excluded: {missing: 1, unfinished: 0, schedule_unknown: 0, no_target: 0},
    pie: {total_hours: 5, counts: {green: 1, amber: 1, red: 1, bright_red: 1, grey: 1},
      percentages: {green: 20, amber: 20, red: 20, bright_red: 20, grey: 20}}};
}

test('render escapes labels, distinguishes missing zero and retains verified PBM values', () => {
  const html = renderSummary(fixture());
  assert.ok(html.includes('Missing record · 0 shown'));
  assert.ok(html.includes('&lt;img'));
  assert.ok(!html.includes('<img src=x'));
  assert.ok(html.includes('&lt;EX1&gt;'));
  assert.ok(html.includes('hps-neutral'));
  assert.ok(html.includes('1.25'));
  assert.ok(!html.includes('Δ'));
});

test('five hour-count slices and correct averages replace estimate presentation', () => {
  const html = renderSummary(fixture());
  for (const text of ['Average ADTs Available', 'Average ADTs Utilised', 'Average Excavators Available',
    'Average Excavators Utilised', '20% · 1 h', '5 eligible hours', 'reached on completed hours',
    'Reached in completed hours', 'Production Targets', 'Excavator Production']) assert.ok(html.includes(text), text);
  for (const text of ['PBM-backed', 'availability estimate', 'localStorage', 'Unpaired excavator availability',
    'unique utilised', 'historical transfers']) assert.ok(!html.includes(text), text);
  assert.equal((html.match(/<article class="hps-kpi/g) || []).length, 5);
});

test('no eligible hours produces no false classification pie', () => {
  const data = fixture();
  data.pie.total_hours = 0;
  data.summary.achievement = null;
  const html = renderSummary(data);
  assert.ok(html.includes('No eligible hours'));
  assert.ok(!html.includes('role="img"'));
});

test('selected day changes only daily sections and navigation is bounded', () => {
  const data = fixture();
  assert.equal(selectedIndex(data, 'outside'), 0);
  assert.equal(selectedIndex(data, '2026-10-13'), 1);
  const page = Object.create(HourlyProductivitySummaryPage.prototype);
  Object.assign(page, {data, selectedDate: '2026-10-12', draw() {this.draws = (this.draws || 0) + 1;}});
  page.navigate(-1);
  assert.equal(page.draws, undefined);
  page.navigate(1);
  assert.equal(page.selectedDate, '2026-10-13');
  page.navigate(1);
  assert.equal(page.draws, 1);
  const html = renderSummary(data, page.selectedDate);
  assert.ok(html.includes('MPP-SECOND'));
  assert.match(html, /hps-next" disabled/);
  assert.ok(html.includes('9,680'));
});

test('Full Day identifies separate shifts and dynamic indicators outside hour cells', () => {
  const data = fixture();
  data.days[0].excavator_production.push({...data.days[0].excavator_production[0], shift: 'Night',
    first_load_time: '18:20', last_load_time: '05:42'});
  const html = renderSummary(data);
  assert.ok(html.includes('Day · Unverified'));
  assert.ok(html.includes('Night · Unverified'));
  assert.ok(html.includes('05:42'));
  assert.ok(html.includes('06:10–06:25'));
  assert.ok(html.indexOf('hps-indicators') < html.indexOf('hps-hour-row'));
  assert.ok(html.includes('45m · — loads'));
});
