function hodGetPreviousSunday(value) {
    const parts = String(value || "")
        .split("-")
        .map(Number);

    if (parts.length !== 3) {
        return value;
    }

    const date = new Date(
        parts[0],
        parts[1] - 1,
        parts[2]
    );

    const weekday = date.getDay();

    const daysBack =
        weekday === 0
            ? 7
            : weekday;

    date.setDate(
        date.getDate() - daysBack
    );

    const year = date.getFullYear();

    const month = String(
        date.getMonth() + 1
    ).padStart(2, "0");

    const day = String(
        date.getDate()
    ).padStart(2, "0");

    return `${year}-${month}-${day}`;
}


const hodPresentationPreviousSunday =
    hodGetPreviousSunday(
        frappe.datetime.get_today()
    );


const HOD_PRESENTATION_FILTERS = [
        {fieldname: "start_date", label: __("Start Date (optional)"), fieldtype: "Date",
         description: __("Defaults to the selected production planning period start.")},
        {
            fieldname: "end_date",
            label: __("End Date"),
            fieldtype: "Date",
            reqd: 1,
            default: hodPresentationPreviousSunday
        },
        {
            fieldname: "site",
            label: __("Sites"),
            fieldtype: "MultiSelectList",
            reqd: 1,
            get_data: function (txt) {
                return frappe.db.get_link_options("Location", txt);
            }
        },
        {
            fieldname: "summary_type",
            label: __("Summary Type"),
            fieldtype: "Select",
            options: [
                "Daily Summary",
                "Average Per Machine",
                "Weekly Summary",
                "Monthly Summary"
            ],
            reqd: 1,
            default: "Average Per Machine"
        },
        {
            fieldname: "machine_scope",
            label: __("Machine Scope"),
            fieldtype: "Select",
            options: [
                "Production Machines",
                "Swing/Spare Machines",
                "Include Swing/Spare"
            ],
            reqd: 1,
            default: "Include Swing/Spare"
        },
        {
            fieldname: "asset_ownership",
            label: __("Asset Ownership"),
            fieldtype: "Select",
            options: [
                "Isambane & Excavo Assets",
                "Suppliers Assets",
                "All Assets"
            ],
            reqd: 1,
            default: "Isambane & Excavo Assets"
        },
        {
            fieldname: "au_target_filter",
            label: __("A & U Target"),
            fieldtype: "Select",
            options: [
                "100% A & U",
                "85% A & U"
            ],
            reqd: 1,
            default: "85% A & U"
        }
    ];

frappe.pages["hod-presentation"].on_page_load = function (wrapper) {
    wrapper.hod_presentation = new HodPresentationPage(wrapper);
};

frappe.pages["hod-presentation"].on_page_show = function (wrapper) {
    return wrapper.hod_presentation.show();
};

class HodPresentationPage {
    constructor(wrapper) {
        this.page = frappe.ui.make_app_page({parent: wrapper, title: __("HOD Presentation"), single_column: true});
        this.filters = {};
        this.sequence = 0;
        this.applying_filters = true;
        const $filters = $('<div class="hod-page-filters row"></div>').appendTo(this.page.main);
        for (const definition of HOD_PRESENTATION_FILTERS) {
            const control = frappe.ui.form.make_control({
                parent: $('<div class="col-sm-6 col-lg-3"></div>').appendTo($filters),
                df: {...definition, onchange: () => this.filter_changed()},
                render_input: true
            });
            this.filters[definition.fieldname] = control;
        }
        this.$status = $('<div class="hod-page-status text-muted" role="status"></div>').appendTo(this.page.main);
        this.$content = $('<div class="hod-page-content"></div>').appendTo(this.page.main);
        this.view = {
            page: {main: this.$content}, data: [],
            get_filter_value: field => this.get_filter_value(field)
        };
        this.page.set_primary_action(__("Refresh"), () => this.refresh());
        this.$download = this.page.add_inner_button(__("Download Presentation"), () => {
            if (!this.$download.prop("disabled")) return downloadHodPresentation(this.view);
        }).prop("disabled", true);
        this.ready = this.set_defaults();
    }

