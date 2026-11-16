const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(
    'is_production/production/page/hod_presentation/hod_presentation.js',
    'utf8'
);

const start = source.indexOf('function hodValue(');
const end = source.indexOf('function hodMetricLegend(');

assert.ok(start >= 0, 'hodValue function missing');
assert.ok(end > start, 'Gauge functions missing');

const gaugeSource = source.slice(start, end);

const context = {
    HOD_MACHINE_IMAGES: {
        Excavator: 'excavator.png',
        Dozer: 'dozer.png',
        ADT: 'adt.png'
    },
    HOD_CATEGORIES: {
        Excavator: 'Excavators',
        Dozer: 'Dozers',
        ADT: 'ADTs'
    }
};

vm.createContext(context);
vm.runInContext(gaugeSource, context);

function render(production, overall, swing, metric = 'avail') {
    return context.hodGauge(
        'Excavator',
        metric,
        {
            excluding: production,
            including: overall
        },
        {
            swing
        }
    );
}

test('Availability chooses higher Overall value', () => {
    const html = render(
        {avail: 44.605},
        {avail: 75.453},
        {avail: 75.453}
    );
    assert.match(html, /data-value="75.453"/);
});

test('Availability chooses higher Production value', () => {
    const html = render(
        {avail: 72.826},
        {avail: 64.651},
        {avail: 64.651}
    );
    assert.match(html, /data-value="72.826"/);
});

test('Utilisation chooses Production independently', () => {
    const html = render(
        {util: 63.829},
        {util: 57.034},
        {util: 57.034},
        'util'
    );
    assert.match(html, /data-value="63.829"/);
});

test('Missing Production uses available Overall', () => {
    const html = render(
        {avail: null},
        {avail: 80},
        {avail: 70}
    );
    assert.match(html, /data-value="80"/);
});

test('Missing both values shows unavailable', () => {
    const html = render(
        {avail: null},
        {avail: null},
        {avail: null}
    );
    assert.match(html, /data-value=""/);
});

test('Gauge percentage is capped at 100', () => {
    const html = render(
        {avail: 105},
        {avail: 99},
        {avail: 80}
    );
    assert.match(html, /data-value="100"/);
});

test('Swing percentage remains independently displayed', () => {
    const html = render(
        {avail: 44.605},
        {avail: 75.453},
        {avail: 64.651}
    );

    assert.match(html, /data-value="75.453"/);
    assert.match(html, /64,7%/);
});
