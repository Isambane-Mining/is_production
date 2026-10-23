const test = require('node:test');
const assert = require('node:assert/strict');
const {renderAnalysis, renderHourDetails, renderMobileAnalysis, renderProductivityTabs} = require('./hourly_productivity_analysis.js');

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
  assert.ok(html.includes('aria-expanded="false"'));

  assert.ok(html.includes('role="img"'));
});

test('hover card shows readable production metrics and only saved shift load times', () => {
  const row = fixture().rows[0];
  row.load_times = {Day:[{excavator:'<EX1>',first_load_time:'06:15',last_load_time:'17:45'},
    {excavator:'EX2',first_load_time:null,last_load_time:null},
    {excavator:'EX3',first_load_time:null,last_load_time:'17:30'}],
    Night:[{excavator:'NIGHT',first_load_time:'18:20',last_load_time:'05:50'}]};
  const cell = {...row.cells[0], start:'2026-09-30 06:00:00',end:'2026-09-30 07:00:00',
    actual_bcm:110,target_bcm:220,achievement:50,non_working_minutes:30};
  const html = renderHourDetails(row,cell,0);
  for (const label of ['2026-09-30','06:00–07:00','Actual Production','Production Target',
    'Target Achieved','50%','Excavators Available','Recording Status','No production record',
    'Fatigue / Non-working','30 minutes','&lt;EX1&gt;','06:15','17:45','17:30']) assert.ok(html.includes(label),label);
  assert.ok(!html.includes('EX2'));
  assert.ok(!html.includes('NIGHT'));
  assert.ok(!html.includes('—'));
  const night = renderHourDetails(row,cell,12);
  assert.ok(night.includes('NIGHT'));
  assert.ok(!night.includes('&lt;EX1&gt;'));
});

test('hover card keeps unknown achievement and availability distinct from zero', () => {
  const row = fixture().rows[0];
  const html = renderHourDetails(row,{...row.cells[0],achievement:null,excavator_available:null,
    non_working_minutes:0,record_status:'in_progress_missing'},0);
  assert.ok(html.includes('Not available'));
  assert.ok(html.includes('Hour in progress — no record yet'));
  assert.ok(!html.includes('Fatigue / Non-working'));
  assert.ok(!html.includes('Shift Load Times'));
});

test('hour cells expose accessible card controls and no native long tooltip', () => {
  const html = renderAnalysis(fixture());
  const cells = html.match(/<td class="hpa-cell [^>]+>/g);
  assert.ok(cells.every(cell=>cell.includes('tabindex="0"') && cell.includes('aria-haspopup="dialog"')));
  assert.ok(cells.every(cell=>!cell.includes(' title=')));
  assert.ok(html.includes('hpa-pie'));
  assert.ok(html.includes('hpa-legend'));
  assert.ok(!html.includes('Pie percentage ='));
  assert.ok(!html.includes('Missing and future cells remain in the denominator'));
});

test('partial-hour gradient preserves actual BCM and fatigue legend', () => {
  const data = fixture();
  Object.assign(data.rows[0].cells[0], {actual_bcm:123, non_working_minutes:30, segments:[
    {start_percent:0,end_percent:50,category:'grey'},
    {start_percent:50,end_percent:100,category:'bright_red'}]});
  data.pie.categories.grey = {hours:.5,percent:1.04};
  const html = renderAnalysis(data);
  assert.ok(html.includes('linear-gradient(to right,#f5f5f5 0% 50%,#d63c4b 50% 100%)'));
  assert.ok(html.includes('>123'));
  assert.ok(html.includes('Fatigue / Non-Working Hours'));
  assert.ok(html.includes(Number(.5).toLocaleString(undefined,{maximumFractionDigits:2})+' hours'));
});

test('missing planning is rendered in report without a matrix or pie', () => {
  const html = renderAnalysis({rows:[],planning_error:{title:'Monthly Production Planning Not Found',
    issues:[{message:'No accessible production plan for <Koppie> for September 2026.'},
      {message:'No accessible production plan for October 2026.'}]}});
  assert.ok(html.includes('Monthly Production Planning Not Found'));
  assert.ok(html.includes('&lt;Koppie&gt;'));
  assert.ok(html.includes('October 2026'));
  assert.ok(!html.includes('hpa-pie'));
  assert.ok(!html.includes('<table'));
});

test('phone view shows both shifts, 24 buttons, a small five-category key and date selection', () => {
  const data=fixture();
  const before=JSON.stringify(data);
  const html=renderAnalysis(data);
  const mobile=html.match(/<section class="hpa-mobile"[\s\S]*?<\/section>/)?.[0] || '';
  assert.equal((mobile.match(/class="hpa-mobile-cell /g)||[]).length,24);
  assert.equal((mobile.match(/class="hpa-key-item"/g)||[]).length,5);
  assert.ok(mobile.includes('Day shift · 06:00–18:00'));
  assert.ok(mobile.includes('Night shift · 18:00–06:00 next day'));
  assert.ok(mobile.includes('class="hpa-mobile-date"'));
  assert.ok(mobile.includes('2026-09-30'));
  assert.ok(mobile.includes('2026-10-01'));
  assert.equal(JSON.stringify(data),before);
});

test('mobile date changes use existing hour details and the actual next calendar date', () => {
  const data=fixture();
  data.rows[1].cells=data.rows[1].cells.map(cell=>({...cell}));
  Object.assign(data.rows[1].cells[18],{start:'2026-10-02 00:00:00',end:'2026-10-02 01:00:00',
    actual_bcm:123,target_bcm:220,achievement:55.909,excavator_available:null});
  const html=renderMobileAnalysis(data,'test-card',1,18);
  assert.ok(html.includes('value="1" selected'));
  assert.ok(html.includes('2026-10-02 · 00:00–01:00'));
  assert.ok(html.includes('123 BCM'));
  assert.ok(html.includes('Not available'));
  assert.ok(html.includes('No production record'));
});

test('approved palette is shared by the matrix, pie and phone key without changing category counts', () => {
  const data=fixture();
  Object.assign(data.rows[0].cells[0],{category:'amber',segments:[]});
  Object.assign(data.rows[0].cells[1],{category:'red',segments:[]});
  const before=JSON.stringify(data.pie);
  const html=renderAnalysis(data);
  assert.ok(html.includes('#ff9f1c'));
  assert.ok(html.includes('#ffe066'));
  assert.ok(html.includes('#d63c4b'));
  assert.ok(!html.includes('#ffc000'));
  assert.equal(JSON.stringify(data.pie),before);
});

test('mobile preview escapes dates and empty data returns no selectable hours', () => {
  const data=fixture();
  data.rows[0].date='<img src=x onerror=alert(1)>';
  const html=renderMobileAnalysis(data);
  assert.ok(html.includes('&lt;img'));
  assert.ok(!html.includes('<img'));
  assert.ok(!renderMobileAnalysis({...data,rows:[]}).includes('hpa-mobile-cell'));
});

test('combined page presents Analysis first and Summary second with separate labelled panels', () => {
  const html=renderProductivityTabs();
  assert.ok(html.indexOf('Hourly Productivity Analysis')<html.indexOf('Hourly Productivity Summary'));
  assert.equal((html.match(/role="tab"/g)||[]).length,2);
  assert.equal((html.match(/role="tabpanel"/g)||[]).length,2);
  assert.ok(html.includes('aria-selected="true"'));
  assert.ok(html.includes('class="hpt-summary-panel"'));
  assert.ok(html.includes('class="hpt-analysis-panel"'));
});
