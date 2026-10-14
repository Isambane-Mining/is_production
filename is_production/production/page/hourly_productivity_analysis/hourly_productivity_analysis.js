/* Compact operational-day matrix; no writes to planning or production. */
(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const number = (value, digits = 0) => value == null ? '—' :
    Number(value).toLocaleString(undefined, {maximumFractionDigits: digits});
  const categories = [
    ['green', '#d8efdf', 'Target achieved · ≥100%'],
    ['amber', '#fff0c7', '85–99.9% achieved'],
    ['red', '#fbe1e3', 'Below 85%'],
    ['bright_red', '#ffb9c0', 'Zero production while machines were available'],
    ['grey', '#f7f8fa', 'Not planned to work']
  ];

  function displayPercentages(pie) {
    // Largest-remainder rounding keeps one-decimal labels at 100%; source ratios stay exact.
    const shares = categories.map(([key]) => {
      const exact = pie.total_hours ? pie.categories[key].hours / pie.total_hours * 1000 : 0;
      return {key, units: Math.floor(exact), remainder: exact - Math.floor(exact)};
    });
    const remaining = pie.total_hours ? 1000 - shares.reduce((sum, share) => sum + share.units, 0) : 0;
    [...shares].sort((a, b) => b.remainder - a.remainder).slice(0, remaining).forEach(share => share.units++);
    return Object.fromEntries(shares.map(share => [share.key, share.units / 10]));
  }

  function renderAnalysis(data) {
    const percentages = displayPercentages(data.pie);
    let lastMonth = '';
    let gradientStart = 0;
    const gradient = categories.map(([key, colour]) => {
      const start = gradientStart;
      gradientStart += Number(data.pie.categories[key].percent);
      return `${colour} ${start}% ${gradientStart}%`;
    }).join(',');
    const aria = categories.map(([key, , label]) => `${label}: ${number(data.pie.categories[key].hours)} hours, ${number(percentages[key], 1)}%`).join('; ');
    const plans = [...new Map(data.rows.map(row => [row.plan_name, row])).values()];
    const excluded = Object.entries(data.excluded || {}).filter(([, count]) => count).map(([key, count]) =>
      `${number(count)} ${({missing: 'missing', pending: 'unfinished', schedule_unknown: 'schedule unclassified', no_target: 'no target'})[key] || key}`).join(' · ');
    return `<section class="hpa-targets" aria-label="Production Targets"><div><h3>Production Targets <small>Per production excavator</small></h3>
      <table><thead><tr><th>Hour type</th><th>Productive minutes</th><th>BCM target</th></tr></thead>
      <tbody><tr><td>First shift hour</td><td>15 min</td><td>55 BCM</td></tr>
      <tr><td>Normal hour</td><td>60 min</td><td>220 BCM</td></tr>
      <tr><td>Last shift hour</td><td>45 min</td><td>165 BCM</td></tr></tbody></table></div>
      <div class="hpa-plan-targets">${plans.map(row => `<article><strong>Normal hourly target: ${number(row.production_excavator_count == null ? null : row.production_excavator_count * 220)} BCM</strong>
        <small>${escape(row.plan_name)} · ${number(row.production_excavator_count)} production excavators</small></article>`).join('')}</div></section>
      <div class="hpa-scroll" tabindex="0" role="region" aria-label="Hourly BCM table; scroll horizontally">
      <table class="hpa-table"><caption>Hourly recorded excavator BCM · ${escape(data.site)}</caption>
        <thead><tr><th rowspan="2" class="hpa-sticky-date">Date</th><th rowspan="2" class="hpa-sticky-day">Day</th>
          <th rowspan="2" class="hpa-sticky-month">Month</th><th colspan="12" class="hpa-shift-day"><span>DAY SHIFT · 06:00–18:00</span></th>
          <th colspan="12" class="hpa-shift-night"><span>NIGHT SHIFT · 18:00–06:00</span></th></tr>
          <tr>${data.hours.map((hour, i) => `<th class="hpa-hour ${i === 12 ? 'hpa-night-start' : ''}" title="${escape(hour)}">
            ${escape(hour.replaceAll(':00', ''))}</th>`).join('')}</tr></thead>
        <tbody>${data.rows.map(row => {
          const divider = row.month_key !== lastMonth ? `<tr class="hpa-month-divider"><th colspan="27"><span>${escape(row.month)}</span></th></tr>` : '';
          lastMonth = row.month_key;
          return `${divider}<tr><th scope="row" class="hpa-sticky-date" title="MPP: ${escape(row.plan_name)}">${escape(row.date)}</th>
            <td class="hpa-sticky-day">${escape(row.day)}</td><td class="hpa-sticky-month" title="${escape(row.month)}">${escape(row.month.replace(/^(\w{3})\w+/, '$1'))}</td>
            ${row.cells.map((cell, index) => {
              const missing = !cell.has_record;
              const record = missing ? 'No record; displayed zero is unverified' : 'Recorded production';
              const loads = (row.load_times?.[index < 12 ? 'Day' : 'Night'] || []).map(exc =>
                `${exc.excavator} First ${exc.first_load_time || '—'}, Last ${exc.last_load_time || '—'}`).join('; ');
              const title = `${cell.start || row.date + ' ' + cell.label}: ${number(cell.actual_bcm, 1)} BCM / ${number(cell.target_bcm)} target; ` +
                `${number(cell.excavator_available, 2)} production excavators available; ${record}; ${cell.record_status}` +
                (loads ? `; Shift captured loads: ${loads}` : '');
              const category = categories.some(([key]) => key === cell.category) ? cell.category : 'unclassified';
              const schedule = cell.schedule === 'off' ? 'Not planned to work' : cell.schedule === 'unknown' ? 'Schedule unclassified' : 'Planned working hour';
              const tooltip = `${title}; ${schedule}`;
              return `<td class="hpa-cell hpa-${category} ${index === 12 ? 'hpa-night-start' : ''}" title="${escape(tooltip)}"
                aria-label="${escape(tooltip)}">${number(cell.actual_bcm, 1)}${missing && cell.completed && cell.schedule === 'working' ? '<sup aria-label="Missing record">*</sup>' : ''}
                ${!cell.completed ? '<sup aria-label="Unfinished hour">†</sup>' : cell.schedule === 'unknown' ? '<sup aria-label="Schedule unclassified">?</sup>' : ''}</td>`;
            }).join('')}</tr>`;
        }).join('')}</tbody></table></div>
      <section class="hpa-chart" aria-label="Proportion of hourly performance categories">
        ${data.pie.total_hours ? `<div class="hpa-pie" role="img" aria-label="${escape(aria)}" style="background:conic-gradient(${gradient})"></div>` :
          '<div class="hpa-no-pie">No eligible completed hours</div>'}
        <div class="hpa-legend"><strong>Hourly performance · ${number(data.pie.total_hours)} included hours</strong>
          ${categories.map(([key, colour, label]) => `<div><i style="background:${colour}"></i><span>${escape(label)}</span>
            <b>${number(percentages[key], 1)}%</b><small>${number(data.pie.categories[key].hours)} hours</small></div>`).join('')}
          ${excluded ? `<small class="hpa-excluded">Excluded: ${escape(excluded)}</small>` : ''}
        </div></section>`;
  }

  const styles = `<style>
    .hpa-shell{--date-width:94px;--day-width:42px;--month-width:100px;max-width:100%;padding:12px 0;color:var(--text-color,#243447)}
    .hpa-filters{display:grid;grid-template-columns:2fr 1fr 1fr auto auto;gap:12px;align-items:end;margin-bottom:12px}
    .hpa-filters .form-group{margin-bottom:0}.hpa-filters button{margin-bottom:4px}.hpa-note{font-size:12px;line-height:1.5;margin:10px 0;color:var(--text-muted,#667582)}
    .hpa-targets{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px;background:#fff;border:1px solid #dde4eb;border-radius:8px;padding:12px;margin:12px 0;font-size:12px;color:#243447}
    .hpa-targets h3{font-size:14px;margin:0 0 8px}.hpa-targets h3 small{font-size:11px;font-weight:400;color:#657484}.hpa-targets table{width:100%;border-collapse:collapse}
    .hpa-targets th,.hpa-targets td{padding:5px 3px;border-bottom:1px solid #edf0f4;text-align:right}.hpa-targets th:first-child,.hpa-targets td:first-child{text-align:left}
    .hpa-plan-targets{display:flex;flex-direction:column;justify-content:center;gap:10px}.hpa-plan-targets small{display:block;font-size:11px;color:#657484;margin-top:4px;overflow-wrap:anywhere}
    .hpa-excluded{font-size:11px;color:#657484}.hpa-unclassified{background:#eef2f7;color:#516074}.hpa-no-pie{font-size:12px;color:#657484;padding:12px}
    .hpa-scroll{overflow:auto;max-height:65vh;border:1px solid #b7c2cc;border-radius:6px;position:relative}
    .hpa-table{border-collapse:separate;border-spacing:0;table-layout:fixed;min-width:1772px;width:100%;font-size:11px;text-align:center}
    .hpa-table caption{caption-side:top;text-align:left;padding:8px;color:inherit;font-weight:600}
    .hpa-table th,.hpa-table td{box-sizing:border-box;height:29px;padding:4px;border-right:1px solid #a9b7c3;border-bottom:1px solid #a9b7c3;white-space:nowrap}
    .hpa-table thead th{position:sticky;top:0;background:#e7edf3;z-index:5;height:30px;color:#263b4d}
    .hpa-table thead .hpa-hour{top:30px;font-size:10px;width:64px}.hpa-table .hpa-shift-day{background:#d8ead2}.hpa-table .hpa-shift-night{background:#d5e2f3}
    .hpa-sticky-date,.hpa-sticky-day,.hpa-sticky-month{position:sticky;z-index:3;background:var(--card-bg,#fff);color:var(--text-color,#243447);font-weight:500}
    .hpa-sticky-date{left:0;width:var(--date-width);min-width:var(--date-width);max-width:var(--date-width)}
    .hpa-sticky-day{left:var(--date-width);width:var(--day-width);min-width:var(--day-width);max-width:var(--day-width)}
    .hpa-sticky-month{left:calc(var(--date-width) + var(--day-width));width:var(--month-width);min-width:var(--month-width);max-width:var(--month-width)}
    .hpa-table thead .hpa-sticky-date,.hpa-table thead .hpa-sticky-day,.hpa-table thead .hpa-sticky-month{z-index:8}
    .hpa-cell{font-weight:600;width:64px;min-width:64px;color:#172a1a}.hpa-cell sup{font-size:9px;margin-left:2px}
    .hpa-table .hpa-night-start{border-left:3px solid #354f75}.hpa-green{background:#d8efdf;color:#20623c}.hpa-amber{background:#fff0c7;color:#745316}
    .hpa-red{background:#fbe1e3;color:#853a42}.hpa-bright_red{background:#ffb9c0;color:#8d2531}.hpa-grey{background:#f7f8fa;color:#637080}
    .hpa-month-divider th{text-align:left;background:#d9e5ef;color:#203f58;height:27px}.hpa-month-divider span{position:sticky;left:8px}
    .hpa-chart{display:flex;align-items:center;gap:24px;margin-top:16px;padding:12px;border:1px solid var(--border-color,#e1e6eb);border-radius:8px}
    .hpa-pie{width:150px;height:150px;border-radius:50%;flex-shrink:0}.hpa-legend{font-size:12px;display:grid;gap:8px}
    .hpa-legend>div{display:grid;grid-template-columns:12px minmax(0,1fr) auto auto;gap:8px;align-items:center}.hpa-legend i{width:10px;height:10px;border:1px solid #71808c}
    .hpa-message{padding:20px;font-size:14px}.hpa-shell[aria-busy=true]{opacity:.7}
    @media(max-width:600px){.hpa-shell{--date-width:82px;--day-width:38px;--month-width:90px}.hpa-filters{grid-template-columns:1fr 1fr;gap:8px}
      .hpa-shift-day span,.hpa-shift-night span{position:sticky;left:calc(var(--date-width) + var(--day-width) + var(--month-width) + 8px);display:block;width:max-content;text-align:left}
      .hpa-filters>div:first-child{grid-column:1/-1}.hpa-targets{grid-template-columns:1fr;gap:12px;font-size:11px}.hpa-filters button{font-size:11px}.hpa-chart{gap:12px;flex-wrap:wrap}.hpa-pie{width:110px;height:110px}
      .hpa-legend{font-size:11px;width:100%}.hpa-table{min-width:1746px;font-size:10px}.hpa-table th,.hpa-table td{padding:3px}}
  </style>`;

  class HourlyProductivityAnalysisPage {
    constructor(wrapper) {
      this.page = frappe.ui.make_app_page({parent:wrapper,title:'Hourly Productivity Analysis',single_column:true});
      this.method = 'is_production.production.page.hourly_productivity_analysis.hourly_productivity_analysis.';
      this.requestToken = 0;
      this.initializing = true;
      this.page.main.html(`${styles}<div class="hpa-shell"><div class="hpa-filters"><div class="hpa-site"></div>
        <div class="hpa-from_date"></div><div class="hpa-to_date"></div><button class="btn btn-primary hpa-refresh">Refresh</button><button class="btn btn-default hpa-investigate">Open Detailed Investigation</button></div>
        <div class="hpa-content" aria-live="polite"></div></div>`);
      for (const [name,label,type,options] of [['site','Site','Link','Location'],['from_date','From Date','Date'],['to_date','To Date','Date']]) {
        this[name] = frappe.ui.form.make_control({parent:this.page.main.find('.hpa-' + name),render_input:true,
          df:{fieldname:name,label,fieldtype:type,options,reqd:1,onchange:()=>this.load()}});
      }
      this.page.main.on('click','.hpa-refresh',()=>this.load());
      this.page.main.on('click','.hpa-investigate',()=>this.openInvestigation());
      this.message('Select a site and date range.');
      this.initialize();
    }
    openInvestigation() {return frappe.set_route('/desk/production/hourly-productivity-summary');}
    message(text) {this.page.main.find('.hpa-content').html(`<div class="hpa-message">${escape(text)}</div>`);}
    async initialize() {
      try {
        const response = await frappe.call({method:this.method + 'get_defaults'});
        await this.from_date.set_value(response.message.from_date);
        await this.to_date.set_value(response.message.to_date);
        this.initializing = false;
        await this.load();
      } catch (error) {this.initializing = false;this.message('Could not load defaults. Select the dates and refresh.');}
    }
    async load() {
      if (this.initializing) return;
      const token = ++this.requestToken;
      const args = {site:this.site.get_value(),from_date:this.from_date.get_value(),to_date:this.to_date.get_value()};
      this.page.main.find('.hpa-shell').attr('aria-busy','false');
      if (!args.site || !args.from_date || !args.to_date) {this.message('Select a site and date range.');return;}
      if (args.from_date > args.to_date) {this.message('From Date must be on or before To Date.');return;}
      this.message('Loading hourly performance…');
      this.page.main.find('.hpa-shell').attr('aria-busy','true');
      try {
        const response = await frappe.call({method:this.method + 'get_analysis',args});
        if (token !== this.requestToken) return;
        this.page.main.find('.hpa-content').html(renderAnalysis(response.message));
      } catch (error) {
        if (token !== this.requestToken) return;
        this.message('Unable to load. Every date needs a covering MPP, and read access is required for Site, MPP, production, Assets and PBM. Select at most 366 days.');
      } finally {if (token === this.requestToken) this.page.main.find('.hpa-shell').attr('aria-busy','false');}
    }
  }
  if (typeof frappe !== 'undefined') frappe.pages['hourly-productivity-analysis'].on_page_load = wrapper => {
    wrapper.hourlyProductivityAnalysis = new HourlyProductivityAnalysisPage(wrapper);
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = {renderAnalysis,styles,displayPercentages,HourlyProductivityAnalysisPage};
})();