    async set_defaults() {
        for (const definition of HOD_PRESENTATION_FILTERS) {
            if (definition.default !== undefined) {
                await this.filters[definition.fieldname].set_value(definition.default);
            }
        }
        this.applying_filters = false;
    }

    get_filter(field) { return this.filters[field]; }
    get_filter_value(field) { return this.filters[field]?.get_value(); }
    get_filter_values() {
        return Object.fromEntries(Object.keys(this.filters).map(field => [field, this.get_filter_value(field)]));
    }

    async show() {
        await this.ready;
        const options = frappe.route_options || {};
        frappe.route_options = null;
        this.applying_filters = true;
        try {
            for (const field of Object.keys(this.filters)) {
                if (options[field] !== undefined) {
                    const value = field === "site" ? normaliseHodSites(options[field]) : options[field];
                    await this.filters[field].set_value(value);
                }
            }
        } finally {
            this.applying_filters = false;
        }
        return this.refresh();
    }

    filter_changed() {
        if (this.applying_filters) return;
        ++this.sequence;
        this.$download.prop("disabled", true);
        this.$content.empty();
        clearTimeout(this.refresh_timer);
        this.refresh_timer = setTimeout(() => this.refresh(), 300);
    }

    async refresh() {
        clearTimeout(this.refresh_timer);
        const sequence = ++this.sequence;
        const filters = this.get_filter_values();
        this.$download.prop("disabled", true);
        this.$content.empty();
        this.view.data = [];
        this.$status.text(__("Loading HOD Presentation…"));
        try {
            const response = await frappe.call({
                method: "is_production.production.page.hod_presentation.hod_presentation.get_data",
                args: {filters: JSON.stringify(filters)}, freeze: false
            });
            if (sequence !== this.sequence) return;
            this.view.data = response.message?.result || [];
            if (!filters.end_date || !normaliseHodSites(filters.site).length) {
                this.$status.text(__("Select at least one site and an End Date."));
                return;
            }
            this.$status.text(this.view.data.length ? "" : __("No data matches the selected filters."));
            await renderHodPresentationLayout(this.view);
            if (sequence !== this.sequence) return;
            const complete = this.view.data.length > 0 &&
                this.$content.find(".hod-performance-dashboard").length === this.view.data.length;
            this.$download.prop("disabled", !complete);
        } catch (error) {
            if (sequence !== this.sequence) return;
            this.$content.empty();
            this.$status.text(__("Unable to load HOD Presentation. Check your filters and access permissions."));
        }
    }
}


function normaliseHodSites(value) {
    if (Array.isArray(value)) {
        return value
            .map(item => {
                if (typeof item === "object" && item !== null) {
                    return item.value || item.name || "";
                }

                return String(item || "");
            })
            .map(item => item.trim())
            .filter(Boolean);
    }

    if (!value) {
        return [];
    }

    if (typeof value === "string") {
        const trimmed = value.trim();

        if (!trimmed) {
            return [];
        }

        if (trimmed.startsWith("[")) {
            try {
                const parsed = JSON.parse(trimmed);

                if (Array.isArray(parsed)) {
                    return parsed
                        .map(item => {
                            if (typeof item === "object" && item !== null) {
                                return item.value || item.name || "";
                            }

                            return String(item || "");
                        })
                        .map(item => item.trim())
                        .filter(Boolean);
                }
            } catch (error) {
                console.warn("Could not parse selected HOD sites.", error);
            }
        }

        return trimmed
            .split(",")
            .map(item => item.trim())
            .filter(Boolean);
    }

    return [String(value).trim()].filter(Boolean);
}



const HOD_SITE_COMPLEXES = [
    {
        name: "Middelburg Complex",
        sites: [
            "Klipfontein",
            "Gwab",
            "Kriel Rehabilitation",
            "Bankfontein"
        ]
    },
    {
        name: "Ermelo Complex",
        sites: [
            "Uitgevallen",
            "Koppie"
        ]
    }
];


function hodGetComplexName(site) {
    const siteName = String(site || "").trim();

    const complex = HOD_SITE_COMPLEXES.find(item =>
        item.sites.includes(siteName)
    );

    return complex ? complex.name : "Other Sites";
}


