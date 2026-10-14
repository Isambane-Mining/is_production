/* Read-only range dashboard. Pure rendering and navigation helpers support local tests. */
(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const number = (value, digits = 0) => value == null ? '—' :
    Number(value).toLocaleString(undefined, {maximumFractionDigits: digits});
  const categories = [
    ['green', 'Target achieved · ≥100%'],
    ['amber', 'Near target · 85–99.9%'],
    ['red', 'Below target · <85%'],
    ['bright_red', 'Zero BCM · machines available'],
    ['grey', 'Not planned to work']
  ];
  const status = {recorded: 'Recorded', missing: 'Missing record · 0 shown', pending: 'Future',
    in_progress: 'In progress', in_progress_missing: 'In progress · no record'};
  const timelineLegend = [['used', 'Available · used'], ['idle', 'Available · unused'],
    ['unavailable', 'PBM unavailable'], ['partial', 'Partially available'], ['missing', 'Missing / unfinished']];

  function selectedIndex(data, date) {
    const index = data.days.findIndex(day => day.date === date);
    return index < 0 ? 0 : index;
  }

  function hourStatus(hour) {
    if (hour.completed === false) return `${status[hour.record_status] || 'Unfinished'}${hour.schedule === 'off' ? ' · not planned to work' : hour.schedule === 'unknown' ? ' · schedule unclassified' : ''}`;
    if (hour.schedule === 'off') return 'Not planned to work';
    if (hour.schedule === 'unknown') return 'Schedule unclassified';
    if (hour.target_bcm === 0) return 'No planned excavators';
    return status[hour.record_status] || hour.record_status;
  }

  function renderSummary(data, selectedDate) {
    const s = data.summary;
    const index = selectedIndex(data, selectedDate);
    const day = data.days[index];
    const achievementColour = s.achievement == null ? 'neutral' : s.achievement >= 100 ? 'green' : s.achievement >= 85 ? 'amber' : 'red';
    const kpi = (label, value, note, cls = '') => `<article class="hps-kpi ${cls}"><small>${escape(label)}</small>
      <strong>${value}</strong><small>${escape(note)}</small></article>`;
    const average = (name, available, utilised) => `<article class="hps-kpi"><small>${name} Available / Utilised</small>
      <div class="hps-average"><span>Average ${name} Available <b>${number(available, 2)}</b></span>
      <span>Average ${name} Utilised <b>${number(utilised, 2)}</b></span></div><small title="Matching completed, recorded, planned working hours">${number(s.average_hours)} hourly observations</small></article>`;
    let end = 0;
    const stops = categories.map(([key]) => {
      end += data.pie.percentages[key];
      return `data-pie-${key}="${end}"`;
    }).join(' ');
    const period = data.from_date === data.to_date ? data.from_date : `${data.from_date} – ${data.to_date}`;
    const excluded = [['missing', 'missing'], ['unfinished', 'unfinished'], ['schedule_unknown', 'schedule unclassified'], ['no_target', 'no target']]
      .filter(([key]) => data.excluded[key]).map(([key, label]) => `${number(data.excluded[key])} ${label}`).join(' · ');
    return `<div class="hps-period">${escape(data.site)} · ${escape(period)} · ${escape(data.shift)}</div>
      <div class="hps-kpis">
        ${kpi('Actual BCM', number(s.actual_bcm, 1), 'Recorded excavator output')}
        ${kpi('BCM Target', number(s.target_bcm), 'Saved MPP production count')}
        ${kpi('Achievement %', s.achievement == null ? '—' : number(s.achievement, 2) + '%',
          `${number(s.completed_actual_bcm, 1)} / ${number(s.completed_target_bcm)} · reached on completed hours`, 'hps-' + achievementColour)}
        ${average('ADTs', s.average_adts_available, s.average_adts_utilised)}
        ${average('Excavators', s.average_excavators_available, s.average_excavators_utilised)}
      </div>
      <div class="hps-overview">
        <section class="hps-panel hps-targets"><h3>Production Targets <small>Per Excavator</small></h3>
          <table><tbody><tr><td>First shift hour <small>15 minutes</small></td><td>55 BCM</td></tr>
            <tr><td>Normal operating hour <small>60 minutes</small></td><td>220 BCM</td></tr>
            <tr><td>Last shift hour <small>45 minutes</small></td><td>165 BCM</td></tr></tbody></table>
          <small class="hps-basis">${escape(day.plan_name || 'No covering MPP')} · ${number(day.production_excavator_count)} production excavators</small>
        </section>
        <section class="hps-panel hps-chart-section" aria-label="Hourly performance classification">
          ${data.pie.total_hours ? `<div class="hps-pie" role="img" aria-label="${escape(categories.map(([key, label]) => `${label}: ${number(data.pie.percentages[key], 2)}%, ${data.pie.counts[key]} hours`).join('; '))}"
            ${stops}></div>` : '<div class="hps-no-pie">No eligible hours</div>'}
          <div class="hps-legend"><h3>Hourly Performance <small>${number(data.pie.total_hours)} eligible hours</small></h3>
            ${categories.map(([key, label]) => `<span><i class="hps-dot hps-slice-${key}"></i>${escape(label)}
              <b>${number(data.pie.percentages[key], 1)}% · ${number(data.pie.counts[key])} h</b></span>`).join('')}
            <small>Reached in completed hours · ${number(s.completed_target_bcm)} BCM target</small>
            ${excluded ? `<small class="hps-excluded">Excluded: ${escape(excluded)}</small>` : ''}
          </div>
        </section>
      </div>
      <nav class="hps-day-nav" aria-label="Production day navigation">
        <button class="btn btn-default hps-previous" ${index === 0 ? 'disabled' : ''} aria-label="Previous production day">← Previous</button>
        <strong class="hps-selected-date">${escape(day.date)} <small>${index + 1} / ${data.days.length}</small></strong>
        <button class="btn btn-default hps-next" ${index === data.days.length - 1 ? 'disabled' : ''} aria-label="Next production day">Next →</button>
      </nav>
      ${day.indicators.length ? `<div class="hps-indicators">${day.indicators.map(r =>
        `<span>${escape(r.shift)} · ${escape(r.period)} <b>${escape(r.label)}</b></span>`).join('')}</div>` : ''}
      <div class="hps-daily-grid">
        <section class="hps-panel"><h3>Hourly Performance</h3><div class="hps-table-wrap"><table class="hps-table hps-hourly">
          <thead><tr><th>Hour</th><th>Actual BCM</th><th>Target BCM</th><th>Achievement %</th>
            <th title="PBM-adjusted available minutes ÷ 60">ADTs Available</th><th title="Distinct ADTs with recorded loads in this hour">ADTs Utilised</th>
            <th title="Verified production excavator available minutes ÷ 60">Excavators Available</th>
            <th title="Verified production excavators with loads in this hour">Excavators Utilised</th>
            <th title="Production excavator PBM minutes ÷ 60">Excavators PBM</th></tr></thead>
          <tbody>${day.hours.map((h, i) => `${i === 0 || h.shift !== day.hours[i - 1].shift ?
            `<tr class="hps-shift-band"><th colspan="9">${escape(h.shift.toUpperCase())} SHIFT</th></tr>` : ''}
            <tr class="hps-${escape(h.category)} hps-hour-row"><th scope="row">${escape(h.label)}<small>${escape(hourStatus(h))}</small></th>
              <td>${number(h.actual_bcm, 1)}</td><td>${number(h.target_bcm)}</td><td>${h.achievement == null ? '—' : number(h.achievement, 1) + '%'}</td>
              <td>${number(h.available, 2)}</td><td>${number(h.has_record ? h.utilised : null)}</td>
              <td>${number(h.excavator_available, 2)}</td><td>${number(h.has_record ? h.excavator_utilised : null)}</td>
              <td>${number(h.excavator_unavailable, 2)}</td></tr>`).join('')}</tbody></table></div></section>
        <section class="hps-panel"><h3>Excavator Production</h3><div class="hps-table-wrap"><table class="hps-table hps-timings">
          <thead><tr><th>Excavator</th><th>BCM</th><th title="Earliest valid captured first load within the shift">First Load</th>
            <th title="Latest valid captured last load within the shift">Last Load</th></tr></thead>
          <tbody>${day.excavator_production.map(r => `<tr><th scope="row">${escape(r.excavator)}
            <small>${data.shift === 'Full Day' ? escape(r.shift) + ' · ' : ''}${escape(r.availability_status)}</small></th>
            <td>${number(r.actual_bcm, 1)}</td><td>${escape(r.first_load_time || '—')}</td><td>${escape(r.last_load_time || '—')}</td></tr>`).join('') ||
            '<tr><td colspan="4">No excavator records</td></tr>'}</tbody></table></div></section>
      </div>
      <section class="hps-panel hps-timeline-panel"><h3>ADT Availability</h3>
        <div class="hps-timeline-legend">${timelineLegend.map(([key, label]) => `<span><i class="hps-dot hps-state-${key}"></i>${label}</span>`).join('')}</div>
        <div class="hps-table-wrap"><table class="hps-table hps-timeline"><thead><tr><th rowspan="2">ADT</th>
          ${(data.shift === 'Full Day' ? ['Day', 'Night'] : [data.shift]).map(shift => `<th colspan="12" class="hps-shift-band">${escape(shift.toUpperCase())} SHIFT</th>`).join('')}</tr>
          <tr>${day.hours.map(h => `<th>${escape(h.label)}</th>`).join('')}</tr></thead><tbody>
          ${day.timeline.map(r => `<tr><th scope="row">${escape(r.adt)}</th>${r.cells.map(cell =>
            `<td><div class="hps-block hps-state-${escape(cell.state)}" title="${escape(status[cell.record_status] || cell.record_status)}">${number(cell.available_minutes, 1)}m · ${number(cell.loads)} loads</div></td>`).join('')}</tr>`).join('') ||
            `<tr><td colspan="${day.hours.length + 1}">No accessible ADTs</td></tr>`}</tbody></table></div>
      </section>`;
  }


  function applyChartData(root) {
    root.find('.hps-pie').each((_, element) => {
      for (const key of ['green', 'amber', 'red', 'bright_red', 'grey']) {
        element.style.setProperty(`--hps-${key}-end`, `${Number(element.getAttribute('data-pie-' + key))}%`);
      }
    });
  }

  class HourlyProductivitySummaryPage {
    constructor(wrapper, options = {}) {
      this.page = options.main ? {main: options.main} : frappe.ui.make_app_page({parent: wrapper, title: 'Hourly Productivity Summary', single_column: true});
      this.method = 'is_production.production.page.hourly_productivity_summary.';
      this.requestToken = 0;
      this.initializing = true;
      this.filtersReady = new Promise(resolve => {this.resolveFiltersReady = resolve;});
      this.initialFilters = options.initialFilters || {};
      this.onOpenSummary = options.onOpenSummary;
      this.data = null;
      this.selectedDate = null;
      this.page.main.html(`<div class="hps-shell"><div class="hps-filters"><div class="hps-site"></div>
        <div class="hps-from_date"></div><div class="hps-to_date"></div><div class="hps-shift"></div>
        <button class="btn btn-primary hps-refresh">Refresh</button></div><div class="hps-content" aria-live="polite"></div></div>`);
      for (const [name, type, label, options] of [['site', 'Link', 'Site', 'Location'], ['from_date', 'Date', 'From Date'],
        ['to_date', 'Date', 'To Date'], ['shift', 'Select', 'Shift', 'Day\nNight\nFull Day']]) {
        this[name] = frappe.ui.form.make_control({parent: this.page.main.find(`.hps-${name}`), render_input: true,
          df: {fieldname: name, fieldtype: type, label, options, reqd: 1, onchange: () => this.load()}});
      }
      this.page.main.on('click', '.hps-refresh', () => this.load());
      this.page.main.on('click', '.hps-previous', () => this.navigate(-1));
      this.page.main.on('click', '.hps-next', () => this.navigate(1));
      this.message('Select a site to view productivity.');
      this.ready = this.initialize();
    }

    message(text) {
      this.page.main.find('.hps-content').html(`<div class="hps-message">${escape(text)}</div>`);
    }

    async initialize() {
      try {
        const response = await frappe.call({method: this.method + 'hourly_productivity_summary.get_defaults'});
        await this.from_date.set_value(this.initialFilters.from_date || response.message.date);
        await this.to_date.set_value(this.initialFilters.to_date || response.message.date);
        await this.shift.set_value(response.message.shift);
        if (this.initialFilters.site) await this.site.set_value(this.initialFilters.site);
        this.initializing = false;
        this.resolveFiltersReady();
        await this.load();
      } catch (error) {
        this.initializing = false;
        this.resolveFiltersReady();
        this.message('Could not load defaults. Select dates and shift, then refresh.');
      }
    }

    draw() {
      this.page.main.find('.hps-content').html(renderSummary(this.data, this.selectedDate));
      applyChartData(this.page.main);
    }

    navigate(direction) {
      if (!this.data) return;
      const index = selectedIndex(this.data, this.selectedDate) + direction;
      if (index < 0 || index >= this.data.days.length) return;
      this.selectedDate = this.data.days[index].date;
      this.draw();
    }

    async load() {
      if (this.initializing) return;
      const token = ++this.requestToken;
      const args = {site: this.site.get_value(), from_date: this.from_date.get_value(),
        to_date: this.to_date.get_value(), shift: this.shift.get_value()};
      this.data = null;
      if (!args.site || !args.from_date || !args.to_date || !args.shift) {
        this.page.main.find('.hps-shell').attr('aria-busy', 'false');
        this.message('Select a site, date range and shift.');
        return;
      }
      if (args.from_date > args.to_date) {
        this.page.main.find('.hps-shell').attr('aria-busy', 'false');
        this.message('From Date must be on or before To Date.');
        return;
      }
      this.page.main.find('.hps-shell').attr('aria-busy', 'true');
      this.message('Loading productivity…');
      try {
        const response = await frappe.call({method: this.method + 'hourly_productivity_summary.get_dashboard', args});
        if (token !== this.requestToken) return;
        this.data = response.message;
        this.selectedDate = this.data.days[selectedIndex(this.data, this.selectedDate)].date;
        this.draw();
      } catch (error) {
        if (token !== this.requestToken) return;
        this.data = null;
        this.message('Unable to load productivity. Check the date range and your access to the selected site, then refresh.');
      } finally {
        if (token === this.requestToken) this.page.main.find('.hps-shell').attr('aria-busy', 'false');
      }
    }
  }

  if (typeof window !== 'undefined') {
    window.is_production = window.is_production || {};
    window.is_production.hourly_productivity = window.is_production.hourly_productivity || {};
    window.is_production.hourly_productivity.Summary = HourlyProductivitySummaryPage;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {renderSummary, selectedIndex, HourlyProductivitySummaryPage};
})();
