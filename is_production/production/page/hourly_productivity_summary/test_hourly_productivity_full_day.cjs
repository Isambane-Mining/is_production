const test=require('node:test');
const assert=require('node:assert/strict');
const {renderFullDayCards}=require('./hourly_productivity_summary.js');
function shift(name){return {warnings:[],summary:{missing_hours:0},hours:Array.from({length:12},(_,i)=>({label:`${String((i+(name==='Day'?6:18))%24).padStart(2,'0')}:00`,record_status:'recorded',actual_bcm:220,target_bcm:220,achievement:100,presentation_colour:'green',available:4.5,utilised:4,difference:.5,excavator_available:1,excavator_utilised:1,excavator_unavailable:0,excavator_pbm_count:0}))};}
test('full operational day keeps twelve Day and twelve Night hours and next-date overnight details',()=>{
  const day=shift('Day'),night=shift('Night');
  night.hours[6].actual_bcm=123;
  const state={date:'2026-12-31',shifts:{Day:day,Night:night},selectedShift:'Night',selectedHour:6};
  const before=JSON.stringify(state),html=renderFullDayCards(state);
  assert.equal((html.match(/data-hps-hour=/g)||[]).length,24);
  assert.ok(html.indexOf('Day shift')<html.indexOf('Night shift'));
  assert.ok(html.includes('2027-01-01'));
  assert.ok(html.includes('123'));
  for(const label of ['Target BCM','ADTs Available','Excavators Available','Excavators PBM','PBM affected excavators'])assert.ok(html.includes(label),label);
  assert.equal(JSON.stringify(state),before);
});
test('failed companion shift is unavailable with retry, never twelve invented zero hours',()=>{
  const html=renderFullDayCards({date:'2026-10-15',shifts:{Day:shift('Day')},selectedShift:'Day',selectedHour:0,errors:{Night:true}});
  assert.equal((html.match(/data-hps-hour=/g)||[]).length,12);
  assert.ok(html.includes('Night shift'));
  assert.ok(html.includes('data-hps-retry'));
  assert.ok(html.includes('unavailable'));
});
test('full-day cards preserve mixed fatigue, unknown target and missing status without unsafe labels',()=>{
  const day=shift('Day');
  Object.assign(day.hours[0],{label:'<img src=x>',record_status:'missing',actual_bcm:0,target_bcm:null,achievement:null,presentation_colour:'blue',presentation_segments:[{category:'blue',start_percent:0,end_percent:50},{category:'grey',start_percent:50,end_percent:100}]});
  const html=renderFullDayCards({date:'2026-10-15',shifts:{Day:day},selectedShift:'Day',selectedHour:0});
  assert.ok(html.includes('&lt;img'));
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('No record'));
  assert.ok(html.includes('50%'));
  assert.ok(html.includes('hps-blue'));
  assert.ok(html.includes('hps-mobile-mixed'));
  assert.ok(html.includes('—'));
  assert.ok(!html.includes('null%'));
});