const HOD_CATEGORIES = {ADT: 'ADTs', Dozer: 'Dozers', Excavator: 'Excavators'};
const HOD_MACHINE_IMAGES = {ADT: 'mining-truck.png', Dozer: 'dozer.png', Excavator: 'excavator (1).png'};

function hodEscape(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function hodValue(value) {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
}
function hodNumber(value, decimals = 0, signed = false) {
    const number = hodValue(value);
    if (number === null) return '—';
    return (signed && number > 0 ? '+' : '') + number.toLocaleString('en-ZA', {
        minimumFractionDigits: decimals, maximumFractionDigits: decimals
    });
}
function hodPercent(value) { return hodValue(value) === null ? '—' : `${hodNumber(value, 1)}%`; }
function hodBand(metric, value) {
    value = hodValue(value);
    if (value === null) return 'neutral';
    const [low, high] = metric === 'avail' ? [75,85] : [70,80];
    return value < low ? 'red' : value < high ? 'amber' : 'green';
}
function hodAveragePair(averages, metric) {
    return ['excluding','including'].map((cohort,index)=>{
        const value = averages?.[cohort]?.[metric];
        const colour = hodValue(value) === null ? 'neutral-text' : index === 0 ? 'production' : 'spare';
        return `<span class="hod-${colour}">${hodPercent(value)}</span>`;
    }).join(' / ');
}
function hodGauge(category, metric, averages) {
    const value = hodValue(averages?.including?.[metric]);
    const progress = Math.min(100, Math.max(0, value ?? 0));
    const band = hodBand(metric, value);
    const stroke = {red:'#e02f43',amber:'#f1a900',green:'#19a557',neutral:'#a0a8b2'}[band];
    const text = {red:'#b42131',amber:'#a16a00',green:'#167743',neutral:'#677489'}[band];
    return `<div class="hod-gauge" data-category="${category}" data-metric="${metric}" data-value="${value ?? ''}">
        <div class="hod-gauge-category"><img src="/assets/is_production/images/${encodeURIComponent(HOD_MACHINE_IMAGES[category])}" alt=""><strong>${HOD_CATEGORIES[category]}</strong></div>
        <svg viewBox="0 0 180 105" role="img" aria-label="${HOD_CATEGORIES[category]} ${metric === 'avail' ? 'availability' : 'utilisation'} ${hodPercent(value)}">
            <path d="M 20 80 A 70 70 0 0 1 160 80" pathLength="100" fill="none" stroke="#e3e8ef" stroke-width="13"/>
            <path class="hod-gauge-${band}" d="M 20 80 A 70 70 0 0 1 160 80" pathLength="100" fill="none" stroke="${stroke}" stroke-width="13" stroke-dasharray="${progress} 100"/>
            <text x="18" y="99" class="hod-gauge-tick" fill="#24476c" font-family="Arial" font-size="10">0</text><text x="149" y="99" class="hod-gauge-tick" fill="#24476c" font-family="Arial" font-size="10">100</text>
            <text x="90" y="53" text-anchor="middle" fill="#17365d" font-family="Arial" font-size="9" font-weight="800">PROD + SWING</text>
            <text x="90" y="79" text-anchor="middle" class="hod-gauge-value hod-text-${band}" fill="${text}" font-family="Arial" font-size="27" font-weight="800">${hodPercent(value)}</text>
        </svg><div class="hod-gauge-average" style="display:grid;grid-template-columns:1fr 1fr;gap:4px;text-align:center;padding:4px 2px">
            <div>
                <small style="display:block;font-size:9px;font-weight:600">Prod Machines</small>
                <strong style="display:block;font-size:13px;font-weight:800;line-height:1.3">${hodPercent(averages?.excluding?.[metric])}</strong>
            </div>
            <div>
                <small style="display:block;font-size:9px;font-weight:600">Swing Machines</small>
                <strong style="display:block;font-size:13px;font-weight:800;line-height:1.3">${hodPercent(averages?.swing?.[metric])}</strong>
            </div>
        </div>
    </div>`;
}
function hodMetricLegend(metric) {
    const ranges = metric === 'avail' ? ['<75%', '75–<85%', '≥85%'] : ['<70%', '70–<80%', '≥80%'];
    return `<span class="hod-thresholds">${ranges.map((range,i)=>`<span><i class="hod-dot hod-dot-${['red','amber','green'][i]}"></i>${hodEscape(range)}</span>`).join('')}</span>`;
}
function hodRankTable(rows, category, metric, bottom = false) {
    const label = metric === 'avail' ? 'Availability' : 'Utilisation';
    const title = bottom ? `Bottom 10 ${label.toLowerCase()} · all categories` : `Top 5 ${HOD_CATEGORIES[category]}`;
    const description = metric === 'avail' ? 'Breakdown cause' : 'Main utilisation problem';
    const columns = bottom ? (metric === 'avail' ? 5 : 4) : 3;
    const body = rows.map((row,index) => {
        const colour = row.is_spare ? 'spare' : 'production';
        return `<tr class="hod-machine-${colour}" data-fleet="${hodEscape(row.machine)}" data-category="${hodEscape(row.category)}" data-value="${row[metric]}">
            <td>${index+1}</td><th scope="row" class="hod-${colour}">${hodEscape(row.machine)}</th>
            ${bottom ? `<td>${hodEscape(row.category)}</td>` : ''}
            <td class="hod-cell-${hodBand(metric,row[metric])}">${hodPercent(row[metric])}</td>
            ${bottom && metric === 'avail' ? `<td class="hod-explanation"><button type="button" class="hod-view-breakdown-detail" data-fleet="${hodEscape(row.machine)}" data-reason="${hodEscape(row.explanation || 'Not recorded')}">View Detail</button></td>` : ''}</tr>`;
    }).join('') || `<tr><td colspan="${columns}" class="hod-no-values">No valid values</td></tr>`;
    return `<section class="hod-ranking ${bottom ? 'hod-ranking-bottom' : ''}" data-ranking="${bottom ? 'bottom' : 'top'}" data-metric="${metric}" ${category ? `data-category="${category}"` : ''}>
        <h4>${title}${bottom ? '' : `<small>${label}</small>`}</h4>
        <table><thead><tr><th>#</th><th>Fleet</th>${bottom ? '<th>Category</th>' : ''}<th>${bottom ? (metric === 'avail' ? 'Avail.' : 'Util.') : label} %</th>${bottom && metric === 'avail' ? `<th>${description}</th>` : ''}</tr></thead><tbody>${body}</tbody></table></section>`;
}
function hodRankings(engineering, metric) {
    return hodRankTable(engineering.bottom[metric], null, metric, true);
}
function hodProduction(row) {
    const metrics = [
        ['Monthly target','monthly_target_bcm','BCM'], ['Forecast','forecast_bcm','BCM'],
        ['Waste variance','waste_variance_bcm','BCM',0,true], ['Coal variance','coal_variance_tons',"TON's",0,true],
        ['Actual BCMs','actual_bcm','BCM'], ['Actual coal','actual_coal_tons',"TON's"],
        ['Daily required','daily_required_bcm','BCM/D',1], ['Daily achieved','daily_achieved_bcm','BCM/D',1],
        ['Average BCM/hour','average_bcm_h','BCM/H',1], ['Days worked / left',null,'Days',1],
        ['Strip ratio','strip_ratio','BCM/T',1], ['Forecast delivery','forecast_delivery_percent','%',1]
    ];
    const variance = hodValue(row.forecast_variance_bcm);
    return `<div class="hod-site-strip">${hodEscape(row.site)}</div>
        <div class="hod-forecast ${variance === null ? '' : variance >= 0 ? 'hod-positive' : 'hod-negative'}"><span>Forecast variance</span><strong>${hodNumber(variance,0,true)} BCM</strong></div>
        <table class="hod-production-table"><tbody>${metrics.map(([label,field,unit,decimals=0,signed=false]) => {
            const value = field ? row[field] : null;
            const colour = signed && hodValue(value) !== null ? (Number(value) >= 0 ? 'hod-positive' : 'hod-negative') : '';
            return `<tr data-production-field="${field || 'days_worked_left'}"><th scope="row">${label}</th><td>${unit}</td><td class="${colour}">${field ? hodNumber(value,decimals,signed)+(unit==='%' && hodValue(value)!==null ? '%' : '') : `${hodNumber(row.days_worked,1)} / ${hodNumber(row.days_left,1)}`}</td></tr>`;
        }).join('')}</tbody></table>`;
}
function hodDashboard(row) {
    const engineering = row.engineering;
    return `<article class="hod-performance-dashboard" data-site="${hodEscape(row.site)}">
        <header class="hod-brand"><img src="/assets/is_production/images/isambane_logo.png" alt="Isambane Mining"><h2>${hodEscape(hodGetComplexName(row.site))} · Production Performance</h2></header>
        <div class="hod-period"><strong>${hodEscape(row.site)}</strong><span>${hodEscape(row.period)}</span></div>
        <div class="hod-columns">
            <section class="hod-domain-panel hod-engineering-panel">

                <div class="hod-domain-heading" style="margin-top:8px;display:block;height:auto;min-height:85px;padding:10px 12px;overflow:visible">
                        <div style="display:flex;align-items:center;justify-content:space-between">
                            <h3>Total Site Fleet</h3>
                            <button type="button" class="hod-view-plant-detail" data-site="${hodEscape(row.site)}" data-from="${hodEscape(row.start_date)}" data-to="${hodEscape(row.end_date)}" style="background:white;color:#075583;border:0;border-radius:4px;padding:3px 9px;font-size:11px;font-weight:700;cursor:pointer">View Detail</button>
                        </div>
                        <div style="color:white;font-size:10px;font-weight:500;text-transform:uppercase">
                            Production and Swing Machines
                        </div>
                        <div style="display:flex;justify-content:flex-start;align-items:center;flex-wrap:wrap;gap:24px;margin-top:5px;color:white;font-weight:600;font-size:12px;text-align:left">
                            <span>Excavator = ${engineering.fleet_counts?.Excavator ?? 0}</span>
                            <span>Dozer = ${engineering.fleet_counts?.Dozer ?? 0}</span>
                            <span>ADT = ${engineering.fleet_counts?.ADT ?? 0}</span>
                        </div>
                    </div>

                <div class="hod-domain-heading"><div><h3>Engineering</h3><span>Availability &amp; utilisation</span></div><div class="hod-header-legend"><span>Availability ${hodMetricLegend('avail')}</span><span>Utilisation ${hodMetricLegend('util')}</span></div></div>
                <div class="hod-domain-main hod-engineering-main"><h4 style="display:flex;align-items:center;justify-content:space-between">
                        <span>Fleet averages · all filtered machines</span>
                        <button type="button" class="hod-view-au-detail" data-site="${hodEscape(row.site)}" data-from="${hodEscape(row.start_date)}" data-to="${hodEscape(row.end_date)}">View Detail</button>
                    </h4>
                    ${['avail','util'].map(metric=>`<div class="hod-gauge-row"><strong class="hod-gauge-label">${metric==='avail'?'Availability':'Utilisation'}</strong>${['Excavator','Dozer','ADT'].map(category=>hodGauge(category,metric,engineering.category_averages[category])).join('')}</div>`).join('')}
                    </div>
${hodRankings(engineering,'avail')}
            </section>
            <section class="hod-domain-panel hod-production-panel">
                <div class="hod-domain-heading"><div><h3>Production</h3><span>Production performance</span></div></div>
                <div class="hod-domain-main">${hodProduction(row)}</div>${hodRankings(engineering,'util')}
            </section>
        </div>

    </article>`;
}
function alignHodDomainHeadings(root) {
    const dashboards = root.find('.hod-performance-dashboard');

    dashboards.each(function () {
        const board = this;
        const engineering = board.querySelector(
            '.hod-engineering-panel > .hod-domain-heading:nth-child(2)'
        );
        const productionPanel = board.querySelector('.hod-production-panel');

        if (!engineering || !productionPanel) return;

        productionPanel.style.paddingTop = '0px';

        const productionHeading = productionPanel.querySelector(
            '.hod-domain-heading:first-child'
        );

        if (!productionHeading) return;

        const difference =
            engineering.getBoundingClientRect().top -
            productionHeading.getBoundingClientRect().top;

        productionPanel.style.paddingTop =
            Math.max(0, difference) + 'px';
    });
}

function bindHodDetailButtons(view) {
    view.page.main.find('.hod-view-breakdown-detail')
        .off('click')
        .on('click', async function () {
            const fleet = $(this).attr('data-fleet') || '';
            const site = $(this).closest('.hod-performance-dashboard').attr('data-site') || '';
            // Use the actual reporting period returned for this site.
            // The Start Date filter is optional.
            const siteRow = (view.data || []).find(
                row => row.site === site
            );
            const fromDate = siteRow?.start_date || '';
            const toDate = siteRow?.end_date || '';

            if (!fromDate || !toDate) {
                frappe.msgprint({
                    title: __('Breakdown Reasons'),
                    message: __('The reporting period is unavailable. Refresh the HOD report.'),
                    indicator: 'red'
                });
                return;
            }
            const escape = value => frappe.utils.escape_html(String(value ?? ''));

            const formatMinutes = value => {
                const minutes = Math.max(0, Math.round(Number(value) || 0));
                const hours = Math.floor(minutes / 60);
                const remainder = minutes % 60;
                return hours
                    ? `${hours}h${remainder ? ` ${remainder}m` : ''}`
                    : `${remainder}m`;
            };

            const dialog = new frappe.ui.Dialog({
                title: __('Breakdown Reasons - {0}', [fleet]),
                size: 'extra-large',
                fields: [{
                    fieldname: 'details',
                    fieldtype: 'HTML'
                }]
            });

            dialog.show();
            dialog.fields_dict.details.$wrapper.html(
                '<div style="padding:12px">Loading breakdown details...</div>'
            );

            try {
                const result = await frappe.xcall(
                    'is_production.production.page.hod_presentation.hod_presentation.get_hod_breakdown_details',
                    {
                        machine: fleet,
                        site: site,
                        from_date: fromDate,
                        to_date: toDate
                    }
                );

                const rows = result?.rows || [];

                const body = rows.map(row => {
                    const startTime = escape(row.start_datetime || '-');
                    const resolvedTime = escape(row.resolved_datetime || 'Open');
                    const total = formatMinutes(row.total_minutes);
                    const excluded = formatMinutes(row.startup_fatigue_minutes);
                    const sunday = formatMinutes(row.sunday_minutes);
                    const au = formatMinutes(row.au_minutes);
                    const reason = escape(row.reason || 'Not recorded');

                    return `<tr>
                        <td>${startTime}</td>
                        <td>${resolvedTime}</td>
                        <td><strong>${total}</strong></td>
                        <td style="color:#c2410c;font-weight:700">${excluded}</td>
                        <td style="color:#7c3aed;font-weight:700">${sunday}</td>
                        <td style="color:#047857;font-weight:700">${au}</td>
                        <td>${reason}</td>
                    </tr>`;
                }).join('');

                const html = `
                    <div style="max-height:65vh;overflow:auto">
                        <div style="padding:10px;background:#dbeafe;color:#1d4ed8;font-weight:700">
                            Breakdown Reasons for ${escape(fleet)}
                        </div>
                        <table class="table table-bordered"
                               style="width:100%;font-size:12px;white-space:nowrap">
                            <thead style="background:#eff6ff;color:#1d4ed8;position:sticky;top:0">
                                <tr>
                                    <th>Start</th>
                                    <th>Resolved</th>
                                    <th>Total Time</th>
                                    <th>Start-up + Fatigue</th>
                                    <th>Sunday Hours</th>
                                    <th>A&amp;U Time</th>
                                    <th>Reason</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${body || '<tr><td colspan="7">No breakdown records for the selected period.</td></tr>'}
                            </tbody>
                        </table>
                    </div>`;

                dialog.fields_dict.details.$wrapper.html(html);

            } catch (error) {
                dialog.fields_dict.details.$wrapper.text(
                    error.message || 'Unable to load breakdown details.'
                );
            }
        });

    view.page.main.find('.hod-view-au-detail').off('click').on('click', function () {
        const button = $(this);

        const params = new URLSearchParams({
            from_date: button.attr('data-from') || '',
            to_date: button.attr('data-to') || '',
            location: button.attr('data-site') || '',
            asset_category: 'All Equipment',
            machine_scope: 'Production + Swing/Spare Machines',
            asset_ownership: 'All Assets',
            au_target_filter: '85% A & U'
        });

        const url = '/app/query-report/' +
            encodeURIComponent('Availability and Utilisation Month End Report') +
            '?' + params.toString();

        window.open(url, '_blank', 'noopener');
    });

    view.page.main.find('.hod-view-plant-detail')
        .off('click')
        .on('click', function () {
            const site = $(this).attr('data-site') || '';
            const from = $(this).attr('data-from') || '';
            const to = $(this).attr('data-to') || '';

            if (!site || !from || !to) {
                frappe.msgprint(__('Site and reporting dates are required.'));
                return;
            }

            const params = new URLSearchParams({
                site: site,
                from_date: from,
                to_date: to
            });

            window.open(
                '/hod-machine-plant-list?' + params.toString(),
                '_blank',
                'noopener'
            );
        });
}

async function renderHodPresentationLayout(view) {
    view.page.main.html(view.data.map(hodDashboard).join(''));
    bindHodDetailButtons(view);
    alignHodDomainHeadings(view.page.main);
}
async function downloadHodPresentation(view) {
    const boards = view.page.main.find('.hod-performance-dashboard').toArray();
    if (!boards.length) return;
    frappe.dom.freeze(__('Generating presentation…'));
    try {
        await frappe.require('/assets/is_production/js/html-to-image.js');
        const slides = [];
        for (const board of boards) {
            const root = document.createElement('div');
            root.style.cssText = 'position:fixed;left:-20000px;top:0;width:1600px;background:white';
            const clone = board.cloneNode(true);
            clone.style.width = '1600px';
            clone.classList.add('hod-export-dashboard');

            // Enlarge the logo inside its existing header position.
            // Do not change the header or dashboard dimensions.
            const exportLogo = clone.querySelector('.hod-brand img');
            if (exportLogo) {
                exportLogo.style.width = '220px';
                exportLogo.style.height = '65px';
                exportLogo.style.objectFit = 'contain';
                exportLogo.style.flexShrink = '0';
                exportLogo.style.transform = 'scale(1.3)';
                exportLogo.style.transformOrigin = 'left center';
            }

            // PowerPoint frame: black left and bottom only.
            // The dashboard already includes its black top header.
            clone.style.borderLeft = '24px solid #050505';
            clone.style.borderBottom = '24px solid #050505';
            clone.style.borderRight = '0';
            clone.style.borderRadius = '0';
            clone.style.boxSizing = 'border-box';

            root.appendChild(clone); document.body.appendChild(root);
            try {
                await document.fonts.ready;
                await Promise.all([...clone.querySelectorAll('img')].map(img=>img.decode()));
                // Capture actual View Detail button positions.
                const dashboardRect = clone.getBoundingClientRect();
                const linkAreas = [
                    ['au', '.hod-view-au-detail'],
                    ['fleet', '.hod-view-plant-detail']
                ].map(([kind, selector]) => {
                    const button = clone.querySelector(selector);
                    if (!button) return null;
                    const rect = button.getBoundingClientRect();
                    return {
                        kind,
                        x: (rect.left - dashboardRect.left) / dashboardRect.width,
                        y: (rect.top - dashboardRect.top) / dashboardRect.height,
                        w: rect.width / dashboardRect.width,
                        h: rect.height / dashboardRect.height
                    };
                }).filter(Boolean);

                // Capture every Bottom 10 Availability detail button.
                clone.querySelectorAll('.hod-view-breakdown-detail')
                    .forEach(button => {
                        const rect = button.getBoundingClientRect();
                        linkAreas.push({
                            kind: 'breakdown',
                            machine: button.dataset.fleet || '',
                            x: (rect.left - dashboardRect.left) / dashboardRect.width,
                            y: (rect.top - dashboardRect.top) / dashboardRect.height,
                            w: rect.width / dashboardRect.width,
                            h: rect.height / dashboardRect.height
                        });
                    });

                const siteRow = (view.data || []).find(
                    row => row.site === board.dataset.site
                );

                slides.push({title:`${board.dataset.site} · Production Performance`, heading_style:'dashboard',
                    site:board.dataset.site,
                    start_date:siteRow?.start_date || '',
                    end_date:siteRow?.end_date || '',
                    link_areas:linkAreas,
                    image_data:await captureHodSection(clone,{width:1600,pixelRatio:1.5,quality:0.96})});
            } finally { root.remove(); }
        }
        const response = await frappe.call({
            method:'is_production.production.report.hod_presentation.hod_presentation.download_captured_presentation',
            args:{captured_slides:JSON.stringify(slides),site:boards.map(b=>b.dataset.site).join(' / '),
                start_date:view.get_filter_value('start_date'),end_date:view.get_filter_value('end_date')},freeze:false
        });
        downloadHodBase64File(response.message.content,response.message.filename);
    } catch (error) {
        frappe.msgprint({title:__('Presentation export failed'),message:hodEscape(error.message),indicator:'red'});
    } finally { frappe.dom.unfreeze(); }
}

async function captureHodSection(element, options = {}) {
    if (!element) {
        throw new Error(
            __("The report section was not found.")
        );
    }

    const captureWidth = Math.max(
        Number(options.width || 0),
        element.scrollWidth,
        element.offsetWidth,
        element.clientWidth,
        1
    );

    const captureHeight = Math.max(
        element.scrollHeight,
        element.offsetHeight,
        element.clientHeight,
        1
    );

    const capturePromise =
        window.htmlToImage.toJpeg(
            element,
            {
                quality:
                    options.quality || 0.82,
                pixelRatio:
                    options.pixelRatio || 0.75,
                backgroundColor: "#f7f8fa",
                cacheBust: true,
                width: captureWidth,
                height: captureHeight,

                style: {
                    overflow: "visible"
                },

                filter: node => {
                    if (!node) {
                        return true;
                    }

                    const tagName = (
                        node.tagName || ""
                    ).toLowerCase();

                    if (
                        tagName === "script" ||
                        tagName === "iframe" ||
                        tagName === "grammarly-extension"
                    ) {
                        return false;
                    }

                    if (
                        node.hasAttribute &&
                        (
                            node.hasAttribute(
                                "data-gramm"
                            ) ||
                            node.hasAttribute(
                                "data-gramm_editor"
                            ) ||
                            node.hasAttribute(
                                "data-grammarly-shadow-root"
                            )
                        )
                    ) {
                        return false;
                    }

                    const className =
                        typeof node.className === "string"
                            ? node.className
                            : "";

                    if (
                        className.includes(
                            "grammarly"
                        ) ||
                        className.includes(
                            "gr_"
                        )
                    ) {
                        return false;
                    }

                    return true;
                }
            }
        );

    const timeoutPromise =
        new Promise(
            (_, reject) => {
                window.setTimeout(
                    () => {
                        reject(
                            new Error(
                                __(
                                    "Capturing a presentation slide exceeded 45 seconds."
                                )
                            )
                        );
                    },
                    45000
                );
            }
        );

    return Promise.race([
        capturePromise,
        timeoutPromise
    ]);
}


function downloadHodBase64File(

    base64Content,
    filename
) {
    if (!base64Content) {
        throw new Error(
            __("No PowerPoint file was returned.")
        );
    }

    const binary = window.atob(
        base64Content
    );

    const bytes = new Uint8Array(
        binary.length
    );

    for (
        let index = 0;
        index < binary.length;
        index += 1
    ) {
        bytes[index] = binary.charCodeAt(
            index
        );
    }

    const blob = new Blob(
        [bytes],
        {
            type:
                "application/vnd.openxmlformats-" +
                "officedocument.presentationml.presentation"
        }
    );

    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");

    link.href = url;
    link.download =
        filename ||
        "HOD_Presentation.pptx";

    document.body.appendChild(link);
    link.click();
    link.remove();

    window.setTimeout(() => {
        URL.revokeObjectURL(url);
    }, 1000);
}
