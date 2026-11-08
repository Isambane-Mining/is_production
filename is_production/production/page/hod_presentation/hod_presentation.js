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

        // HOD_DEDICATED_CSS_V1
        // Load HOD styling before rendering the dashboard.
        if (typeof frappe.require === "function") {
            await frappe.require(
                "/assets/is_production/css/hod_presentation.css"
            );
        }
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
    const [low, high] = metric === 'avail' ? [75,85] : [70,85];
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
    // HOD_GAUGE_MAX_SCOPE_V1
    // Display the higher Production or Swing percentage.
    // Keep underlying A&U calculations unchanged.
    const productionValue = hodValue(averages?.excluding?.[metric]);
    const swingValue = hodValue(averages?.swing?.[metric]);
    const availableValues = [productionValue, swingValue]
        .filter(value => value !== null);
    const value = availableValues.length
        ? Math.max(...availableValues)
        : null;
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
        </svg><div class="hod-gauge-average">
            <div>
                <small class="hod-gauge-scope-label">Prod Machines</small>
                <strong class="hod-gauge-scope-value">${hodPercent(averages?.excluding?.[metric])}</strong>
            </div>
            <div>
                <small class="hod-gauge-scope-label">Swing Machines</small>
                <strong class="hod-gauge-scope-value">${hodPercent(averages?.swing?.[metric])}</strong>
            </div>
        </div>
    </div>`;
}
function hodMetricLegend(metric) {
    const ranges = metric === 'avail' ? ['<75%', '75–<85%', '≥85%'] : ['<70%', '70–<85%', '≥85%'];
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
// Average BCM/H original HOD orange threshold.
if (typeof document !== "undefined" &&
    !document.getElementById("hod-average-bcm-amber-style")) {
    const style = document.createElement("style");
    style.id = "hod-average-bcm-amber-style";
    style.textContent =
        ".hod-performance-dashboard " +
        ".hod-production-table td.hod-average-bcm-amber {" +
        "color:#a66a00 !important;}";
    document.head.appendChild(style);
}

function hodProduction(row) {
    const metrics = [
        ['Monthly target','monthly_target_bcm','BCM'], ['Forecast','forecast_bcm','BCM'],
        ['Waste variance','waste_variance_bcm','BCM',0,true], ['Coal variance','coal_variance_tons',"TON's",0,true],
        ['Actual BCMs','actual_bcm','BCM'], ['Actual coal','actual_coal_tons',"TON's"],
        ['Daily required','daily_required_bcm','BCM/D',1], ['Daily achieved','daily_achieved_bcm','BCM/D',1],
        ['Average BCM/hour','average_bcm_h','BCM/H',1], ['Days worked / left',null,'Days',1],
        ['Strip ratio','strip_ratio','BCM/T',1], ['Forecast delivery','forecast_delivery_percent','%',1],
        // HOD_BCM_PER_MAN_V1
        ['BCM/Man','bcm_per_man','BCM',1]
    ];
    const variance = hodValue(row.forecast_variance_bcm);
    return `<div class="hod-site-strip">${hodEscape(row.site)}</div>
        <div class="hod-forecast ${variance === null ? '' : variance >= 0 ? 'hod-positive' : 'hod-negative'}"><span>Forecast variance</span><strong>${hodNumber(variance,0,true)} BCM</strong></div>
        <table class="hod-production-table"><tbody>${metrics.map(([label,field,unit,decimals=0,signed=false]) => {
            const value = field ? row[field] : null;
            // HOD_AVERAGE_BCM_COLOUR_V1
            // Original HOD thresholds: Green >=220, Orange >=200, Red <200.
            let colour = signed && hodValue(value) !== null
                ? (Number(value) >= 0 ? 'hod-positive' : 'hod-negative')
                : '';

            if (field === 'average_bcm_h' && hodValue(value) !== null) {
                colour = Number(value) >= 220
                    ? 'hod-positive'
                    : Number(value) >= 200
                        ? 'hod-average-bcm-amber'
                        : 'hod-negative';
            }
            return `<tr data-production-field="${field || 'days_worked_left'}"><th scope="row">${label}</th><td>${unit}</td><td class="${colour}">${field ? hodNumber(value,decimals,signed)+(unit==='%' && hodValue(value)!==null ? '%' : '') : `${hodNumber(row.days_worked,1)} / ${hodNumber(row.days_left,1)}`}</td></tr>`;
        }).join('')}</tbody></table>`;
}

function hodClassificationIcon(classification) {
    const name = String(classification || "").toLowerCase();

    // Icons only; categories and totals always come from ERP data.
    if (/electrical|electric|electronic/.test(name)) return "⚡";
    if (/hydraulic/.test(name)) return "💧";
    if (/engine/.test(name)) return "🔧";
    if (/tyre|tire|wheel/.test(name)) return "🛞";
    if (/transmission|drivetrain/.test(name)) return "⚙";
    if (/brake|braking/.test(name)) return "🛑";
    if (/planned maintenance|service/.test(name)) return "🗓";
    if (/structural/.test(name)) return "🏗";
    if (/fuel/.test(name)) return "⛽";
    if (/cooling/.test(name)) return "🌡";
    if (/mechanical/.test(name)) return "🔩";

    return "⚙";
}

function hodBreakdownBarChart(classifications) {
    if (!classifications.length) {
        return `<div class="hod-breakdown-chart-empty">
            <span class="hod-breakdown-chart-message">
                No downtime records for this period
            </span>
        </div>`;
    }

    const maximum = Math.max(
        ...classifications.map(item => Number(item.hours) || 0), 0
    );

    const totalHours = classifications.reduce(
        (sum, item) => sum + (Number(item.hours) || 0), 0
    );

    // HOD_DOWNTIME_TWO_COLUMNS_V2
    // Two columns, filling from top to bottom.
    // Existing clickable breakdown buttons remain unchanged.
    const rowCount = classifications.length;
    const rowsPerColumn = Math.ceil(rowCount / 2);

    return `<div class="hod-breakdown-bars"
        style="
            display:grid;
            grid-template-columns:repeat(2,minmax(0,1fr));
            grid-template-rows:repeat(${rowsPerColumn},auto);
            grid-auto-flow:column;
            column-gap:12px;
            row-gap:4px;
            align-items:start;
        ">
        ${classifications.map((item, index) => {
            const hours = Number(item.hours) || 0;
            const width = totalHours > 0 ? hours / totalHours * 100 : 0;

            // HOD_CLASSIFICATION_BAR_COLOURS_V1
            const colours = [
                "#1688e8", "#fa8b22", "#17ad61",
                "#e54868", "#8e55d8", "#e7a600",
                "#1c9ed5", "#21a58b", "#db5e2b",
                "#7666d8", "#c44b8a", "#3478c5",
                "#d59b17", "#299d64", "#ad62d6",
                "#0e9ca8", "#d64c64", "#7c8f28"
            ];

            const colour = colours[index % colours.length];

            const percentage = totalHours > 0
                ? hours / totalHours * 100 : 0;

            return `<button type="button"
                class="hod-breakdown-bar-row hod-downtime-bar-button"
                data-hod-class-index="${index}"
                aria-expanded="false"
                title="Show ${hodEscape(item.classification)} details">
                <span class="hod-breakdown-bar-label">
                    <span class="hod-breakdown-class-icon">
                        ${hodClassificationIcon(item.classification)}
                    </span>
                    <span class="hod-breakdown-class-text">
                        ${hodEscape(item.classification)}
                    </span>
                </span>
                <span class="hod-breakdown-bar-track"
                   >
                    <span class="hod-breakdown-bar-fill"
                        style="width:${width.toFixed(2)}%;
                            background-color:${colour} !important;
                            background-image:none !important;
                            display:block;
                            height:100%;
                            min-height:9px;
                            opacity:1 !important">
                    </span>
                </span>
                <strong>${percentage.toFixed(1)}%</strong>
            </button>`;
        }).join('')}
    </div>`;
}


// HOD_POPUP_EXPORT_BUTTONS_V1
// Export the actual rendered popup table, without recalculating downtime.

function hodPopupExportRows(table) {
    const clean = value =>
        String(value || "").replace(/\s+/g, " ").trim();

    const rows = [];

    const headers = Array.from(
        table.querySelectorAll("thead th")
    ).map(cell => clean(cell.textContent));

    rows.push(headers);

    table.querySelectorAll("tbody tr").forEach(tr => {
        const values = [];

        Array.from(tr.children).forEach(cell => {
            values.push(clean(cell.textContent));

            for (let n = 1; n < (cell.colSpan || 1); n++) {
                values.push("");
            }
        });

        rows.push(values);
    });

    return rows;
}

function hodPopupMakeXlsx(rows) {
    const encode = value =>
        new TextEncoder().encode(String(value));

    const xmlEscape = value =>
        String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&apos;");

    const columnName = index => {
        let result = "";
        let number = index + 1;

        while (number > 0) {
            number--;
            result = String.fromCharCode(
                65 + number % 26
            ) + result;
            number = Math.floor(number / 26);
        }

        return result;
    };

    const sheetRows = rows.map((cells, rowIndex) => {
        const xmlCells = cells.map((value, colIndex) => {
            const address =
                columnName(colIndex) + (rowIndex + 1);

            return `<c r="${address}" t="inlineStr">` +
                `<is><t xml:space="preserve">` +
                `${xmlEscape(value)}</t></is></c>`;
        }).join("");

        return `<row r="${rowIndex + 1}">` +
            xmlCells + `</row>`;
    }).join("");

    const sheetXml =
        '<?xml version="1.0" encoding="UTF-8"?>' +
        '<worksheet xmlns="http://schemas.openxmlformats.org/' +
        'spreadsheetml/2006/main">' +
        '<sheetViews><sheetView workbookViewId="0"/></sheetViews>' +
        '<sheetData>' + sheetRows + '</sheetData>' +
        '</worksheet>';

    const files = [
        [
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>' +
            '<Types xmlns="http://schemas.openxmlformats.org/' +
            'package/2006/content-types">' +
            '<Default Extension="rels" ContentType="' +
            'application/vnd.openxmlformats-package.relationships+xml"/>' +
            '<Default Extension="xml" ContentType="application/xml"/>' +
            '<Override PartName="/xl/workbook.xml" ContentType="' +
            'application/vnd.openxmlformats-officedocument.' +
            'spreadsheetml.sheet.main+xml"/>' +
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="' +
            'application/vnd.openxmlformats-officedocument.' +
            'spreadsheetml.worksheet+xml"/>' +
            '</Types>'
        ],
        [
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>' +
            '<Relationships xmlns="http://schemas.openxmlformats.org/' +
            'package/2006/relationships">' +
            '<Relationship Id="rId1" Type="' +
            'http://schemas.openxmlformats.org/officeDocument/' +
            '2006/relationships/officeDocument" ' +
            'Target="xl/workbook.xml"/></Relationships>'
        ],
        [
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>' +
            '<workbook xmlns="http://schemas.openxmlformats.org/' +
            'spreadsheetml/2006/main" ' +
            'xmlns:r="http://schemas.openxmlformats.org/' +
            'officeDocument/2006/relationships">' +
            '<sheets><sheet name="Breakdowns" sheetId="1" ' +
            'r:id="rId1"/></sheets></workbook>'
        ],
        [
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>' +
            '<Relationships xmlns="http://schemas.openxmlformats.org/' +
            'package/2006/relationships">' +
            '<Relationship Id="rId1" Type="' +
            'http://schemas.openxmlformats.org/officeDocument/' +
            '2006/relationships/worksheet" ' +
            'Target="worksheets/sheet1.xml"/></Relationships>'
        ],
        ["xl/worksheets/sheet1.xml", sheetXml]
    ];

    // Create a valid uncompressed ZIP container for XLSX.
    // No external libraries or network requests required.
    const crcTable = Array.from({length: 256}, (_, n) => {
        let c = n;

        for (let k = 0; k < 8; k++) {
            c = c & 1
                ? 0xedb88320 ^ (c >>> 1)
                : c >>> 1;
        }

        return c >>> 0;
    });

    const crc32 = bytes => {
        let crc = 0xffffffff;

        for (const byte of bytes) {
            crc = crcTable[(crc ^ byte) & 255] ^
                (crc >>> 8);
        }

        return (crc ^ 0xffffffff) >>> 0;
    };

    const localParts = [];
    const centralParts = [];
    let offset = 0;

    for (const [filename, content] of files) {
        const name = encode(filename);
        const data = encode(content);
        const crc = crc32(data);

        const local = new Uint8Array(30 + name.length);
        const l = new DataView(local.buffer);

        l.setUint32(0, 0x04034b50, true);
        l.setUint16(4, 20, true);
        l.setUint16(8, 0, true);
        l.setUint32(14, crc, true);
        l.setUint32(18, data.length, true);
        l.setUint32(22, data.length, true);
        l.setUint16(26, name.length, true);
        local.set(name, 30);

        const central = new Uint8Array(46 + name.length);
        const c = new DataView(central.buffer);

        c.setUint32(0, 0x02014b50, true);
        c.setUint16(4, 20, true);
        c.setUint16(6, 20, true);
        c.setUint32(16, crc, true);
        c.setUint32(20, data.length, true);
        c.setUint32(24, data.length, true);
        c.setUint16(28, name.length, true);
        c.setUint32(42, offset, true);
        central.set(name, 46);

        localParts.push(local, data);
        centralParts.push(central);

        offset += local.length + data.length;
    }

    const centralLength = centralParts.reduce(
        (sum, part) => sum + part.length, 0
    );

    const footer = new Uint8Array(22);
    const f = new DataView(footer.buffer);

    f.setUint32(0, 0x06054b50, true);
    f.setUint16(8, files.length, true);
    f.setUint16(10, files.length, true);
    f.setUint32(12, centralLength, true);
    f.setUint32(16, offset, true);

    return new Blob(
        [...localParts, ...centralParts, footer],
        {
            type: "application/vnd.openxmlformats-officedocument." +
                "spreadsheetml.sheet"
        }
    );
}

function hodPopupDownloadExcel(dialog, classification) {
    const table = dialog.$body.find("table").get(0);

    if (!table) {
        frappe.msgprint("Breakdown table not found.");
        return;
    }

    const rows = hodPopupExportRows(table);
    const workbook = hodPopupMakeXlsx(rows);

    const safeName = classification
        .replace(/[^a-zA-Z0-9_-]+/g, "_")
        .replace(/^_+|_+$/g, "");

    const filename =
        `HOD_Breakdowns_${safeName || "Report"}.xlsx`;

    const url = URL.createObjectURL(workbook);
    const link = document.createElement("a");

    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();

    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function hodPopupPrintReport(dialog, classification) {
    const table = dialog.$body.find("table").get(0);

    if (!table) {
        frappe.msgprint("Breakdown table not found.");
        return;
    }

    const printWindow = window.open("", "_blank");

    if (!printWindow) {
        frappe.msgprint(
            "Please allow popups to print this report."
        );
        return;
    }

    const title = hodEscape(
        `Breakdown Reasons - ${classification}`
    );

    const documentHtml = `
        <!doctype html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>${title}</title>
            <style>
                @page {
                    size: A4 landscape;
                    margin: 10mm;
                }
                body {
                    font-family: Arial, sans-serif;
                    color: #111;
                    margin: 0;
                    font-size: 10px;
                }
                h2 {
                    margin: 0 0 14px;
                    font-size: 17px;
                    color: #111;
                }
                table {
                    border-collapse: collapse !important;
                    width: 100% !important;
                    min-width: 0 !important;
                    font-size: 9px !important;
                }
                th, td {
                    padding: 6px !important;
                    border: 1px solid #aaa !important;
                    color: #111 !important;
                    background: #fff !important;
                    vertical-align: top;
                }
                thead th {
                    background: #ddd !important;
                    font-weight: bold !important;
                }
                tbody tr:last-child td {
                    background: #ddd !important;
                    font-weight: bold !important;
                }
                thead {
                    display: table-header-group;
                }
                tr {
                    break-inside: avoid;
                }
            </style>
        </head>
        <body>
            <h2>${title}</h2>
            ${table.outerHTML}
        </body>
        </html>
    `;

    printWindow.document.open();
    printWindow.document.write(documentHtml);
    printWindow.document.close();

    printWindow.onload = function() {
        printWindow.focus();
        printWindow.print();
    };
}

function hodOpenDowntimePopup(button) {
    const panel = button.closest(".hod-downtime-contributors");
    const index = button.getAttribute("data-hod-class-index");
    const detail = Array.from(
        panel.querySelectorAll(".hod-downtime-class-detail")
    ).find(item =>
        item.getAttribute("data-hod-class-index") === index
    );

    if (!detail) {
        return;
    }

    const classification = button.querySelector(
        ".hod-breakdown-class-text"
    )?.textContent?.trim() || "Downtime";

    const dialog = new frappe.ui.Dialog({
        title: `Breakdown Reasons - ${classification}`,
        size: "extra-large"
    });

    dialog.$body.html(detail.innerHTML);

    // HOD_POPUP_COLUMN_FILTERS_V1
    // Filter popup rows in the browser. No recalculation.

    const hodTable = dialog.$body.find("table").first();
    const hodHead = hodTable.find("thead");

    if (hodHead.length) {
        const columns = hodHead.find("tr").first()
            .children("th");

        const filterRow = $("<tr>").addClass(
            "hod-popup-column-filters"
        );

        columns.each(function(index) {
            const th = $("<th>").css({
                background: "#252c35",
                padding: "7px 5px",
                border: "1px solid #46505c"
            });

            const input = $("<input>", {
                type: "text",
                placeholder: "Filter...",
                "aria-label": "Filter " +
                    $(this).text().trim()
            }).addClass(
                "hod-popup-column-filter"
            ).attr(
                "data-column-index", index
            ).css({
                width: "100%",
                minWidth: "65px",
                boxSizing: "border-box",
                padding: "6px",
                border: "1px solid #596574",
                borderRadius: "4px",
                background: "#111820",
                color: "#ffffff",
                fontSize: "11px"
            });

            th.append(input);
            filterRow.append(th);
        });

        hodHead.append(filterRow);

        const dataRows = hodTable.find("tbody tr")
            .filter(function() {
                return $(this).children("td").length ===
                    columns.length;
            });

        const filterInfo = $("<div>")
            .addClass("hod-popup-filter-info")
            .css({
                color: "#dce6ef",
                background: "#252c35",
                padding: "8px 12px",
                fontSize: "12px",
                borderRadius: "4px",
                marginBottom: "8px"
            });

        hodTable.before(filterInfo);


        // HOD_POPUP_FILTERED_TOTALS_V1
        // Change popup totals to match the visible filtered rows.
        // No backend or A&U calculation changes.

        const totalRow = hodTable.find("tbody tr")
            .filter(function() {
                return $(this).children("td[colspan]").length > 0;
            }).last();

        const totalCells = totalRow.children("td");

        const originalTotals = totalCells
            .slice(1, 5)
            .map(function() {
                return $(this).text().trim();
            }).get();

        const parseMinutes = function(value) {
            const text = String(value || "").trim();
            const match = text.match(
                /(\d+)\s*h\s*(\d+)\s*m/i
            );

            if (match) {
                return Number(match[1]) * 60 +
                    Number(match[2]);
            }

            return 0;
        };

        const formatMinutes = function(minutes) {
            const amount = Math.max(
                0, Math.round(minutes)
            );

            const hours = Math.floor(amount / 60);
            const remainder = amount % 60;

            return hours + "h " +
                String(remainder).padStart(2, "0") + "m";
        };

        const updateFilters = function() {
            const filters = [];

            filterRow.find("input").each(function() {
                filters.push(
                    String($(this).val() || "")
                        .trim()
                        .toLowerCase()
                );
            });

            const hasFilters = filters.some(Boolean);
            const totals = [0, 0, 0, 0];

            let visible = 0;

            dataRows.each(function() {
                const row = $(this);
                const cells = row.children("td");

                const matches = filters.every(
                    (value, index) => {
                        if (!value) return true;

                        const text = String(
                            cells.eq(index).text() || ""
                        ).toLowerCase();

                        return text.includes(value);
                    }
                );

                row.toggle(matches);

                if (matches) {
                    visible++;

                    // Column indices:
                    // 3 = Total Time
                    // 4 = Start-up + Fatigue
                    // 5 = Sunday Hours
                    // 6 = A&U Time
                    for (let i = 0; i < 4; i++) {
                        totals[i] += parseMinutes(
                            cells.eq(i + 3).text()
                        );
                    }
                }
            });

            // Footer starts with the TOTAL cell spanning
            // Plant No., Start and Resolved.
            if (totalCells.length >= 5) {
                for (let i = 0; i < 4; i++) {
                    const text = hasFilters
                        ? formatMinutes(totals[i])
                        : originalTotals[i];

                    totalCells.eq(i + 1).text(text);
                }
            }

            filterInfo.text(
                "Showing " + visible + " of " +
                dataRows.length + " breakdown records. " +
                (hasFilters
                    ? "Totals reflect the filtered records."
                    : "Totals reflect the full classification.")
            );
        };

        filterRow.on(
            "input",
            ".hod-popup-column-filter",
            updateFilters
        );

        updateFilters();
    }


    // Print / PDF and Excel export actions for this classification.
    dialog.$body.prepend(`
        <div class="hod-popup-export-actions"
            >
            <button type="button"
                class="btn btn-default btn-sm hod-popup-print"
               >
                Print / PDF
            </button>
            <button type="button"
                class="btn btn-primary btn-sm hod-popup-excel"
               >
                Export Excel
            </button>
        </div>
    `);

    dialog.$body.on("click", ".hod-popup-print", function() {
        hodPopupPrintReport(dialog, classification);
    });

    dialog.$body.on("click", ".hod-popup-excel", function() {
        hodPopupDownloadExcel(dialog, classification);
    });

    dialog.show();

    // HOD_DOWNTIME_DARK_THEME_V1
    dialog.$wrapper.addClass("hod-downtime-dark-theme");

    // Close when clicking the modal backdrop.
    dialog.$wrapper.on("click.hodDowntimePopup", function(event) {
        if (event.target === this ||
            event.target.classList.contains("modal")) {
            dialog.hide();
        }
    });
}

if (typeof document !== "undefined") {
    document.addEventListener("click", function(event) {
        const button = event.target.closest(
            ".hod-downtime-bar-button"
        );
        if (button) {
            event.preventDefault();
            hodOpenDowntimePopup(button);
        }
    });
}

function hodProblemMachinesPanel(engineering) {
    const breakdown = engineering?.breakdown_summary || {};
    const machines = breakdown.top_machines || [];
    return `
        <section class="hod-new-summary hod-problem-machines">
            <div class="hod-new-summary-title hod-downtime-lost-title">
                DOWNTIME LOST HOURS
            </div>

            <div class="hod-breakdown-layout hod-breakdown-layout-block">
                <div class="hod-breakdown-table-section">
                    <h4>Bottom 10 Availability</h4>

                    <table class="hod-new-problem-table">
                        <thead>
                            <tr>
                                <th>#</th>
                                <th>Fleet</th>
                                <th>Category</th>
                                <th>Breakdown cause</th>
                                <th class="hod-hours-column">Hours</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${machines.length ? machines.map((item, index) => `
                                <tr>
                                    <td>${index + 1}</td>
                                    <td style="${item.is_spare ? 'background:#e9d5ff !important;' : ''}">${hodEscape(item.fleet)}</td>
                                    <td style="${item.is_spare ? 'background:#e9d5ff !important;' : ''}">${hodEscape(item.category)}</td>
                                    <td title="${hodEscape(item.reason)}">
                                        ${hodEscape(item.classification)}
                                        <small class="hod-breakdown-reason hod-reason-detail">
                                            ${hodEscape(item.reason)}
                                        </small>
                                    </td>
                                    <td style="text-align:left !important;">${Number(item.hours).toFixed(1)}</td>
                                </tr>
                            `).join('') : `
                                <tr>
                                    <td colspan="5" class="hod-new-empty">
                                        No breakdown records for this period
                                    </td>
                                </tr>
                            `}
                        </tbody>
                    </table>

                </div>


            </div>
        </section>
    `;
}


function hodDowntimeContributorsPanel(engineering) {
    const classifications =
        engineering?.downtime_contributors?.classifications || [];

    // HOD_CLASSIFICATION_HEADING_PERCENT_V1
    const hodClassTotalHours = classifications.reduce(
        (sum, item) => sum + (Number(item.hours) || 0),
        0
    );


    const fmt = minutes => {
        const value = Math.max(0, Math.round(Number(minutes) || 0));
        return `${Math.floor(value / 60)}h ${String(value % 60).padStart(2, "0")}m`;
    };

    const cell = "padding:8px 10px;border-bottom:1px solid #dbeafe;vertical-align:top";
    const head = "padding:9px 10px;background:#eff6ff;color:#1d4ed8;text-align:left";

    return `
        <section class="hod-new-summary hod-downtime-contributors">
            <div class="hod-new-summary-title hod-contributors-title">
                DOWNTIME CONTRIBUTORS BAR GRAPH
            </div>
            <div class="hod-breakdown-chart-section hod-contributors-chart">

                ${hodBreakdownBarChart(classifications)}

                ${classifications.map((item, index) => `
                    <div class="hod-downtime-class-detail"
                        data-hod-class-index="${index}" hidden>

                        <div style="border:1px solid #2563eb;
                                    border-radius:10px;overflow:hidden">

                            <div style="background:#dbeafe;color:#1d4ed8;
                                        padding:10px 12px;font-weight:900">
                                Breakdown Reasons for
                                ${hodEscape(item.classification)}
                                (${hodClassTotalHours > 0
                                    ? (
                                        (Number(item.hours) || 0)
                                        / hodClassTotalHours * 100
                                      ).toFixed(1)
                                    : "0.0"}%)

                                <!-- HOD_CLASSIFICATION_CALCULATION_V1 -->
                                <div style="font-size:11px;
                                            margin-top:5px;
                                            font-weight:600;
                                            color:#164db8">
                                    Calculation:
                                    ${Number(item.hours || 0).toFixed(2)} hrs
                                    &divide;
                                    ${hodClassTotalHours.toFixed(2)} hrs
                                    &times; 100 =
                                    ${hodClassTotalHours > 0
                                        ? (
                                            (Number(item.hours) || 0)
                                            / hodClassTotalHours * 100
                                          ).toFixed(1)
                                        : "0.0"}%
                                </div>
                            </div>

                            <div style="overflow:auto;max-height:65vh">
                                <table style="width:100%;min-width:1150px;
                                              border-collapse:collapse;font-size:12px">
                                    <thead>
                                        <tr>
                                            ${[
                                                "Plant No.", "Start", "Resolved",
                                                "Total Time", "Start-up + Fatigue",
                                                "Sunday Hours", "A&U Time", "Reason"
                                            ].map(label =>
                                                `<th style="${head}">${label}</th>`
                                            ).join("")}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        ${(item.details || []).map(detail => `
                                            <tr>
                                                <td style="${cell};font-weight:700">
                                                    ${hodEscape(detail.plant_no || "")}
                                                </td>
                                                <td style="${cell};white-space:nowrap">
                                                    ${hodEscape(detail.start_datetime || "")}
                                                </td>
                                                <td style="${cell};white-space:nowrap">
                                                    ${hodEscape(detail.resolved_datetime || "")}
                                                </td>
                                                <td style="${cell}">
                                                    ${fmt(detail.total_minutes)}
                                                </td>
                                                <td style="${cell};color:#b45309">
                                                    ${fmt(detail.startup_fatigue_minutes)}
                                                </td>
                                                <td style="${cell};color:#7c3aed">
                                                    ${fmt(detail.sunday_minutes)}
                                                </td>
                                                <td style="${cell};color:#166534;font-weight:700">
                                                    ${fmt(detail.au_minutes)}
                                                </td>
                                                <td style="${cell};min-width:210px">
                                                    ${hodEscape(detail.reason || "")}
                                                </td>
                                            </tr>
                                        `).join("")}
                                        <tr style="background:#dbeafe;font-weight:900">
                                            <td colspan="3"
                                                style="${cell};text-align:right">
                                                TOTAL
                                            </td>
                                            <td style="${cell}">
                                                ${fmt(item.total_minutes)}
                                            </td>
                                            <td style="${cell};color:#b45309">
                                                ${fmt(item.startup_fatigue_minutes)}
                                            </td>
                                            <td style="${cell};color:#7c3aed">
                                                ${fmt(item.sunday_minutes)}
                                            </td>
                                            <td style="${cell};color:#166534">
                                                ${fmt(item.au_minutes)}
                                            </td>
                                            <td style="${cell}"></td>
                                        </tr>
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                `).join("")}
            </div>
        </section>
    `;
}

function hodReliabilityPanel(engineering) {
    const categories = [
        ['Excavators', 'Excavator'],
        ['Dozers', 'Dozer'],
        ['ADTs', 'ADT']
    ];

    const metrics = [
        ['MTBF', 'hrs'],
        ['MTTR', 'hrs'],
        ['Utilisation', '%'],
        ['Downtime', '%']
    ];

    return `
        <section class="hod-new-summary hod-reliability-panel">
            <div class="hod-new-summary-title"
                style="background:#006b3f;">
                MAINTENANCE & RELIABILITY (Production and Swing Machine)
            </div>
            <h4 class="hod-reliability-subheading"
                style="margin:8px 8px 5px;font-size:12px;font-weight:800;color:#082e57">
                DOWNTIME
            </h4>
            <div class="hod-new-reliability-grid">
                ${categories.map(([label, category]) => `
                    <div class="hod-new-asset-card">
                        <div class="hod-new-asset-name">
                            <strong>${label}</strong>
                        </div>
                        <div class="hod-new-metrics">
                            ${metrics.map(([name, unit]) => `
                                <div class="hod-new-metric">
                                    <span>${name}</span>
                                    <small>(${unit})</small>
                                    <strong>${
                                        name === 'Utilisation'
                                            ? hodPercent(
                                                hodValue(
                                                    engineering?.reliability
                                                        ?.[category]?.adjusted_utilisation
                                                )
                                            )
                                            : (name === 'MTBF' || name === 'MTTR')
                                                ? (
                                                    hodValue(
                                                        engineering?.reliability
                                                            ?.[category]?.[name.toLowerCase()]
                                                    ) === null
                                                        ? '—'
                                                        : hodNumber(
                                                            engineering.reliability[category][name.toLowerCase()],
                                                            1
                                                        )
                                                )
                                                : name === 'Downtime'
                                                    ? (
                                                        hodValue(
                                                            engineering?.reliability
                                                                ?.[category]?.downtime
                                                        ) === null
                                                            ? '—'
                                                            : hodPercent(
                                                                engineering.reliability[category].downtime
                                                            )
                                                    )
                                                    : '—'
                                    }</strong>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                `).join('')}
            </div>
            <div class="hod-new-footnote">
                MTBF: operating hours per failure. MTTR: average completed breakdown restoration hours. Downtime: mechanical breakdown hours / required hours.
            </div>

            <h4 class="hod-reliability-subheading"
                style="margin:12px 8px 5px;font-size:12px;font-weight:800;color:#082e57">
                PLANNED MAINTENANCE
            </h4>

            <div class="hod-new-reliability-grid">
                ${categories.map(([label, category]) => `
                    <div class="hod-new-asset-card">
                        <div class="hod-new-asset-name">
                            <strong>${label}</strong>
                        </div>
                        <div class="hod-new-metrics">
                            ${metrics.map(([name, unit]) => `
                                <div class="hod-new-metric">
                                    <span title="${
                                        name === 'Utilisation'
                                            ? 'Adjusted Utilisation = Work Hours / (Required Hours - Planned Maintenance Hours) x 100'
                                            : ''
                                    }">${
                                        name === 'Downtime'
                                            ? 'Planned %'
                                            : name === 'MTBF'
                                                ? 'Hrs/Service'
                                                : name === 'MTTR'
                                                    ? 'Avg Service'
                                            : name === 'Utilisation'
                                                ? 'Planned Hrs'
                                                : name
                                    }</span>
                                    <small>(${name === 'Utilisation' ? 'hrs' : unit})</small>
                                    <strong>${
                                        name === 'MTBF'
                                            ? (
                                                hodValue(
                                                    engineering?.reliability
                                                        ?.[category]?.planned?.mtbf
                                                ) === null
                                                    ? '—'
                                                    : hodNumber(
                                                        engineering.reliability[category].planned.mtbf,
                                                        1
                                                    )
                                            )
                                            : name === 'MTTR'
                                                ? hodNumber(
                                                    engineering?.reliability
                                                        ?.[category]?.planned?.mttr,
                                                    1
                                                )
                                                : name === 'Utilisation'
                                                    ? hodNumber(
                                                        engineering?.reliability
                                                            ?.[category]?.planned?.hours,
                                                        1
                                                    )
                                                    : (
                                                        hodValue(
                                                            engineering?.reliability
                                                                ?.[category]?.planned?.downtime
                                                        ) === null
                                                            ? '—'
                                                            : hodNumber(
                                                                engineering.reliability[category].planned.downtime,
                                                                2
                                                            ) + '%'
                                                    )
                                    }</strong>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                `).join('')}
            </div>

            <div class="hod-new-footnote">
                Planned Hrs = A&U Engine planned maintenance hours. Avg Service = average completed service duration.
            </div>

        </section>
    `;
}

// HOD_HR_STATS_V2
// Blue workforce overview - dynamic Employee data.
// This styling is limited to the HOD presentation.
function hodHrStats(row) {
    const stats = row.hr_stats || {};

    const icons = {
        employees: `<svg viewBox="0 0 24 24" width="24" height="24"
            fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round"
            aria-hidden="true">
            <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>
            <circle cx="9" cy="7" r="4"/>
            <path d="M22 21v-2a4 4 0 0 0-3-3.87"/>
            <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
        </svg>`,

        terminations: `<svg viewBox="0 0 24 24" width="24" height="24"
            fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round"
            aria-hidden="true">
            <path d="M10 17l5-5-5-5"/>
            <path d="M15 12H3"/>
            <path d="M12 3h7a2 2 0 0 1 2 2v14
                a2 2 0 0 1-2 2h-7"/>
        </svg>`,

        recruitment: `<svg viewBox="0 0 24 24" width="24" height="24"
            fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round"
            aria-hidden="true">
            <circle cx="11" cy="11" r="8"/>
            <circle cx="11" cy="9" r="2"/>
            <path d="M6.5 16a5 5 0 0 1 9 0"/>
            <path d="m17 17 5 5"/>
        </svg>`
    };

    const groups = [
        {
            label: "TOTAL EMPLOYEES",
            icon: icons.employees,
            values: stats.total_employees
        },
        {
            label: "TERMINATIONS",
            icon: icons.terminations,
            values: stats.terminations
        },
        {
            label: "RECRUITMENT",
            icon: icons.recruitment,
            values: stats.recruitment
        }
    ];

    const safeCount = value => {
        const number = Number(value);
        return Number.isFinite(number) && number >= 0
            ? Math.floor(number)
            : 0;
    };

    return `
        <div class="hod-hr-stats"
           >

            <div class="hod-hr-header">
                <div class="hod-hr-title">HR STATS</div>

                <div class="hod-hr-subtitle">WORKFORCE OVERVIEW</div>
            </div>

            ${groups.map(group => `
                <div class="hod-hr-card">
                    <div class="hod-hr-card-heading">
                        <span class="hod-hr-icon">${group.icon}</span>

                        <span class="hod-hr-card-label">${group.label}</span>
                    </div>

                    <div class="hod-hr-card-values">
                        <span>Male = <strong class="hod-hr-count">${safeCount(group.values?.male)}</strong></span>
                        <span class="hod-hr-separator">|</span>
                        <span>Female = <strong class="hod-hr-count">${safeCount(group.values?.female)}</strong></span>
                    </div>
                </div>
            `).join("")}
        </div>
    `;
}

function hodDashboard(row) {
    const engineering = row.engineering;
    return `<article class="hod-performance-dashboard" data-site="${hodEscape(row.site)}">
        <header class="hod-brand"><img src="/files/ISAMBANE-WHITE.png" alt="Isambane Mining"><h2>${hodEscape(hodGetComplexName(row.site))} · Production Performance</h2></header>
        <div class="hod-period"><strong>${hodEscape(row.site)}</strong><span>${hodEscape(row.period)}</span></div>
        <div class="hod-columns">
            <section class="hod-domain-panel hod-engineering-panel">

                <!-- HOD_SUMMARY_LARGE_NUMBERS_V1 -->
<!-- HOD_MACHINE_CARDS_V1 -->
                <div class="hod-domain-heading hod-machine-summary"
                   >

                    ${[
                        {
                            label:"TOTAL MACHINES",
                            counts:engineering.fleet_counts,
                            icon:"total"
                        },
                        {
                            label:"PRODUCTION MACHINES",
                            counts:engineering.fleet_scope_counts?.production,
                            icon:"production"
                        },
                        {
                            label:"SWING MACHINES",
                            counts:engineering.fleet_scope_counts?.swing,
                            icon:"swing"
                        }
                    ].map(group => `
                        <div class="hod-machine-card">
                            <div class="hod-machine-card-heading">
                                <svg viewBox="0 0 24 24"
                                    width="19" height="19"
                                    fill="none"
                                    stroke="currentColor"
                                    stroke-width="2"
                                    stroke-linecap="round"
                                    stroke-linejoin="round"
                                    class="hod-machine-icon"
                                    aria-hidden="true">
                                    ${group.icon === "total"
                                        ? `<rect x="2" y="10" width="20" height="8" rx="2"/>
                                           <circle cx="7" cy="20" r="2"/>
                                           <circle cx="17" cy="20" r="2"/>
                                           <path d="M5 10V6h6l4 4M15 10V5h4"/>`
                                        : group.icon === "production"
                                        ? `<path d="M3 21V8l6 4V8l6 4V4h6v17Z"/>
                                           <path d="M7 17h2m4 0h2m3 0h1"/>`
                                        : `<path d="M4 7h16l-4-4m4 4-4 4"/>
                                           <path d="M20 17H4l4-4m-4 4 4 4"/>`
                                    }
                                </svg>

                                <span class="hod-machine-card-label">
                                    ${group.label}
                                </span>
                            </div>

                            <div class="hod-machine-card-values">
                                <span>Excavator = <strong class="hod-machine-count">${group.counts?.Excavator ?? 0}</strong></span>
                                <span>Dozer = <strong class="hod-machine-count">${group.counts?.Dozer ?? 0}</strong></span>
                                <span>ADT = <strong class="hod-machine-count">${group.counts?.ADT ?? 0}</strong></span>
                            </div>
                        </div>
                    `).join('')}
                </div>

                <div class="hod-domain-heading"><div><h3>Engineering</h3><span>Availability &amp; utilisation</span></div><div class="hod-header-legend"><span>Availability ${hodMetricLegend('avail')}</span><span>Utilisation ${hodMetricLegend('util')}</span></div></div>
                <div class="hod-domain-main hod-engineering-main"><h4 style="display:flex;align-items:center;justify-content:space-between">
                        <span>Fleet averages · all filtered machines</span>
                        <button type="button" class="hod-view-au-detail" data-site="${hodEscape(row.site)}" data-from="${hodEscape(row.start_date)}" data-to="${hodEscape(row.end_date)}">View Detail</button>
                    </h4>
                    ${['avail','util'].map(metric=>`<div class="hod-gauge-row"><strong class="hod-gauge-label">${metric==='avail'?'Availability':'Utilisation'}</strong>${['Excavator','Dozer','ADT'].map(category=>hodGauge(category,metric,engineering.category_averages[category])).join('')}</div>`).join('')}
                    </div>
${hodProblemMachinesPanel(engineering)}
            </section>
            <section class="hod-domain-panel hod-production-panel">
                ${hodHrStats(row)}
                <div class="hod-domain-heading hod-production-title"><div><h3>Production</h3><span>Production performance</span></div></div>
                <div class="hod-domain-main" style="height:auto;min-height:360px;overflow:visible;box-sizing:border-box;padding-bottom:10px">${hodProduction(row)}</div>
                ${hodDowntimeContributorsPanel(engineering)}
            </section>
        </div>

    </article>`;
}
// HOD_HR_SUMMARY_ALIGNMENT_V2
// Match the summary box heights without stretching their contents.
function alignHodDomainHeadings(root) {
    root.find(".hod-performance-dashboard").each(function () {
        const board = this;

        const engineeringPanel = board.querySelector(
            ".hod-engineering-panel"
        );
        const productionPanel = board.querySelector(
            ".hod-production-panel"
        );

        if (!engineeringPanel || !productionPanel) return;

        const machineSummary = engineeringPanel.querySelector(
            ":scope > .hod-domain-heading:first-child"
        );
        const hrSummary = productionPanel.querySelector(
            ":scope > .hod-hr-stats"
        );
        const engineeringHeading = engineeringPanel.querySelector(
            ":scope > .hod-domain-heading:nth-child(2)"
        );
        const productionHeading = productionPanel.querySelector(
            ":scope > .hod-production-title"
        );

        if (!machineSummary || !hrSummary ||
            !engineeringHeading || !productionHeading) return;

        // Remove previous adjustments before measuring natural sizes.
        machineSummary.style.height = "auto";
        hrSummary.style.height = "auto";
        engineeringPanel.style.paddingTop = "0px";
        productionPanel.style.paddingTop = "0px";

        machineSummary.style.boxSizing = "border-box";
        hrSummary.style.boxSizing = "border-box";

        // Match heights without changing the card contents.
        const height = Math.ceil(Math.max(
            machineSummary.getBoundingClientRect().height,
            hrSummary.getBoundingClientRect().height
        ));

        machineSummary.style.height = height + "px";
        hrSummary.style.height = height + "px";

        // Match vertical spacing below both summary boxes.
        const machineMargin = parseFloat(
            getComputedStyle(machineSummary).marginBottom
        ) || 0;

        const hrMargin = parseFloat(
            getComputedStyle(hrSummary).marginBottom
        ) || 0;

        hrSummary.style.marginBottom = machineMargin + "px";

        // Adjust only if other layout differences remain.
        const difference =
            engineeringHeading.getBoundingClientRect().top -
            productionHeading.getBoundingClientRect().top;

        if (difference > 0.5) {
            productionPanel.style.paddingTop = difference + "px";
        } else if (difference < -0.5) {
            engineeringPanel.style.paddingTop = (-difference) + "px";
        }
    });
}


// HOD_DOWNTIME_SECTION_ALIGNMENT_V1
// Align Engineering and Production downtime section headings.
// HOD_MAIN_PANEL_HEIGHT_ALIGNMENT_V1
// Keep Engineering gauges and Production KPIs equal in height.
// HOD_GAUGE_FILL_SPACE_V1
// Fill the Engineering panel without changing gauge calculations.
function fillHodGaugeSpace(root) {
    root.find(".hod-performance-dashboard").each(function () {
        const board = this;

        const engineering = board.querySelector(
            ".hod-engineering-panel > .hod-engineering-main"
        );

        const production = board.querySelector(
            ".hod-production-panel > .hod-domain-main"
        );

        if (!engineering || !production) return;

        const rows = Array.from(
            engineering.querySelectorAll(
                ":scope > .hod-gauge-row"
            )
        );

        if (rows.length !== 2) return;

        // Main panel heights have already been aligned.
        const height = Math.ceil(Math.max(
            engineering.getBoundingClientRect().height,
            production.getBoundingClientRect().height
        ));

        engineering.style.boxSizing = "border-box";
        engineering.style.height = height + "px";
        engineering.style.minHeight = height + "px";
        engineering.style.display = "flex";
        engineering.style.flexDirection = "column";

        // Override flex: 0 0 auto from the existing CSS.
        rows.forEach(row => {
            row.style.flex = "1 1 0";
            row.style.minHeight = "0";
            row.style.alignItems = "stretch";

            row.querySelectorAll(".hod-gauge").forEach(card => {
                card.style.display = "flex";
                card.style.flexDirection = "column";
                card.style.justifyContent = "center";
                card.style.minHeight = "0";
            });
        });
    });
}

function alignHodMainPanels(root) {
    root.find(".hod-performance-dashboard").each(function () {
        const board = this;

        const engineering = board.querySelector(
            ".hod-engineering-panel > .hod-engineering-main"
        );

        const production = board.querySelector(
            ".hod-production-panel > .hod-domain-main"
        );

        if (!engineering || !production) return;

        // Preserve the original minimum heights for repeat calls.
        for (const panel of [engineering, production]) {
            if (!panel.hasAttribute("data-hod-base-min-height")) {
                panel.setAttribute(
                    "data-hod-base-min-height",
                    panel.style.minHeight || ""
                );
            }

            panel.style.minHeight = panel.getAttribute(
                "data-hod-base-min-height"
            );
        }

        // Use the larger rendered height; never shrink content.
        const targetHeight = Math.ceil(Math.max(
            engineering.getBoundingClientRect().height,
            production.getBoundingClientRect().height
        ));

        for (const panel of [engineering, production]) {
            panel.style.boxSizing = "border-box";
            panel.style.minHeight = targetHeight + "px";
        }
    });
}

// HOD_DOWNTIME_EQUAL_HEIGHT_V1
// Match the outer heights of the two downtime panels.
// Preserve table rows, graph bars and existing heading alignment.
function alignHodDowntimePanelHeights(root) {
    root.find(".hod-performance-dashboard").each(function () {
        const board = this;

        const engineering = board.querySelector(
            ".hod-engineering-panel .hod-problem-machines"
        );

        const production = board.querySelector(
            ".hod-production-panel .hod-downtime-contributors"
        );

        if (!engineering || !production) return;

        const panels = [engineering, production];

        // Reset previous height adjustments before measuring.
        panels.forEach(panel => {
            if (!panel.hasAttribute("data-hod-downtime-base-min")) {
                panel.setAttribute(
                    "data-hod-downtime-base-min",
                    panel.style.minHeight || ""
                );
            }

            panel.style.minHeight = panel.getAttribute(
                "data-hod-downtime-base-min"
            );

            panel.style.boxSizing = "border-box";
        });

        const bounds = panels.map(panel =>
            panel.getBoundingClientRect()
        );

        // Both bottom borders should finish on the same line.
        const targetBottom = Math.ceil(Math.max(
            bounds[0].bottom,
            bounds[1].bottom
        ));

        panels.forEach((panel, index) => {
            const requiredHeight = Math.ceil(
                targetBottom - bounds[index].top
            );

            panel.style.minHeight =
                Math.max(
                    bounds[index].height,
                    requiredHeight
                ) + "px";
        });
    });
}

function alignHodDowntimeSections(root) {
    const dashboards = root.find(".hod-performance-dashboard");

    dashboards.each(function () {
        const board = this;

        const engineering = board.querySelector(
            ".hod-engineering-panel .hod-problem-machines"
        );

        const production = board.querySelector(
            ".hod-production-panel .hod-downtime-contributors"
        );

        if (!engineering || !production) {
            return;
        }

        // Reset previous offsets before measuring.
        engineering.style.marginTop = "0px";
        production.style.marginTop = "0px";

        const engineeringTop =
            engineering.getBoundingClientRect().top;

        const productionTop =
            production.getBoundingClientRect().top;

        const difference = productionTop - engineeringTop;

        if (difference > 0.5) {
            engineering.style.marginTop = difference + "px";
        } else if (difference < -0.5) {
            production.style.marginTop =
                Math.abs(difference) + "px";
        }
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


}

// HOD_DOWNTIME_FILL_SPACE_V1
// Distribute two-column graph bars over the available panel height.
function fillHodDowntimeGraphSpace(root) {
    root.find(".hod-performance-dashboard").each(function () {
        const board = this;

        const leftPanel = board.querySelector(
            ".hod-engineering-panel .hod-problem-machines"
        );

        const rightPanel = board.querySelector(
            ".hod-production-panel .hod-downtime-contributors"
        );

        const bars = rightPanel?.querySelector(
            ".hod-breakdown-bars"
        );

        if (!leftPanel || !rightPanel || !bars) return;

        const rows = Array.from(bars.children);

        if (!rows.length || !rows.every(row =>
            row.classList.contains("hod-breakdown-bar-row")
        )) return;

        const rowsPerColumn = Math.ceil(rows.length / 2);

        // Use the aligned left-panel bottom as the target.
        const bottom = leftPanel.getBoundingClientRect().bottom;
        const top = bars.getBoundingClientRect().top;

        const paddingBottom = parseFloat(
            getComputedStyle(rightPanel).paddingBottom
        ) || 0;

        const availableHeight = Math.max(
            bars.getBoundingClientRect().height,
            bottom - top - paddingBottom - 2
        );

        // Keep both existing columns and classification order.
        bars.style.display = "grid";
        bars.style.gridTemplateColumns =
            "repeat(2, minmax(0, 1fr))";
        bars.style.gridTemplateRows =
            `repeat(${rowsPerColumn}, minmax(0, 1fr))`;
        bars.style.gridAutoFlow = "column";
        bars.style.columnGap = "12px";
        bars.style.rowGap = "4px";
        bars.style.alignItems = "stretch";
        bars.style.boxSizing = "border-box";
        bars.style.height = Math.ceil(availableHeight) + "px";

        // Use the available row space instead of leaving
        // one large blank block below the graph.
        rows.forEach(row => {
            row.style.height = "100%";
            row.style.minHeight = "0";
            row.style.boxSizing = "border-box";
            row.style.alignItems = "center";
        });
    });
}

async function renderHodPresentationLayout(view) {
    view.page.main.html(view.data.map(hodDashboard).join(''));
    bindHodDetailButtons(view);
    alignHodDomainHeadings(view.page.main);
    alignHodMainPanels(view.page.main);
    fillHodGaugeSpace(view.page.main);
    alignHodDowntimeSections(view.page.main);
    alignHodDowntimePanelHeights(view.page.main);
    fillHodDowntimeGraphSpace(view.page.main);
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

            // HOD_EXPORT_REMOVE_HIDDEN_DETAILS_V1
            // These large drill-down tables are not visible
            // on the dashboard and are not needed in the PPTX.
            // Modify only the export clone, never the live page.
            clone.querySelectorAll(
                '.hod-downtime-class-detail'
            ).forEach(element => element.remove());

            // Remove hidden HTML that is not part of the
            // visible dashboard presentation.
            clone.querySelectorAll('[hidden]').forEach(
                element => element.remove()
            );


            // Export-only cropped logo; preserve live dashboard logo.
            const exportHeader = clone.querySelector('.hod-brand');
            const exportLogo = clone.querySelector('.hod-brand img');

            if (exportHeader && exportLogo) {
                exportHeader.style.height = '80px';
                exportHeader.style.minHeight = '80px';
                exportHeader.style.padding = '5px 12px';
                exportHeader.style.alignItems = 'center';
                exportHeader.style.overflow = 'hidden';

                exportLogo.src =
                    '/files/ISAMBANE-WHITE.png';
                exportLogo.style.width = '216px';
                exportLogo.style.height = '68px';
                exportLogo.style.maxWidth = 'none';
                exportLogo.style.objectFit = 'contain';
                exportLogo.style.objectPosition = 'left center';
                exportLogo.style.flexShrink = '0';
                exportLogo.style.transform = 'none';
                exportLogo.style.margin = '0';
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

                // Static PowerPoint: remove live ERPNext action buttons
                // from the captured image only.
                clone.querySelectorAll(
                    '.hod-view-au-detail, .hod-view-plant-detail, .hod-view-breakdown-detail'
                ).forEach(button => button.remove());
                const siteRow = (view.data || []).find(
                    row => row.site === board.dataset.site
                );

                slides.push({title:`${board.dataset.site} · Production Performance`, heading_style:'dashboard',
                    site:board.dataset.site,
                    start_date:siteRow?.start_date || '',
                    end_date:siteRow?.end_date || '',

                    image_data:await captureHodSection(clone,{width:1600,pixelRatio:1.0,quality:0.85})});
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




