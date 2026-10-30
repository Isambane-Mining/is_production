// Run with node --test. Exercise the Page controller without booting all of Desk.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function load() {
    const context = vm.createContext({
        frappe: {pages: {'hod-presentation': {}}, datetime: {get_today: () => '2026-10-22'}},
        __: text => text, clearTimeout, setTimeout, console
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname, 'hod_presentation.js'), 'utf8'), context);
    return context;
}

function controller() {
    const state = {disabled: true, status: ''};
    return {
        sequence: 0, view: {data: []},
        get_filter_values: () => ({end_date: '2026-03-31', site: ['Uitgevallen']}),
        $download: {prop: (_, value) => {state.disabled = value;}},
        $content: {empty() {}, find: () => ({length: 1})},
        $status: {text: value => {state.status = value;}}, state
    };
}

test('stale responses cannot overwrite the latest filters or enable download', async () => {
    const context = load();
    const pending = [];
    context.frappe.call = () => new Promise(resolve => pending.push(resolve));
    context.renderHodPresentationLayout = async () => {};
    const refresh = vm.runInContext('value => HodPresentationPage.prototype.refresh.call(value)', context);
    const app = controller();
    const first = refresh(app);
    const second = refresh(app);
    pending[1]({message: {result: [{site: 'new'}]}});
    await second;
    pending[0]({message: {result: [{site: 'stale'}]}});
    await first;
    assert.equal(app.view.data[0].site, 'new');
    assert.equal(app.state.disabled, false);
});

test('download stays disabled until all site dashboards have rendered', async () => {
    const context = load();
    context.frappe.call = async () => ({message: {result: [{site: 'Uitgevallen'}]}});
    let finish;
    context.renderHodPresentationLayout = () => new Promise(resolve => {finish = resolve;});
    const refresh = vm.runInContext('value => HodPresentationPage.prototype.refresh.call(value)', context);
    const app = controller();
    const work = refresh(app);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.state.disabled, true);
    finish();
    await work;
    assert.equal(app.state.disabled, false);
});

test('denied data leaves no old dashboard or downloadable presentation', async () => {
    const context = load();
    context.frappe.call = async () => {throw new Error('Permission denied');};
    const refresh = vm.runInContext('value => HodPresentationPage.prototype.refresh.call(value)', context);
    const app = controller();
    await refresh(app);
    assert.equal(app.state.disabled, true);
    assert.equal(app.view.data.length, 0);
    assert.match(app.state.status, /access permissions/);
});

test('old Query Report forwards filters to the dedicated Page', () => {
    let route;
    const context = vm.createContext({
        frappe: {query_reports: {}, route_options: {machine_scope: 'Include Swing/Spare'},
            set_route: value => {route = value;}},
        window: {location: {search: '?site=%5B%22Uitgevallen%22%5D&end_date=2026-03-31'}},
        URLSearchParams
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../report/hod_presentation/hod_presentation.js'), 'utf8'), context);
    context.frappe.query_reports['HOD Presentation'].onload();
    assert.equal(route, 'hod-presentation');
    assert.equal(context.frappe.route_options.site, '["Uitgevallen"]');
    assert.equal(context.frappe.route_options.end_date, '2026-03-31');
    assert.equal(context.frappe.route_options.machine_scope, 'Include Swing/Spare');
});


test('threshold bands, legends and unavailable values agree', () => {
    const context = load();
    for (const [metric,cases] of Object.entries({
        avail:[[74.99,'red'],[75,'amber'],[84.99,'amber'],[85,'green']],
        util:[[69.99,'red'],[70,'amber'],[79.99,'amber'],[80,'green']]
    })) {
        const band = vm.runInContext('hodBand', context);
        cases.forEach(([value,expected])=>assert.equal(band(metric,value),expected));
        assert.equal(band(metric,null),'neutral');
    }
    assert.equal(vm.runInContext('hodPercent(null)',context),'—');
    assert.match(vm.runInContext("hodMetricLegend('avail')",context),/75–&lt;85%/);
    assert.match(vm.runInContext("hodMetricLegend('util')",context),/70–&lt;80%/);
});

test('ranking tables escape recorded text and retain spare colours', () => {
    const context = load();
    context.rows = [{machine:'EX<1>',category:'ADT',is_spare:true,avail:85,explanation:'<script>bad</script>'}];
    const html = vm.runInContext("hodRankTable(rows,null,'avail',true)",context);
    assert.match(html,/hod-spare/);
    assert.match(html,/EX&lt;1&gt;/);
    assert.match(html,/hod-cell-green/);
    assert.ok(!html.includes('<script>'));
    assert.match(html,/&lt;script&gt;/);
});


test('gauges carry their threshold colours and font sizes into SVG image capture', () => {
    const context = load();
    const html = vm.runInContext("hodGauge('ADT','avail',{including:{avail:74},excluding:{avail:80}})",context);
    assert.match(html,/stroke="#e02f43"/);
    assert.match(html,/fill="#b42131" font-family="Arial" font-size="27" font-weight="800"/);
    assert.match(html,/stroke-dasharray="74 100"/);
    const missing = vm.runInContext("hodGauge('Dozer','util',{})",context);
    assert.match(missing,/stroke="#a0a8b2"/);
    assert.match(missing,/—/);
});

test('export includes only the unchanged dashboard for each selected site', async () => {
    const context = load();
    const sites = ['Uitgevallen','Klipfontein'];
    const boards = sites.map(site=>({dataset:{site},cloneNode:()=>({
        kind:`dashboard-${site}`,
        style:{},
        classList:{add(){}},
        querySelector: selector => (
            selector === '.hod-brand' || selector === '.hod-brand img'
                ? {style:{},src:''}
                : null
        ),
        querySelectorAll:()=>[]
    })}));
    let removed = 0, exported, error;
    context.document = {
        fonts:{ready:Promise.resolve()},body:{appendChild(){}},
        createElement:()=>({style:{},appendChild(){},remove(){removed++;},
            querySelector:()=>({kind:'fleet-summary',querySelectorAll:()=>[],querySelector:()=>null})})
    };
    context.frappe.dom = {freeze(){},unfreeze(){}};
    context.frappe.require = async()=>{};
    context.frappe.msgprint = value=>{error=value;};
    context.frappe.call = async options=>{
        exported=JSON.parse(options.args.captured_slides);
        return {message:{content:'pptx',filename:'sites.pptx'}};
    };
    context.captureHodSection = async element=>element.kind;
    context.downloadHodBase64File = ()=>{};
    context.view = {
        data:sites.map(site=>({site})),
        page:{main:{find:()=>({toArray:()=>boards})}},get_filter_value:()=>''
    };
    await vm.runInContext('downloadHodPresentation(view)',context);
    assert.equal(error,undefined);
    assert.equal(exported.length,2);
    assert.deepEqual(exported.map(s=>s.title),[
        'Uitgevallen · Production Performance','Klipfontein · Production Performance'
    ]);
    assert.deepEqual(exported.map(s=>s.heading_style),['dashboard','dashboard']);
    assert.deepEqual(exported.map(s=>s.image_data),['dashboard-Uitgevallen','dashboard-Klipfontein']);
    assert.equal(removed,2);
});
