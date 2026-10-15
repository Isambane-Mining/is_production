/* Read-only Desk page. Render functions are also used by the local UI tests. */
(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const number = (value, digits = 0) => value == null ? '—' :
    Number(value).toLocaleString(undefined, {maximumFractionDigits: digits});
  const status = {
    recorded: 'Recorded', missing: 'No record · 0 shown', pending: 'Pending',
    in_progress: 'In progress', in_progress_missing: 'In progress · no record'
  };
  const colour = (value, thresholds) => value == null ? 'neutral' :
    value >= thresholds.green ? 'green' : value >= thresholds.orange ? 'orange' : 'red';

  function renderSummary(data) {
    const s = data.summary;
    const pie = data.pie;
    const achievedEnd = pie.target ? pie.achieved / pie.target * 100 : 0;
    const estimateEnd = pie.target ? (pie.achieved + pie.availability_estimate) / pie.target * 100 : 0;
    const cards = [
      ['Actual BCM', number(s.actual_bcm, 1), 'All recorded excavator output'],
      ['Shift target BCM', number(s.target_bcm), `${number(data.production_excavator_count)} MPP excavators × 2,420`],
      ['Achievement', s.achievement == null ? '—' : `${number(s.achievement, 2)}%`,
        `${number(s.completed_actual_bcm, 1)} / ${number(s.completed_target_bcm)} · completed hours`],
      ['ADTs available / utilised', `${number(s.average_available, 2)} / ${number(s.adts_utilised)}`,
        '12-hour average available / unique utilised']
    ];
    const fleet = (label, rows) => `<div class="hps-fleet"><strong>${escape(label)}</strong>
      ${rows.length ? rows.map(row => `<span>${escape(row.label)} · ${number(row.actual_bcm, 1)} BCM
        ${row.classification ? `<small>${escape(row.classification)}</small>` : ''}</span>`).join('') : '<span>None listed</span>'}</div>`;
    return `<div class="hps-kpis">${cards.map(([label, value, note], index) =>
      `<article class="hps-kpi ${index === 2 ? 'hps-' + colour(s.achievement, data.thresholds) : ''}">
        <small>${escape(label)}</small><strong>${escape(value)}</strong><small>${escape(note)}</small></article>`).join('')}</div>
      <div class="hps-basis">${data.plan_name ? `MPP: ${escape(data.plan_name)}` : 'No covering MPP'} ·
        First hour 55 · Normal 220 · Last hour 165 BCM per production excavator. Targets stay fixed during breakdowns.</div>
      <section class="hps-chart-section" aria-label="Completed-hour target composition">
        ${pie.target ? `<div class="hps-pie" role="img" aria-label="${escape(`Target achieved ${number(pie.achieved, 1)} BCM; PBM-backed availability estimate ${number(pie.availability_estimate, 1)} BCM; remaining shortfall ${number(pie.remaining, 1)} BCM`)}"
          style="background:conic-gradient(#168552 0% ${achievedEnd}%, #e7a22b ${achievedEnd}% ${estimateEnd}%, #d64b50 ${estimateEnd}% 100%)"></div>` :
          '<div class="hps-no-pie">No completed-hour target</div>'}
        <div class="hps-legend"><strong>Completed hours · ${number(pie.target)} BCM target</strong>
          <span><i class="hps-dot green"></i>Target achieved <b>${number(pie.achieved, 1)} BCM</b></span>
          <span><i class="hps-dot orange"></i>PBM-backed availability estimate <b>${number(pie.availability_estimate, 1)} BCM</b></span>
          <span><i class="hps-dot red"></i>Remaining shortfall <b>${number(pie.remaining, 1)} BCM</b></span>
          <small>Achieved is capped at target; excess BCM remains in the KPI.</small>
        </div>
      </section>
      <details class="hps-explanation"><summary>How the availability estimate works</summary>
        <p>Recorded excavator downtime and the unavailable fraction of its MPP-assigned ADTs estimate exposed capacity at the planned rate.
        Overlapping excavator and ADT downtime counts once. Each estimate is capped at its team's recorded shortfall and the site's shortfall.
        This assumes equal ADT contributions and downtime spread uniformly across productive minutes; it is not proof of causation.</p>
        <p>Only completed hours with production records and an identified MPP roster support attribution. Missing-record shortfalls remain unattributed.
        Future and current hours are excluded from the pie. PBMs excluded from availability reporting or without a reason are excluded here too.</p>
      </details>
      ${s.missing_hours ? `<div class="hps-warning">${number(s.missing_hours)} completed hours have no production record. Their displayed 0 BCM is unverified.</div>` : ''}
      ${data.warnings.map(w => `<div class="hps-warning">${escape(w)}</div>`).join('')}
      <div class="hps-table-wrap"><table class="hps-table"><caption>Hourly performance</caption>
        <thead><tr><th scope="col">Hour</th><th scope="col">Actual BCM</th><th scope="col">Target BCM</th>
          <th scope="col">Achievement %</th><th scope="col">ADTs Available</th><th scope="col">ADTs Utilised</th>
          <th scope="col">Excavators Available</th><th scope="col">Excavators Utilised</th><th scope="col">Excavators PBM</th></tr></thead>
        <tbody>${data.hours.map(h => `<tr class="hps-${escape(['green', 'orange', 'red'].includes(h.colour) ? h.colour : 'neutral')}">
          <th scope="row">${escape(h.label)}<small>${escape(status[h.record_status] || h.record_status)}</small></th>
          <td>${number(h.actual_bcm, 1)}</td><td>${number(h.target_bcm)}</td>
          <td>${h.achievement == null ? '—' : `${number(h.achievement, 2)}%`}</td>
          <td class="hps-available">${number(h.available, 2)}<small>Δ ${number(h.difference, 2)}</small></td>
          <td class="hps-utilised">${number(h.utilised)}</td><td>${number(h.excavator_available, 2)}</td>
          <td>${number(h.excavator_utilised)}</td><td>${number(h.excavator_unavailable, 2)}
            <small>${number(h.excavator_pbm_count)} affected</small></td></tr>`).join('')}</tbody></table></div>
      <p class="hps-footnote">Available ADTs are weighted by available minutes. An ADT is utilised once it records at least one load.
        Δ = available − utilised; it can be negative with partial-hour availability.</p>
      <p class="hps-footnote">Production excavator availability and PBM unavailability use the same minute weighting.
        “Affected” counts excavators with any PBM overlap; utilised counts distinct excavators with at least one load.
        Unknown MPP classifications show —. Missing records do not verify zero utilisation.</p>
      <details class="hps-details"><summary>Unpaired excavator availability · production/spare unknown</summary>
        <div class="hps-table-wrap"><table class="hps-table"><thead><tr><th>Hour</th><th>Available</th><th>Utilised</th><th>PBM unavailable</th></tr></thead>
        <tbody>${data.hours.map(h => `<tr><th>${escape(h.label)}</th><td>${number(h.unpaired_available, 2)}</td>
          <td>${number(h.unpaired_utilised)}</td><td>${number(h.unpaired_unavailable, 2)}</td></tr>`).join('')}</tbody></table></div></details>
      <details class="hps-details"><summary>ADT availability detail</summary><div class="hps-table-wrap">
        <table class="hps-table"><thead><tr><th>ADT</th><th>Hour</th><th>Available minutes</th><th>Status</th><th>Loads</th></tr></thead>
        <tbody>${(data.details || []).map(r => `<tr><td>${escape(r.adt)}</td><td>${escape(r.hour)}</td>
          <td>${number(r.available_minutes, 1)}</td><td>${escape(r.availability_status)}</td><td>${number(r.loads)}</td></tr>`).join('')}</tbody></table></div></details>
      <h3>First / last recorded load times</h3><div class="hps-excavators">
        ${(data.excavators || []).map(r => `<article><strong>${escape(r.excavator)}</strong><div>
          <span>First <b>${escape(r.first_load_time || '—')}</b></span>
          <span>Last <b>${escape(r.last_load_time || '—')}</b></span></div></article>`).join('') || '<p>No load times recorded.</p>'}</div>
      <p class="hps-footnote">First is the earliest captured First Load; Last is the latest captured Last Load within the shift.
        Restart times do not replace the first load. Missing or invalid times remain —.</p>
      ${fleet('MPP truck-assigned production teams', data.production_excavators || [])}
      ${fleet('Unpaired excavators · production/spare unknown', data.unpaired_excavators || [])}
      <p class="hps-footnote">Spare classification is unavailable from the original saved MPP rows. Empty production
        assignments are stored only in browser localStorage; the page does not infer their identities.</p>
      ${(data.unplanned_excavators || []).length ? fleet('Other recorded excavators · MPP classification unknown', data.unplanned_excavators) : ''}
      <p class="hps-footnote">All recorded excavator output is included in Actual BCM; targets use the saved MPP production count. Colours: green ≥ ${number(data.thresholds.green)}%,
        orange ≥ ${number(data.thresholds.orange)}%, red below ${number(data.thresholds.orange)}%. Missing and unfinished hours are neutral.</p>`;
  }

  const styles = `<style>
    .hps-shell{max-width:1100px;margin:auto;padding:12px 0;color:var(--text-color,#243447)}
    .hps-filters{display:grid;grid-template-columns:2fr 1.3fr 1fr auto;gap:12px;align-items:end;margin-bottom:14px}
    .hps-filters .form-group{margin-bottom:0}.hps-filters button{margin-bottom:4px}
    .hps-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
    .hps-kpi{border:1px solid var(--border-color,#e1e6eb);border-top:4px solid #4799bd;border-radius:12px;padding:12px;background:var(--card-bg,#fff)}
    .hps-kpi strong{display:block;font-size:24px;margin:4px 0;line-height:1.25}.hps-kpi small{display:block;font-size:11px}
    .hps-green{background:#e6f5ed;color:#12613c}.hps-orange{background:#fff3dd;color:#7d4b00}.hps-red{background:#ffeded;color:#9d292e}
    .hps-kpi.hps-green{border-top-color:#168552}.hps-kpi.hps-orange{border-top-color:#e7a22b}.hps-kpi.hps-red{border-top-color:#d64b50}
    .hps-basis,.hps-footnote{font-size:12px;line-height:1.5;margin:10px 0;color:var(--text-muted,#667582)}
    .hps-chart-section{display:flex;align-items:center;gap:20px;padding:12px;border:1px solid var(--border-color,#e1e6eb);border-radius:12px;margin-top:12px}
    .hps-pie{width:120px;height:120px;border-radius:50%;flex-shrink:0}.hps-legend{display:flex;flex-direction:column;gap:7px;font-size:12px}
    .hps-legend span{display:flex;align-items:center;gap:7px;flex-wrap:wrap}.hps-dot{width:10px;height:10px;border-radius:50%;display:inline-block;flex-shrink:0}
    .hps-dot.green{background:#168552}.hps-dot.orange{background:#e7a22b}.hps-dot.red{background:#d64b50}
    .hps-warning{background:#fff3dd;color:#754900;padding:8px 10px;border-radius:7px;font-size:12px;margin:8px 0}
    .hps-explanation,.hps-details{font-size:12px;margin:12px 0}.hps-explanation p{margin:8px 0}.hps-shell summary{cursor:pointer;font-weight:600}
    .hps-table-wrap{overflow:auto;border:1px solid var(--border-color,#e1e6eb);border-radius:10px;margin-top:12px}
    .hps-table{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap}.hps-table caption{caption-side:top;padding:10px;font-weight:600;color:inherit}
    .hps-table th,.hps-table td{text-align:right;padding:9px 10px;border-bottom:1px solid var(--border-color,#dce4e9)}
    .hps-table th:first-child,.hps-table td:first-child{text-align:left}.hps-table thead{background:var(--subtle-fg,#edf1f5);color:var(--text-color,#243447)}
    .hps-table small{display:block;font-size:10px;font-weight:400}.hps-available{font-weight:600}.hps-utilised{font-weight:600}
    .hps-excavators{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px}.hps-excavators article{border:1px solid var(--border-color,#e1e6eb);border-radius:10px;padding:10px}
    .hps-excavators article>div{display:flex;justify-content:space-between;margin-top:8px;font-size:12px}.hps-excavators b{display:block;color:#168552}
    .hps-excavators span:last-child b{color:#b27615}.hps-shell h3{font-size:15px;margin:16px 0 10px}
    .hps-fleet{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:12px;margin:12px 0}.hps-fleet>strong{width:100%}
    .hps-fleet>span{padding:6px 9px;border-radius:7px;background:var(--subtle-fg,#edf1f5)}.hps-fleet small{display:block}
    .hps-message{padding:20px;font-size:14px}.hps-shell[aria-busy=true]{opacity:.7}
    @media(max-width:600px){.hps-filters{grid-template-columns:1fr 1fr;gap:8px}.hps-filters>div:first-child{grid-column:1/-1}
      .hps-filters button{grid-column:1/-1}.hps-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.hps-kpi{padding:10px}.hps-kpi strong{font-size:22px}
      .hps-chart-section{gap:12px;padding:10px}.hps-pie{width:90px;height:90px}.hps-legend{font-size:11px}.hps-table{font-size:12px}
      .hps-table th,.hps-table td{padding:8px}.hps-shell{padding:8px 0}.hps-basis{overflow-wrap:anywhere}}
    @media(prefers-color-scheme:dark){.hps-green{background:#183b2c;color:#b1ebca}.hps-orange{background:#40331d;color:#f6d69b}.hps-red{background:#442327;color:#ffc1c4}}
  </style>`;

  class HourlyProductivitySummaryPage {
    constructor(wrapper) {
      this.page = frappe.ui.make_app_page({parent: wrapper, title: 'Hourly Productivity Summary', single_column: true});
      this.method = 'is_production.production.page.hourly_productivity_summary.hourly_productivity_summary.';
      this.requestToken = 0;
      this.initializing = true;
      this.page.main.html(`${styles}<div class="hps-shell"><div class="hps-filters"><div class="hps-site"></div>
        <div class="hps-date"></div><div class="hps-shift"></div><button class="btn btn-primary hps-refresh">Refresh</button></div>
        <div class="hps-content" aria-live="polite"></div></div>`);
      for (const [name, type, options] of [['site', 'Link', 'Location'], ['date', 'Date'], ['shift', 'Select', 'Day\nNight']]) {
        this[name] = frappe.ui.form.make_control({parent: this.page.main.find(`.hps-${name}`), render_input: true,
          df: {fieldname: name, fieldtype: type, label: name === 'date' ? 'Shift start date' : name[0].toUpperCase() + name.slice(1),
            options, reqd: 1, onchange: () => this.load()}});
      }
      this.page.main.on('click', '.hps-refresh', () => this.load());
      this.message('Select a site to view the shift.');
      this.initialize();
    }

    message(text) {
      this.page.main.find('.hps-content').html(`<div class="hps-message">${escape(text)}</div>`);
    }

    async initialize() {
      try {
        const response = await frappe.call({method: this.method + 'get_defaults'});
        await this.date.set_value(response.message.date);
        await this.shift.set_value(response.message.shift);
        this.initializing = false;
        await this.load();
      } catch (error) {
        this.initializing = false;
        this.message('Could not load defaults. Select the date and shift, then refresh.');
      }
    }

    async load() {
      if (this.initializing) return;
      const token = ++this.requestToken;
      const args = {site: this.site.get_value(), date: this.date.get_value(), shift: this.shift.get_value()};
      if (!args.site || !args.date || !args.shift) {
        this.page.main.find('.hps-shell').attr('aria-busy', 'false');
        this.message('Select a site, date and shift.');
        return;
      }
      this.page.main.find('.hps-shell').attr('aria-busy', 'true');
      this.message('Loading shift…');
      try {
        const response = await frappe.call({method: this.method + 'get_summary', args});
        if (token !== this.requestToken) return;
        this.page.main.find('.hps-content').html(renderSummary(response.message));
      } catch (error) {
        if (token !== this.requestToken) return;
        this.message('Unable to load this shift. Check your access to the site, production, MPP, assets and PBM, then refresh.');
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
  if (typeof module !== 'undefined' && module.exports) module.exports = {renderSummary, styles, HourlyProductivitySummaryPage};
})();
