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
  const colour = value => value == null ? 'neutral' : value >= 100 ? 'green' : 'red';
  const hourColour = hour => hour.target_bcm == null ? 'neutral' : hour.actual_bcm >= hour.target_bcm ? 'green' : 'red';
  const hourCategory = hour => ['green','red','blue','grey'].includes(hour.presentation_colour) ? hour.presentation_colour : hourColour(hour);
  const scheduleColours = {green:'var(--hps-hour-green)',red:'var(--hps-hour-red)',blue:'var(--hps-hour-blue)',grey:'var(--hps-hour-grey)',neutral:'var(--hps-hour-neutral)'};
  const hourBackground = hour => (hour.presentation_segments || []).map(segment =>
    `${scheduleColours[segment.category] || scheduleColours.neutral} ${Number(segment.start_percent)}% ${Number(segment.end_percent)}%`).join(',');
  const needsTextBacking = hour => {
    const categories = new Set((hour.presentation_segments || []).map(segment => segment.category));
    return ['green','red','blue'].some(category => categories.has(category)) &&
      ['grey','neutral'].some(category => categories.has(category));
  };
  // Dark status segments share white text; a split with grey/neutral needs a backing.
  const readableMixedHour = (hour, html) => {
    return needsTextBacking(hour) ? html.replace(
      /(<(?:th|td)\b[^>]*>)([\s\S]*?)(<\/(?:th|td)>)/g,
      '$1<span class="hps-mixed-text">$2</span>$3') : html;
  };
  const visibleWarning = warning =>
    warning !== 'ADT fleet uses current Asset site assignments; historical transfers are not reconstructed.' &&
    warning !== 'Unpaired MPP excavators are production/spare unknown: the original MPP stores empty production assignments in browser localStorage, not on these rows. They are not labelled as confirmed spares.' &&
    !/^MPP specifies \d+(?:\.\d+)? working hours for \d{4}-\d{2}-\d{2} (?:Day|Night), without clock times\. Only configured fatigue is shaded; the shorter shift's timing is unknown\.$/.test(warning);

  function renderMobileHour(hour) {
    const category = hourCategory(hour);
    const label = {green:'Target achieved',red:'Target not achieved',blue:'Scheduled fatigue',grey:'Confirmed non-working'}[category] || 'Target unknown';
    const note = hour.record_status !== 'recorded' ? status[hour.record_status] || hour.record_status : '';
    const metrics = [
      ['Target BCM',hour.target_bcm,0],['ADTs Available',hour.available,2],['ADTs Utilised',hour.utilised,0],
      ['Δ Available − utilised',hour.difference,2],['Excavators Available',hour.excavator_available,2],
      ['Excavators Utilised',hour.excavator_utilised,0],['Excavators PBM',hour.excavator_unavailable,2],
      ['PBM affected excavators',hour.excavator_pbm_count,0]
    ];
    return `<details class="hps-mobile-hour"><summary class="hps-mobile-summary hps-${category} ${needsTextBacking(hour) ? 'hps-mobile-mixed' : ''}"
      aria-label="${escape(`${hour.label}; ${label}; ${note ? note + '; ' : ''}Actual ${number(hour.actual_bcm,1)} BCM; achievement ${hour.achievement == null ? 'unknown' : number(hour.achievement,2) + '%'}. Expand for target and availability.`)}"
      ${hour.presentation_segments?.length ? `style="background:linear-gradient(to right,${escape(hourBackground(hour))})"` : ''}>
      <span><span class="hps-mobile-text">${escape(hour.label)}<small>${escape(label)}</small>${note ? `<small>${escape(note)}</small>` : ''}</span></span>
      <span><span class="hps-mobile-text">${number(hour.actual_bcm,1)}</span></span>
      <span><span class="hps-mobile-text">${hour.achievement == null ? '—' : `${number(hour.achievement,2)}%`}<small class="hps-mobile-toggle" aria-hidden="true">＋</small></span></span>
      </summary><dl class="hps-mobile-metrics">${metrics.map(([name,value,digits]) =>
        `<div><dt>${escape(name)}</dt><dd>${number(value,digits)}</dd></div>`).join('')}</dl></details>`;
  }

  function setupResponsiveOverview(root) {
    const media = window.matchMedia('(max-width:600px)');
    const update = () => root.querySelectorAll('.hps-responsive-fold').forEach(section => {section.open = !media.matches;});
    media.addEventListener('change',update);
    update();
    return update;
  }

  function renderFullDayCards(state) {
    const {date,shifts,selectedShift,selectedHour=0,errors={}} = state;
    const hourDate = (shift,index,hour) => {
      if (hour.start) return String(hour.start).slice(0,10);
      const day = new Date(`${date}T00:00:00Z`);
      if (!Number.isFinite(day.getTime())) return date;
      if (shift === 'Night' && index >= 6) day.setUTCDate(day.getUTCDate()+1);
      return day.toISOString().slice(0,10);
    };
    const selected = shifts[selectedShift]?.hours[selectedHour];
    return `<h3>Full day · Day and Night</h3><p class="hps-day-caption">Actual BCM · achievement %</p>
      <div class="hps-day-key"><span><i class="hps-green"></i>Achieved</span><span><i class="hps-red"></i>Below target</span>
        <span><i class="hps-blue"></i>Fatigue</span><span><i class="hps-grey"></i>Confirmed off</span></div>
      ${['Day','Night'].map(shift => {
        const data = shifts[shift];
        return `<div class="hps-day-heading"><h4>${shift} shift</h4><small>${escape(date)} · ${shift === 'Day' ? '06:00–18:00' : '18:00–06:00 next day'}</small></div>
          ${data ? `<div class="hps-day-grid">${data.hours.map((hour,index) => {
            const note = hour.record_status !== 'recorded' ? status[hour.record_status] || hour.record_status : '';
            const shortLabel = String(hour.label).replace(/^(\d{2}):00\s*[–-]\s*(\d{2}):00$/, '$1–$2');
            return `<button type="button" class="hps-day-hour hps-${hourCategory(hour)} ${needsTextBacking(hour) ? 'hps-mobile-mixed' : ''}"
              data-hps-shift="${shift}" data-hps-hour="${index}" aria-pressed="${shift === selectedShift && index === selectedHour}"
              aria-controls="${escape(state.detailId || 'hps-day-detail')}" aria-label="${escape(`${shift} · ${hourDate(shift,index,hour)} · ${hour.label}; ${note}; Actual ${number(hour.actual_bcm,1)} BCM; ${hour.achievement == null ? 'target unknown' : number(hour.achievement,2)+'%'}; view details`)}"
              ${hour.presentation_segments?.length ? `style="background:linear-gradient(to right,${escape(hourBackground(hour))})"` : ''}>
              <span class="hps-mobile-text"><small>${escape(shortLabel)}</small><strong>${number(hour.actual_bcm,1)}</strong>
                <span>${hour.achievement == null ? '—' : number(hour.achievement,1)+'%'}</span>${note ? `<em>${escape(note)}</em>` : ''}</span></button>`;
          }).join('')}</div>
          ${shift !== state.overviewShift ? `${data.summary?.missing_hours ? `<div class="hps-warning">${number(data.summary.missing_hours)} completed ${shift} hours have no production record. Displayed 0 BCM is unverified.</div>` : ''}
          ${(data.warnings || []).filter(visibleWarning).map(w => `<div class="hps-warning">${escape(w)}</div>`).join('')}` : ''}` :
          `<div class="hps-message" role="status">${errors[shift] ? `${shift} shift unavailable. Check access and planning, then retry. <button type="button" class="btn btn-default" data-hps-retry="${shift}">Retry</button>` : `Loading ${shift} shift…`}</div>`}`;
      }).join('')}
      <div class="hps-day-detail" id="${escape(state.detailId || 'hps-day-detail')}" aria-live="polite">${selected ?
        `<h4>${escape(`${selectedShift} · ${hourDate(selectedShift,selectedHour,selected)} · ${selected.label}`)}</h4>${renderMobileHour(selected).replace('<details class=', '<details open class=')}` : ''}</div>`;
  }

  function renderSummary(data) {
    const s = data.summary;
    const pie = data.pie;
    const achievedEnd = pie.target ? pie.achieved / pie.target * 100 : 0;
    // The full unmet BCM target includes the retained PBM estimate and remaining shortfall.
    const notAchieved = Math.max(pie.target - pie.achieved, 0);
    const cards = [
      ['Actual BCM', number(s.actual_bcm, 1), 'All recorded excavator output'],
      ['Shift target BCM', number(s.target_bcm), `${number(data.production_excavator_count)} MPP excavators × 2,420`],
      ['Achievement', s.achievement == null ? '—' : `${number(s.achievement, 2)}%`,
        `${number(s.completed_actual_bcm, 1)} / ${number(s.completed_target_bcm)} · completed hours`],
      ['ADTs available / utilised', `${number(s.average_available, 2)} / ${number(s.adts_utilised)}`,
        '12-hour average available / unique utilised']
    ];
    const fleet = (label, rows) => `<section class="hps-fleet"><h3>${escape(label)}</h3>
      ${rows.length ? `<div class="hps-fleet-grid">${rows.map(row => `<article class="hps-asset-card">
        <strong>${escape(row.label)}</strong><div class="hps-asset-bcm">${number(row.actual_bcm, 1)} <span>BCM</span></div>
        ${row.classification ? `<small>${escape(row.classification)}</small>` : ''}</article>`).join('')}</div>` : '<p>None listed</p>'}</section>`;
    return `<div class="hps-kpis">${cards.map(([label, value, note], index) =>
      `<article class="hps-kpi ${index === 2 ? 'hps-' + colour(s.achievement) : ''}">
        <small>${escape(label)}</small><strong>${escape(value)}</strong><small>${escape(note)}</small></article>`).join('')}</div>
      <details class="hps-targets hps-responsive-fold" open aria-label="Production targets per excavator">
        <summary class="hps-target-heading"><h3>Production targets</h3>
          <span>${data.plan_name ? `MPP: ${escape(data.plan_name)}` : 'No covering MPP'}</span></summary>
        <dl class="hps-target-values">
          <div><dt>First hour</dt><dd>55 <span>BCM</span></dd></div>
          <div><dt>Normal hour</dt><dd>220 <span>BCM</span></dd></div>
          <div><dt>Last hour</dt><dd>165 <span>BCM</span></dd></div>
        </dl>
        <p>Per production excavator · Targets stay fixed during breakdowns.</p>
      </details>
      <details class="hps-load-times hps-responsive-fold" open><summary><h3>First / last recorded load times</h3></summary><div class="hps-excavators">
        ${(data.excavators || []).map(r => `<article><strong>${escape(r.excavator)}</strong><div>
          <span>First <b>${escape(r.first_load_time || '—')}</b></span>
          <span>Last <b>${escape(r.last_load_time || '—')}</b></span></div></article>`).join('') || '<p>No load times recorded.</p>'}</div></details>
      <section class="hps-chart-section" aria-label="Completed-hour target composition">
        ${pie.target ? `<div class="hps-pie" role="img" aria-label="${escape(`Target Achieved ${number(pie.achieved, 1)} BCM; Target Not Achieved ${number(notAchieved, 1)} BCM`)}"
          style="background:conic-gradient(var(--hps-green) 0% ${achievedEnd}%, var(--hps-red) ${achievedEnd}% 100%)"></div>` :
          '<div class="hps-no-pie">No completed-hour target</div>'}
        <div class="hps-legend"><strong>Completed hours · ${number(pie.target)} BCM target</strong>
          <span><i class="hps-dot green"></i>Target Achieved <b>${number(pie.achieved, 1)} BCM</b></span>
          <span><i class="hps-dot red"></i>Target Not Achieved <b>${number(notAchieved, 1)} BCM</b></span>
        </div>
      </section>

      ${s.missing_hours ? `<div class="hps-warning">${number(s.missing_hours)} completed hours have no production record. Their displayed 0 BCM is unverified.</div>` : ''}
      ${data.warnings.filter(visibleWarning).map(w => `<div class="hps-warning">${escape(w)}</div>`).join('')}
      <div class="hps-table-wrap hps-desktop-hours"><table class="hps-table hps-hourly-table"><caption>Hourly performance</caption>
        <thead><tr><th scope="col">Hour</th><th scope="col">Actual BCM</th><th scope="col">Target BCM</th>
          <th scope="col">Achievement %</th><th scope="col">ADTs Available</th><th scope="col">ADTs Utilised</th>
          <th scope="col">Excavators Available</th><th scope="col">Excavators Utilised</th><th scope="col">Excavators PBM</th></tr></thead>
        <tbody>${data.hours.map(h => readableMixedHour(h, `<tr class="hps-${hourCategory(h)}"
          ${h.presentation_segments?.length ? `style="background:linear-gradient(to right,${escape(hourBackground(h))})"` : ''}>
          <th scope="row">${escape(h.label)}${h.record_status !== 'recorded' ? `<small>${escape(status[h.record_status] || h.record_status)}</small>` : ''}
            ${h.non_working_minutes > 0 ? `<small>${number(h.non_working_minutes, 2)} minutes confirmed non-working</small>` : ''}</th>
          <td>${number(h.actual_bcm, 1)}</td><td>${number(h.target_bcm)}</td>
          <td>${h.achievement == null ? '—' : `${number(h.achievement, 2)}%`}</td>
          <td class="hps-available">${number(h.available, 2)}<small>Δ ${number(h.difference, 2)}</small></td>
          <td class="hps-utilised">${number(h.utilised)}</td><td>${number(h.excavator_available, 2)}</td>
          <td>${number(h.excavator_utilised)}</td><td>${number(h.excavator_unavailable, 2)}
            <small>${number(h.excavator_pbm_count)} affected</small></td></tr>`)).join('')}</tbody></table></div>
      <section class="hps-mobile-hours" aria-label="Hourly performance"><h3>Hourly performance</h3>
        <div class="hps-mobile-head" aria-hidden="true"><span>Hour</span><span>Actual BCM</span><span>Achievement</span></div>
        ${data.hours.map(renderMobileHour).join('')}</section>
      <details class="hps-details"><summary>ADT availability detail</summary><div class="hps-table-wrap">
        <table class="hps-table"><thead><tr><th>ADT</th><th>Hour</th><th>Available minutes</th><th>Status</th><th>Loads</th></tr></thead>
        <tbody>${(data.details || []).map(r => `<tr><td>${escape(r.adt)}</td><td>${escape(r.hour)}</td>
          <td>${number(r.available_minutes, 1)}</td><td>${escape(r.availability_status)}</td><td>${number(r.loads)}</td></tr>`).join('')}</tbody></table></div></details>
      ${fleet('MPP truck-assigned production teams', data.production_excavators || [])}
      ${fleet('Unpaired excavators · production/spare unknown', data.unpaired_excavators || [])}`;
  }

  const styles = `<style>
    .hps-shell{--hps-green:#00803c;--hps-red:#d63c4b;--hps-blue:#245f9e;--hps-grey:#f5f5f5;--hps-hour-green:var(--hps-green);--hps-hour-red:var(--hps-red);--hps-hour-blue:var(--hps-blue);--hps-hour-grey:var(--hps-grey);--hps-hour-neutral:#fff;width:100%;max-width:none;margin:0;padding:16px 0;font-size:16px;color:var(--text-color,#243447)}
    .hps-filters{display:grid;grid-template-columns:2fr 1.3fr 1fr auto;gap:12px;align-items:end;margin-bottom:14px}
    .hps-filters .form-group{margin-bottom:0}.hps-filters button{margin-bottom:4px}
    .hps-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
    .hps-kpi{border:1px solid var(--border-color,#e1e6eb);border-top:4px solid #4799bd;border-radius:12px;padding:16px;background:var(--card-bg,#fff)}
    .hps-kpi strong{display:block;font-size:32px;margin:6px 0;line-height:1.25}.hps-kpi small{display:block;font-size:14px}
    .hps-green{background:var(--hps-green);color:#fff}.hps-red{background:var(--hps-red);color:#fff}.hps-blue{background:var(--hps-blue);color:#fff}.hps-grey{background:var(--hps-grey);color:#333}
    .hps-kpi.hps-green{border-top-color:var(--hps-green)}.hps-kpi.hps-red{border-top-color:var(--hps-red)}
    .hps-footnote{font-size:14px;line-height:1.5;margin:12px 0;color:var(--text-muted,#667582)}
    .hps-targets{border:1px solid var(--border-color,#e1e6eb);border-radius:12px;padding:14px 16px;margin:12px 0}
    .hps-target-heading{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:14px}
    .hps-responsive-fold>summary{list-style:none}.hps-responsive-fold>summary::-webkit-details-marker{display:none}
    .hps-shell .hps-targets h3{font-size:17px;margin:0}
    .hps-target-heading>span{font-size:14px;overflow-wrap:anywhere}
    .hps-target-values{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:0}
    .hps-target-values>div{padding-left:14px;border-left:2px solid var(--border-color,#e1e6eb)}
    .hps-target-values>div:first-child{padding-left:0;border-left:0}
    .hps-target-values dt{font-size:14px;font-weight:400}
    .hps-target-values dd{font-size:32px;font-weight:600;margin:2px 0 0}
    .hps-target-values dd span{font-size:14px;font-weight:400}
    .hps-targets p{font-size:14px;line-height:1.5;margin:13px 0 0;padding-top:10px;border-top:1px solid var(--border-color,#e1e6eb)}
    .hps-chart-section{display:flex;align-items:center;gap:24px;padding:16px;border:1px solid var(--border-color,#e1e6eb);border-radius:12px;margin-top:16px}
    .hps-pie{width:160px;height:160px;border-radius:50%;flex-shrink:0}.hps-legend{display:flex;flex-direction:column;gap:10px;font-size:15px}
    .hps-legend span{display:flex;align-items:center;gap:7px;flex-wrap:wrap}.hps-dot{width:10px;height:10px;border-radius:50%;display:inline-block;flex-shrink:0}
    .hps-dot.green{background:var(--hps-green)}.hps-dot.red{background:var(--hps-red)}
    .hps-warning{background:#fff3dd;color:#754900;padding:10px 12px;border-radius:7px;font-size:14px;margin:10px 0}
    .hps-explanation,.hps-details{font-size:14px;margin:14px 0}.hps-explanation p{margin:8px 0}.hps-shell summary{cursor:pointer;font-weight:600}
    .hps-table-wrap{overflow:auto;border:1px solid var(--border-color,#e1e6eb);border-radius:10px;margin-top:12px}
    .hps-table{width:100%;border-collapse:collapse;font-size:16px;white-space:nowrap}.hps-table caption{caption-side:top;padding:14px;font-weight:600;color:inherit}
    .hps-table th,.hps-table td{text-align:right;padding:12px 14px;border-bottom:1px solid var(--border-color,#dce4e9)}
    .hps-table thead th{white-space:normal}
    .hps-table th:first-child,.hps-table td:first-child{text-align:left}.hps-table thead{background:var(--subtle-fg,#edf1f5);color:var(--text-color,#243447)}
    /* All achievement indicators share one palette, including partial-hour segments. */
    .hps-hourly-table{--hps-hour-green:var(--hps-green);--hps-hour-red:var(--hps-red);--hps-hour-blue:var(--hps-blue);--hps-hour-grey:var(--hps-grey);--hps-hour-neutral:#fff}
    .hps-hourly-table tbody tr.hps-green{background-color:var(--hps-hour-green)}
    .hps-hourly-table tbody tr.hps-red{background-color:var(--hps-hour-red)}
    .hps-hourly-table tbody tr.hps-blue{background-color:var(--hps-hour-blue)}
    .hps-hourly-table tbody tr.hps-grey{background-color:var(--hps-hour-grey)}
    .hps-hourly-table tbody tr.hps-neutral{background-color:var(--hps-hour-neutral)}
    .hps-hourly-table tbody tr:is(.hps-green,.hps-red,.hps-blue,.hps-grey,.hps-neutral){color:#000}
    .hps-hourly-table tbody tr:is(.hps-green,.hps-red,.hps-blue){color:#fff}
    .hps-hourly-table .hps-mixed-text{display:inline-block;background:#fff;color:#172a1a;padding:2px 4px;border-radius:3px}
    .hps-hourly-table tbody tr:is(.hps-green,.hps-red,.hps-blue,.hps-grey,.hps-neutral)>:is(th,td){background:transparent;color:inherit}
    .hps-hourly-table tbody tr:is(.hps-green,.hps-red,.hps-blue,.hps-grey,.hps-neutral) small{color:inherit}
    .hps-table small{display:block;font-size:12px;font-weight:400}.hps-available{font-weight:600}.hps-utilised{font-weight:600}
    .hps-excavators{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px}.hps-excavators article{border:1px solid var(--border-color,#e1e6eb);border-radius:10px;padding:10px}
    .hps-excavators article>div{display:flex;justify-content:space-between;margin-top:8px;font-size:14px}.hps-excavators b{display:block;color:inherit}
    .hps-excavators span:last-child b{color:#b27615}.hps-shell h3{font-size:18px;margin:18px 0 12px}
    .hps-fleet{font-size:14px;margin:20px 0}
    .hps-fleet-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,260px),1fr));gap:12px}
    .hps-asset-card{padding:16px;border:1px solid var(--border-color,#e1e6eb);border-radius:12px;background:var(--card-bg,#fff);min-width:0}
    .hps-asset-card>strong{display:block;font-size:17px;overflow-wrap:anywhere}
    .hps-asset-bcm{font-size:28px;font-weight:600;margin:8px 0 4px;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
    .hps-asset-bcm>span{font-size:14px;font-weight:400}.hps-asset-card>small{display:block;font-size:12px;overflow-wrap:anywhere}
    .hps-message{padding:20px;font-size:14px}.hps-shell[aria-busy=true]{opacity:.7}
    .hps-mobile-hours{display:none}
    .hps-day-caption{font-size:12px;margin:4px 0;color:var(--text-muted,#667582)}
    .hps-day-key{display:flex;flex-wrap:wrap;gap:6px 10px;font-size:11px;margin:10px 0}
    .hps-day-key span{display:flex;align-items:center;gap:4px}.hps-day-key i{width:8px;height:8px;border-radius:50%;display:inline-block}
    .hps-day-heading{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:4px;margin:14px 0 8px}
    .hps-day-heading h4{font-size:14px;margin:0}.hps-day-heading small{font-size:11px}
    .hps-day-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:5px}
    .hps-day-hour{min-height:80px;min-width:0;border:0;border-radius:7px;padding:7px 2px;font:inherit;text-align:center}
    .hps-day-hour small{display:block;font-size:11px;overflow-wrap:anywhere}.hps-day-hour strong{display:block;font-size:15px;margin:5px 0}
    .hps-day-hour span span{font-size:11px}.hps-day-hour em{display:block;font-size:11px;font-style:normal;margin-top:4px;overflow-wrap:anywhere}
    .hps-day-hour[aria-pressed=true]{outline:2px solid var(--text-color,#243447);outline-offset:1px}
    .hps-day-hour.hps-neutral{background:#fff;color:#243447}
    .hps-day-detail{margin-top:14px}.hps-day-detail h4{font-size:14px;margin:8px 0}
    @media(max-width:600px){.hps-filters{grid-template-columns:1fr 1fr;gap:8px}.hps-filters>div:first-child{grid-column:1/-1}
      .hps-filters button{grid-column:1/-1}.hps-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.hps-kpi{padding:10px}.hps-kpi strong{font-size:25px}.hps-kpi small{font-size:12px}
      .hps-chart-section{gap:12px;padding:10px}.hps-pie{width:90px;height:90px}.hps-legend{font-size:13px}.hps-table{font-size:14px}
      .hps-table th,.hps-table td{padding:8px}.hps-shell{padding:8px 0}
      .hps-filters :is(input,select,button){min-height:44px;font-size:16px}
      .hps-desktop-hours{display:none}.hps-mobile-hours{display:block;margin-top:16px}
      .hps-mobile-head,.hps-mobile-summary{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr) minmax(0,1fr);gap:6px;text-align:right;align-items:center}
      .hps-mobile-head{padding:8px 10px;font-size:12px}.hps-mobile-head>span:first-child,.hps-mobile-summary>span:first-child{text-align:left}
      .hps-mobile-hour{border:1px solid var(--border-color,#e1e6eb);border-radius:10px;margin:8px 0;overflow:hidden}
      .hps-mobile-summary{list-style:none;padding:12px 10px;min-height:64px;font-size:15px;font-variant-numeric:tabular-nums}
      .hps-mobile-summary::-webkit-details-marker{display:none}.hps-mobile-summary small{display:block;font-size:11px;font-weight:400;margin-top:3px;overflow-wrap:anywhere}
      .hps-mobile-summary.hps-neutral{background:#fff;color:#000}
      .hps-mobile-mixed .hps-mobile-text{display:inline-block;background:#fff;color:#172a1a;padding:2px 4px;border-radius:3px}
      .hps-mobile-hour[open] .hps-mobile-toggle{font-size:0}.hps-mobile-hour[open] .hps-mobile-toggle::after{content:'−';font-size:14px}
      .hps-mobile-metrics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;padding:14px;margin:0;background:var(--card-bg,#fff);color:var(--text-color,#243447)}
      .hps-mobile-metrics dt{font-size:12px;font-weight:400;overflow-wrap:anywhere}.hps-mobile-metrics dd{font-size:18px;margin:3px 0 0;font-weight:600}
      .hps-responsive-fold{border:1px solid var(--border-color,#e1e6eb);border-radius:12px;margin:10px 0;padding:0}
      .hps-responsive-fold>summary{position:relative;min-height:48px;padding:12px 34px 12px 12px;margin:0}
      .hps-responsive-fold>summary::after{content:'＋';position:absolute;right:12px;top:12px}.hps-responsive-fold[open]>summary::after{content:'−'}
      .hps-mobile-mixed .hps-mobile-text{display:inline-block;background:#fff;color:#172a1a;padding:2px 4px;border-radius:3px}
      .hps-shell .hps-load-times h3{font-size:15px;margin:0}.hps-target-values,.hps-targets p{margin:12px}.hps-excavators{padding:0 12px 12px;grid-template-columns:1fr}}
    @media(max-width:360px){.hps-day-grid{grid-template-columns:repeat(4,minmax(0,1fr))}}
    @media(max-width:420px){.hps-target-values{gap:6px}.hps-target-values>div{padding-left:8px}
      .hps-target-values dd{font-size:23px}.hps-target-values dd span{display:block}}
  </style>`;

  class HourlyProductivitySummaryPage {
    constructor(wrapper, page = null) {
      this.page = page || frappe.ui.make_app_page({parent: wrapper, title: 'Hourly Productivity Summary', single_column: true});
      this.method = 'is_production.production.page.hourly_productivity_summary.hourly_productivity_summary.';
      this.requestToken = 0;
      this.initializing = true;
      this.page.main.html(`${styles}<div class="hps-shell"><div class="hps-filters"><div class="hps-site"></div>
        <div class="hps-date"></div><div class="hps-shift"></div><button class="btn btn-primary hps-refresh">Refresh</button></div>
        <div class="hps-content" aria-live="polite"></div></div>`);
      this.updateOverview = setupResponsiveOverview(this.page.main[0]);
      for (const [name, type, options] of [['site', 'Link', 'Location'], ['date', 'Date'], ['shift', 'Select', 'Day\nNight']]) {
        this[name] = frappe.ui.form.make_control({parent: this.page.main.find(`.hps-${name}`), render_input: true,
          df: {fieldname: name, fieldtype: type, label: name === 'date' ? 'Shift start date' : name[0].toUpperCase() + name.slice(1),
            options, reqd: 1, onchange: () => this.load()}});
      }
      this.page.main.on('click', '.hps-refresh', () => this.load());
      this.phoneMedia = window.matchMedia('(max-width:600px)');
      this.phoneMedia.addEventListener('change',() => {if (this.phoneMedia.matches) this.loadFullDay();});
      this.page.main.on('click','.hps-day-hour',event => {
        if (!this.fullDay || this.fullDay.token !== this.requestToken) return;
        this.fullDay.selectedShift = event.currentTarget.dataset.hpsShift;
        this.fullDay.selectedHour = Number(event.currentTarget.dataset.hpsHour);
        // Update only the detail and pressed state, retaining keyboard focus on its card.
        const root = this.page.main[0];
        for (const button of root.querySelectorAll('.hps-day-hour')) button.setAttribute('aria-pressed',String(button === event.currentTarget));
        const fragment = document.createElement('div');
        fragment.innerHTML = renderFullDayCards(this.fullDay);
        root.querySelector('.hps-day-detail').innerHTML = fragment.querySelector('.hps-day-detail').innerHTML;
      });
      this.page.main.on('click','[data-hps-retry]',() => this.loadFullDay(true));
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
      this.fullDay = null;
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
        this.updateOverview();
        this.fullDay = {token,date:args.date,args,shifts:{[args.shift]:response.message},overviewShift:args.shift,
          selectedShift:args.shift,selectedHour:0,errors:{},detailId:`hps-day-detail-${++HourlyProductivitySummaryPage.detailSequence}`};
        await this.loadFullDay();
      } catch (error) {
        if (token !== this.requestToken) return;
        this.message('Unable to load this shift. Check your access to the site, production, MPP, assets and PBM, then refresh.');
      } finally {
        if (token === this.requestToken) this.page.main.find('.hps-shell').attr('aria-busy', 'false');
      }
    }

    async loadFullDay(retry = false) {
      const state = this.fullDay;
      if (!this.phoneMedia.matches || !state || state.token !== this.requestToken) return;
      const render = () => {
        if (state === this.fullDay && state.token === this.requestToken)
          this.page.main.find('.hps-mobile-hours').html(renderFullDayCards(state));
      };
      render();
      const shift = state.overviewShift === 'Day' ? 'Night' : 'Day';
      if (state.shifts[shift] || state.loading || (state.errors[shift] && !retry)) return;
      state.loading = true;
      delete state.errors[shift];
      render();
      try {
        const response = await frappe.call({method:this.method+'get_summary',args:{...state.args,shift}});
        if (state !== this.fullDay || state.token !== this.requestToken) return;
        state.shifts[shift] = response.message;
      } catch (error) {
        if (state !== this.fullDay || state.token !== this.requestToken) return;
        state.errors[shift] = true;
      } finally {state.loading = false;render();}
    }
  }

  HourlyProductivitySummaryPage.detailSequence = 0;

  if (typeof frappe !== 'undefined') frappe.hourlyProductivitySummaryView = {renderSummary,styles,HourlyProductivitySummaryPage,setupResponsiveOverview};
  if (typeof module !== 'undefined' && module.exports) module.exports = {renderSummary, renderFullDayCards, styles, HourlyProductivitySummaryPage, setupResponsiveOverview};
})();
