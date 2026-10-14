/* Compact operational-day matrix; no writes to planning or production. */
(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const number = (value, digits = 0) => value == null ? '—' :
    Number(value).toLocaleString(undefined, {maximumFractionDigits: digits});
  const categories = [
    ['green', 'Target achieved · ≥100%'],
    ['amber', '85–99.9% achieved'],
    ['red', 'Below 85%'],
    ['bright_red', 'Zero production while machines were available'],
    ['grey', 'Not planned to work']
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
    const gradient = categories.map(([key]) => {
      gradientStart += Number(data.pie.categories[key].percent);
      return `data-pie-${key}="${gradientStart}"`;
    }).join(' ');
    const aria = categories.map(([key, label]) => `${label}: ${number(data.pie.categories[key].hours)} hours, ${number(percentages[key], 1)}%`).join('; ');
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
        ${data.pie.total_hours ? `<div class="hpa-pie" role="img" aria-label="${escape(aria)}" ${gradient}></div>` :
          '<div class="hpa-no-pie">No eligible completed hours</div>'}
        <div class="hpa-legend"><strong>Hourly performance · ${number(data.pie.total_hours)} included hours</strong>
          ${categories.map(([key, label]) => `<div><i class="hpa-slice-${key}"></i><span>${escape(label)}</span>
            <b>${number(percentages[key], 1)}%</b><small>${number(data.pie.categories[key].hours)} hours</small></div>`).join('')}
          ${excluded ? `<small class="hpa-excluded">Excluded: ${escape(excluded)}</small>` : ''}
        </div></section>`;
  }


  function applyChartData(root) {
    root.find('.hpa-pie').each((_, element) => {
      for (const key of ['green', 'amber', 'red', 'bright_red', 'grey']) {
        element.style.setProperty(`--hpa-${key}-end`, `${Number(element.getAttribute('data-pie-' + key))}%`);
      }
    });
  }

  class HourlyProductivityAnalysisPage {
    constructor(wrapper, options = {}) {
      this.page = options.main ? {main: options.main} : frappe.ui.make_app_page({parent:wrapper,title:'Hourly Productivity Analysis',single_column:true});
      this.method = 'is_production.production.page.hourly_productivity_analysis.hourly_productivity_analysis.';
      this.requestToken = 0;
      this.initializing = true;
      this.filtersReady = new Promise(resolve => {this.resolveFiltersReady = resolve;});
      this.initialFilters = options.initialFilters || {};
      this.onOpenSummary = options.onOpenSummary;
      this.page.main.html(`<div class="hpa-shell"><div class="hpa-filters"><div class="hpa-site"></div>
        <div class="hpa-from_date"></div><div class="hpa-to_date"></div><button class="btn btn-primary hpa-refresh">Refresh</button><button class="btn btn-default hpa-investigate">Open Detailed Investigation</button></div>
        <div class="hpa-content" aria-live="polite"></div></div>`);
      for (const [name,label,type,options] of [['site','Site','Link','Location'],['from_date','From Date','Date'],['to_date','To Date','Date']]) {
        this[name] = frappe.ui.form.make_control({parent:this.page.main.find('.hpa-' + name),render_input:true,
          df:{fieldname:name,label,fieldtype:type,options,reqd:1,onchange:()=>this.load()}});
      }
      this.page.main.on('click','.hpa-refresh',()=>this.load());
      this.page.main.on('click','.hpa-investigate',()=>this.openInvestigation());
      this.message('Select a site and date range.');
      this.ready = this.initialize();
    }
    openInvestigation() {return this.onOpenSummary ? this.onOpenSummary() : frappe.set_route('/desk/production/hourly-productivity-summary');}
    message(text) {this.page.main.find('.hpa-content').html(`<div class="hpa-message">${escape(text)}</div>`);}
    async initialize() {
      try {
        const response = await frappe.call({method:this.method + 'get_defaults'});
        await this.from_date.set_value(this.initialFilters.from_date || response.message.from_date);
        await this.to_date.set_value(this.initialFilters.to_date || response.message.to_date);
        if (this.initialFilters.site) await this.site.set_value(this.initialFilters.site);
        this.initializing = false;
        this.resolveFiltersReady();
        await this.load();
      } catch (error) {this.initializing = false;this.resolveFiltersReady();this.message('Could not load defaults. Select the dates and refresh.');}
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
        applyChartData(this.page.main);
      } catch (error) {
        if (token !== this.requestToken) return;
        this.message('Unable to load. Every date needs a covering MPP, and read access is required for Site, MPP, production, Assets and PBM. Select at most 366 days.');
      } finally {if (token === this.requestToken) this.page.main.find('.hpa-shell').attr('aria-busy','false');}
    }
  }
  if (typeof window !== 'undefined') {
    window.is_production = window.is_production || {};
    window.is_production.hourly_productivity = window.is_production.hourly_productivity || {};
    window.is_production.hourly_productivity.Analysis = HourlyProductivityAnalysisPage;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {renderAnalysis,displayPercentages,HourlyProductivityAnalysisPage};
})();
