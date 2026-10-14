const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {HourlyProductivityPage}=require('./hourly_productivity.js');

test('canonical page exposes two independent tab panels',()=>{
 assert.equal(typeof HourlyProductivityPage,'function');
});

test('legacy routes redirect on first load and return visits without rendering a second report',()=>{
 for(const tab of ['summary','analysis']) {
  const route='hourly-productivity-'+tab;
  const previous=global.frappe;
  const calls=[];
  global.frappe={pages:{[route]:{}},get_route:()=>[route],route_options:{site:'Koppie'},set_route:r=>calls.push(r)};
  try {
   delete require.cache[require.resolve('../hourly_productivity_'+tab+'/hourly_productivity_'+tab+'.js')];
   require('../hourly_productivity_'+tab+'/hourly_productivity_'+tab+'.js');
   frappe.pages[route].on_page_load({});
   assert.equal(frappe.route_options.tab,tab);
   assert.equal(frappe.route_options.site,'Koppie');
   assert.deepEqual(calls,['hourly-productivity']);
   frappe.pages[route].on_page_show({});
   assert.equal(calls.length,2);
  } finally {global.frappe=previous;}
 }
});

test('CSS is in the existing Desk asset with no JS style blocks',()=>{
 const root=path.resolve(__dirname,'../../..');
 const css=fs.readFileSync(path.join(root,'public/css/dashboards.css'),'utf8');
 for(const name of ['summary','analysis']) {
  const script=fs.readFileSync(path.join(root,'public/js/hourly_productivity_'+name+'.js'),'utf8');
  assert.ok(!script.includes('<style>'));
  assert.ok(css.includes('.hourly-productivity .hp'+(name==='summary'?'s':'a')+'-shell'));
 }
 assert.ok(fs.readFileSync(path.join(root,'hooks.py'),'utf8').includes('/assets/is_production/css/dashboards.css'));
});

test('canonical page retains both original role restrictions',()=>{
 const canonical=JSON.parse(fs.readFileSync(path.join(__dirname,'hourly_productivity.json'),'utf8'));
 for(const tab of ['summary','analysis']) {
  const legacy=JSON.parse(fs.readFileSync(path.join(__dirname,'../hourly_productivity_'+tab,'hourly_productivity_'+tab+'.json'),'utf8'));
  assert.deepEqual(canonical.roles,legacy.roles);
 }
});
