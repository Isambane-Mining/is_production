/* Read-only range dashboard. Pure rendering and navigation helpers support local tests. */
(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const number = (value, digits = 0) => value == null ? '—' :
    Number(value).toLocaleString(undefined, {maximumFractionDigits: digits});
  const categories = [
    ['green', 'Target achieved · ≥100%', '#249b67'],
    ['amber', 'Near target · 85–99.9%', '#e9b44b'],
    ['red', 'Below target · <85%', '#e6a5a8'],
    ['bright_red', 'Zero BCM · machines available', '#ed5b67'],
    ['grey', 'Not planned to work', '#e2e6eb']
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
    const stops = categories.map(([key, , hex]) => {
      const start = end;
      end += data.pie.percentages[key];
      return `${hex} ${start}% ${end}%`;
    }).join(',');
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
            style="background:conic-gradient(${stops})"></div>` : '<div class="hps-no-pie">No eligible hours</div>'}
          <div class="hps-legend"><h3>Hourly Performance <small>${number(data.pie.total_hours)} eligible hours</small></h3>
            ${categories.map(([key, label, hex]) => `<span><i class="hps-dot" style="background:${hex}"></i>${escape(label)}
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

  const styles = `<style>
    .hps-shell{max-width:1480px;margin:auto;padding:12px 0;color:#243447;font-size:13px}
    .hps-shell *{box-sizing:border-box}.hps-filters{display:grid;grid-template-columns:2fr 1fr 1fr 1fr auto;gap:10px;align-items:end;margin-bottom:12px}
    .hps-filters .form-group{margin-bottom:0}.hps-filters button{margin-bottom:4px}.hps-period{font-size:12px;color:#647484;margin:8px 0}
    .hps-kpis{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px}
    .hps-kpi{border:1px solid #dde4eb;border-top:3px solid #5d91b7;border-radius:9px;padding:12px;background:#fff;min-width:0}
    .hps-kpi>strong{display:block;font-size:25px;margin:6px 0}.hps-kpi small{display:block;font-size:11px;line-height:1.5}
    .hps-average{display:grid;gap:7px;margin:8px 0;font-size:11px}.hps-average span{display:flex;justify-content:space-between;gap:5px}.hps-average b{font-size:17px}
    .hps-green{background:#eaf7ef;color:#196440}.hps-amber{background:#fff6df;color:#7d5607}.hps-red{background:#fff0f1;color:#8d353b}
    .hps-bright_red{background:#ffdde1;color:#962631}.hps-grey{background:#f2f4f7;color:#637080}.hps-neutral{background:#fff;color:#536373}
    .hps-kpi.hps-green{border-top-color:#249b67}.hps-kpi.hps-amber{border-top-color:#e9b44b}.hps-kpi.hps-red{border-top-color:#e6a5a8}
    .hps-panel{background:#fff;border:1px solid #dde4eb;border-radius:9px;padding:12px;min-width:0}
    .hps-shell h3{font-size:14px;margin:0 0 10px;font-weight:600}.hps-shell h3 small{font-size:11px;font-weight:400;color:#637080;margin-left:6px}
    .hps-overview{display:grid;grid-template-columns:1fr 2fr;gap:10px;margin:12px 0}
    .hps-targets table{width:100%;font-size:12px}.hps-targets td{padding:5px 0;border-bottom:1px solid #eef1f5}.hps-targets td:last-child{text-align:right;font-weight:600}
    .hps-targets td small{font-size:10px;color:#6d7987;margin-left:4px}.hps-basis{display:block;font-size:11px;color:#6d7987;margin-top:9px;overflow-wrap:anywhere}
    .hps-chart-section{display:flex;align-items:center;gap:20px}.hps-pie{width:126px;height:126px;border-radius:50%;flex-shrink:0;border:1px solid #e2e6eb}
    .hps-no-pie{width:126px;font-size:12px;color:#6d7987}.hps-legend{display:grid;gap:6px;flex:1;min-width:0;font-size:11px}.hps-legend h3{margin-bottom:2px}
    .hps-legend span{display:flex;align-items:center;gap:6px}.hps-legend span b{margin-left:auto;white-space:nowrap}.hps-legend>small{color:#637080;font-size:10px}
    .hps-dot{width:10px;height:10px;border-radius:3px;display:inline-block;flex-shrink:0}
    .hps-day-nav{display:flex;justify-content:space-between;align-items:center;gap:10px;margin:14px 0 10px}.hps-day-nav strong{font-size:14px}.hps-day-nav small{font-size:10px;font-weight:400;margin-left:6px}
    .hps-indicators{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;font-size:11px;color:#283442}.hps-indicators span{padding:4px 8px;border:1px solid #e3e7ed;border-radius:5px;background:#fafbfc}
    .hps-daily-grid{display:grid;grid-template-columns:minmax(0,2.5fr) minmax(310px,1fr);gap:10px;align-items:start}
    .hps-table-wrap{max-width:100%;overflow:auto;border:1px solid #e4e9ef;border-radius:6px}
    .hps-table{width:100%;border-collapse:separate;border-spacing:0;font-size:11px;white-space:nowrap}
    .hps-table th,.hps-table td{text-align:right;padding:8px;border-bottom:1px solid #e5eaf0;vertical-align:middle}
    .hps-table th:first-child,.hps-table td:first-child{text-align:left}.hps-table thead{background:#f5f7fa;color:#3d4f60}.hps-table small{display:block;font-size:9px;font-weight:400;color:inherit;margin-top:2px}
    .hps-hourly{min-width:880px}.hps-hourly th:first-child{position:sticky;left:0;background:inherit}.hps-shift-band{background:#eaf0f6;color:#344d64}
    .hps-table .hps-shift-band th{text-align:left;font-size:10px;letter-spacing:.4px;padding:5px 8px}.hps-timings{min-width:290px}
    .hps-timeline-panel{margin-top:12px}.hps-timeline-legend{display:flex;flex-wrap:wrap;gap:8px 16px;margin-bottom:10px;font-size:11px}.hps-timeline-legend span{display:flex;align-items:center;gap:5px}
    .hps-timeline th:first-child{position:sticky;left:0;background:#f5f7fa;z-index:1;min-width:90px}.hps-timeline td{padding:3px;border-bottom:1px solid #edf0f4}
    .hps-timeline th{text-align:center}.hps-block{min-width:112px;padding:8px 5px;border-radius:4px;text-align:center;font-size:10px}
    .hps-state-used{background:#dcefe4;color:#246442}.hps-state-idle{background:#e4edf9;color:#3d5b82}.hps-state-unavailable{background:#f6dce0;color:#923340}
    .hps-state-partial{background:#fff0cf;color:#805918}.hps-state-missing{background:#eef1f5;color:#687685}
    .hps-message{padding:20px;font-size:14px}.hps-shell[aria-busy=true]{opacity:.7}
    @media(max-width:1100px){.hps-kpis{grid-template-columns:repeat(3,minmax(0,1fr))}.hps-daily-grid{grid-template-columns:minmax(0,1fr)}.hps-timings{min-width:0}}
    @media(max-width:650px){.hps-filters{grid-template-columns:1fr 1fr}.hps-filters>div:first-child{grid-column:1/-1}.hps-filters button{margin:0}
      .hps-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.hps-kpi{padding:9px}.hps-kpi>strong{font-size:22px}.hps-overview{grid-template-columns:1fr}
      .hps-chart-section{gap:10px}.hps-pie{width:85px;height:85px}.hps-legend{font-size:10px}.hps-legend span{flex-wrap:wrap}.hps-legend span b{margin-left:0}
      .hps-panel{padding:9px}.hps-day-nav .btn{font-size:11px;padding:6px}.hps-day-nav strong{font-size:12px}.hps-day-nav small{display:block;margin:0;text-align:center}}
  </style>`;

  class HourlyProductivitySummaryPage {
    constructor(wrapper) {
      this.page = frappe.ui.make_app_page({parent: wrapper, title: 'Hourly Productivity Summary', single_column: true});
      this.method = 'is_production.production.page.hourly_productivity_summary.';
      this.requestToken = 0;
      this.initializing = true;
      this.data = null;
      this.selectedDate = null;
      this.page.main.html(`${styles}<div class="hps-shell"><div class="hps-filters"><div class="hps-site"></div>
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
      this.initialize();
    }

    message(text) {
      this.page.main.find('.hps-content').html(`<div class="hps-message">${escape(text)}</div>`);
    }

    async initialize() {
      try {
        const response = await frappe.call({method: this.method + 'hourly_productivity_summary.get_defaults'});
        await this.from_date.set_value(response.message.date);
        await this.to_date.set_value(response.message.date);
        await this.shift.set_value(response.message.shift);
        this.initializing = false;
        await this.load();
      } catch (error) {
        this.initializing = false;
        this.message('Could not load defaults. Select dates and shift, then refresh.');
      }
    }

    draw() {
      this.page.main.find('.hps-content').html(renderSummary(this.data, this.selectedDate));
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
        const response = await frappe.call({method: this.method + 'summary_dashboard.get_dashboard', args});
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

  if (typeof frappe !== 'undefined') {
    frappe.pages['hourly-productivity-summary'].on_page_load = wrapper => {
      wrapper.hourlyProductivitySummary = new HourlyProductivitySummaryPage(wrapper);
    };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {renderSummary, styles, selectedIndex, HourlyProductivitySummaryPage};
})();
