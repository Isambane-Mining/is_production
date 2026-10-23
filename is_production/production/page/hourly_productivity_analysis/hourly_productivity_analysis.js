/* Compact operational-day matrix; no writes to planning or production. */
(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const number = (value, digits = 0) => value == null ? 'unknown' :
    Number(value).toLocaleString(undefined, {maximumFractionDigits: digits});
  const categories = [
    ['green', '#00b050', 'Target achieved · ≥100%'],
    ['amber', '#ff9f1c', '85–99.9% achieved'],
    ['red', '#ffe066', 'Below 85%'],
    ['bright_red', '#d63c4b', 'Zero production while machines were available'],
    ['grey', '#f5f5f5', 'Fatigue / Non-Working Hours']
  ];

  const colours = Object.fromEntries(categories.map(([key, colour]) => [key, colour]));
  const categoryOf = cell => categories.some(([key]) => key === cell.category) ? cell.category : 'red';
  const cellBackground = cell => (cell.segments || []).length ? `background:linear-gradient(to right,${cell.segments.map(segment =>
    `${colours[segment.category] || colours.red} ${Number(segment.start_percent)}% ${Number(segment.end_percent)}%`).join(',')})` : '';
  const needsTextBacking = cell => (cell.segments || []).some(segment => segment.category === 'bright_red') &&
    cell.segments.some(segment => segment.category !== 'bright_red');
  const cellValue = cell => `${number(cell.actual_bcm,1)}${!cell.has_record ? '<sup aria-label="Missing record">*</sup>' : ''}${!cell.completed && cell.record_status !== 'missing' ? '<sup aria-label="Unfinished hour">†</sup>' : ''}`;

  const hourHeading = (row, cell) => {
    const date = String(cell.start || row.date).slice(0, 10);
    const range = cell.start && cell.end ? `${cell.start.slice(11, 16)}–${cell.end.slice(11, 16)}` : cell.label;
    return `${date} · ${range}`;
  };

  function renderHourDetails(row, cell, index) {
    const value = (amount, suffix = '') => amount == null ? 'Not available' : `${number(amount, 2)}${suffix}`;
    const status = {recorded:'Production recorded', missing:'No production record', pending:'Upcoming hour',
      in_progress:'Hour in progress — production recorded', in_progress_missing:'Hour in progress — no record yet'};
    const fields = [
      ['Actual Production', value(cell.actual_bcm, ' BCM')],
      ['Production Target', value(cell.target_bcm, ' BCM')],
      ['Target Achieved', value(cell.achievement, '%')],
      ['Excavators Available', value(cell.excavator_available)],
      ['Recording Status', status[cell.record_status] || (cell.has_record ? 'Production recorded' : 'No production record')]
    ];
    if (cell.non_working_minutes > 0) fields.push(['Fatigue / Non-working', value(cell.non_working_minutes, ' minutes')]);
    const loads = (row.load_times?.[index < 12 ? 'Day' : 'Night'] || [])
      .filter(exc => exc.first_load_time || exc.last_load_time);
    return `<h3>${escape(hourHeading(row, cell))}</h3><dl>${fields.map(([label, text]) =>
      `<dt>${escape(label)}</dt><dd>${escape(text)}</dd>`).join('')}</dl>
      ${loads.length ? `<h4>Shift Load Times</h4>${loads.map(exc => `<div class="hpa-load-times"><strong>${escape(exc.excavator)}</strong>
        ${exc.first_load_time ? `<span>First load <b>${escape(exc.first_load_time)}</b></span>` : ''}
        ${exc.last_load_time ? `<span>Last load <b>${escape(exc.last_load_time)}</b></span>` : ''}</div>`).join('')}` : ''}`;
  }

  function renderMobileAnalysis(data, cardId = 'hpa-hour-card', selectedRow = 0, selectedHour = 0) {
    if (!data.rows?.length) return '<div class="hpa-message">No hours in this date range.</div>';
    const rowIndex = Number.isInteger(selectedRow) && data.rows[selectedRow] ? selectedRow : 0;
    const row = data.rows[rowIndex];
    const hourIndex = Number.isInteger(selectedHour) && row.cells[selectedHour] ? selectedHour : 0;
    const detailId = `${cardId}-mobile-detail`;
    const keyLabels = ['≥100%','85–99.9%','Below 85%','0 · machines available','Fatigue / non-working'];
    return `<section class="hpa-mobile" aria-label="Hourly production by selected operational date">
      <div class="hpa-mobile-key" aria-label="Hour colour key">${categories.map(([key,colour],index) =>
        `<span class="hpa-key-item"><i style="background:${colour}"></i>${escape(keyLabels[index])}</span>`).join('')}</div>
      <label class="hpa-mobile-date-label">Operational date<select class="hpa-mobile-date">${data.rows.map((item,index) =>
        `<option value="${index}"${index === rowIndex ? ' selected' : ''}>${escape(item.date)} · ${escape(item.day)}</option>`).join('')}</select></label>
      ${[['Day',0,12,'06:00–18:00'],['Night',12,24,'18:00–06:00 next day']].map(([shift,start,end,range]) =>
        `<h3>${shift} shift · ${range}</h3><div class="hpa-mobile-grid" aria-label="${shift} shift hours">${row.cells.slice(start,end).map((cell,offset) => {
          const index = start + offset;
          const label = cell.start && cell.end ? `${cell.start.slice(11,13)}–${cell.end.slice(11,13)}` : cell.label.replaceAll(':00','');
          return `<button type="button" class="hpa-mobile-cell hpa-${categoryOf(cell)} ${needsTextBacking(cell) ? 'hpa-mobile-mixed' : ''}"
            style="${escape(cellBackground(cell))}" data-hpa-row="${rowIndex}" data-hpa-hour="${index}"
            aria-pressed="${index === hourIndex}" aria-controls="${escape(detailId)}"
            aria-label="${escape(hourHeading(row,cell))}; ${number(cell.actual_bcm,1)} BCM${!cell.has_record ? '; no production record' : ''}. View hourly details.">
            <span class="hpa-mobile-text"><small>${escape(label)}</small><strong>${cellValue(cell)}</strong></span></button>`;
        }).join('')}</div>`).join('')}
      <p class="hpa-mobile-note">* No production record · † Unfinished hour</p>
      <div class="hpa-mobile-detail" id="${escape(detailId)}" aria-live="polite">${row.cells[hourIndex] ? renderHourDetails(row,row.cells[hourIndex],hourIndex) : 'No hour selected.'}</div>
    </section>`;
  }

  function renderAnalysis(data, cardId = 'hpa-hour-card') {
    if (data.planning_error || data.configuration_error) {
      const error = data.planning_error || data.configuration_error;
      const messages = error.issues ? error.issues.map(issue => issue.message) : [error.message];
      return `<div class="hpa-message" role="alert"><strong>${escape(error.title)}</strong>
        ${messages.map(message => `<p>${escape(message)}</p>`).join('')}</div>`;
    }
    let lastMonth = '';
    let gradientStart = 0;
    const gradient = categories.map(([key, colour]) => {
      const start = gradientStart;
      gradientStart += Number(data.pie.categories[key].percent);
      return `${colour} ${start}% ${gradientStart}%`;
    }).join(',');
    const aria = categories.map(([key, , label]) => `${label}: ${number(data.pie.categories[key].hours, 2)} hours, ${number(data.pie.categories[key].percent, 1)}%`).join('; ');
    return `<div class="hpa-scroll" tabindex="0" role="region" aria-label="Hourly BCM table; scroll horizontally">
      <table class="hpa-table"><caption>Hourly recorded excavator BCM · ${escape(data.site)}</caption>
        <thead><tr><th rowspan="2" class="hpa-sticky-date">Date</th><th rowspan="2" class="hpa-sticky-day">Day</th>
          <th rowspan="2" class="hpa-sticky-month">Month</th><th colspan="12" class="hpa-shift-day"><span>DAY SHIFT · 06:00–18:00</span></th>
          <th colspan="12" class="hpa-shift-night"><span>NIGHT SHIFT · 18:00–06:00</span></th></tr>
          <tr>${data.hours.map((hour, i) => `<th class="hpa-hour ${i === 12 ? 'hpa-night-start' : ''}" title="${escape(hour)}">
            ${escape(hour.replaceAll(':00', ''))}</th>`).join('')}</tr></thead>
        <tbody>${data.rows.map((row, rowIndex) => {
          const divider = row.month_key !== lastMonth ? `<tr class="hpa-month-divider"><th colspan="27"><span>${escape(row.month)}</span></th></tr>` : '';
          lastMonth = row.month_key;
          return `${divider}<tr><th scope="row" class="hpa-sticky-date" title="MPP: ${escape(row.plan_name)}">${escape(row.date)}</th>
            <td class="hpa-sticky-day">${escape(row.day)}</td><td class="hpa-sticky-month" title="${escape(row.month)}">${escape(row.month.replace(/^(\w{3})\w+/, '$1'))}</td>
            ${row.cells.map((cell, index) => {
              const category = categoryOf(cell);
              const segments = cell.segments || [];
              const background = cellBackground(cell);
              return `<td class="hpa-cell hpa-${category} ${index === 12 ? 'hpa-night-start' : ''} ${segments.length > 1 ? 'hpa-partial' : ''}" style="${escape(background)}" tabindex="0"
                data-hpa-row="${rowIndex}" data-hpa-hour="${index}" aria-haspopup="dialog" aria-expanded="false" aria-controls="${escape(cardId)}"
                aria-label="${escape(hourHeading(row, cell))}; ${number(cell.actual_bcm, 1)} BCM. View hourly production details.">${needsTextBacking(cell) ? `<span class="hpa-mixed-text">${cellValue(cell)}</span>` : cellValue(cell)}</td>`;
            }).join('')}</tr>`;
        }).join('')}</tbody></table></div>
      ${renderMobileAnalysis(data,cardId)}
      <section class="hpa-chart" aria-label="Proportion of hourly performance categories"><div class="hpa-pie" role="img"
        aria-label="${escape(aria)}" style="background:conic-gradient(${gradient})"></div>
        <div class="hpa-legend"><strong>Hourly performance · ${number(data.pie.total_hours)} hours</strong>
          ${categories.map(([key, colour, label]) => `<div><i style="background:${colour}"></i><span>${escape(label)}</span>
            <b>${number(data.pie.categories[key].percent, 1)}%</b><small>${number(data.pie.categories[key].hours, 2)} hours</small></div>`).join('')}
        </div></section>`;
  }

  const styles = `<style>
    .hpa-shell{--date-width:94px;--day-width:42px;--month-width:100px;max-width:100%;padding:12px 0;color:var(--text-color,#243447)}
    .hpa-filters{display:grid;grid-template-columns:2fr 1fr 1fr auto;gap:12px;align-items:end;margin-bottom:12px}
    .hpa-filters .form-group{margin-bottom:0}.hpa-filters button{margin-bottom:4px}.hpa-note{font-size:12px;line-height:1.5;margin:10px 0;color:var(--text-muted,#667582)}
    .hpa-warning{font-size:12px;background:#fff2cf;color:#714900;padding:8px 10px;border-radius:6px;margin:6px 0}
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
    .hpa-table .hpa-night-start{border-left:3px solid #354f75}.hpa-green{background:${colours.green}}.hpa-amber{background:${colours.amber}}
    .hpa-red{background:${colours.red}}.hpa-bright_red{background:${colours.bright_red};color:#fff}.hpa-partial{color:#172a1a}.hpa-grey{background:${colours.grey};color:#333}
    .hpa-mixed-text,.hpa-mobile-mixed .hpa-mobile-text{display:inline-block;background:#fff;color:#172a1a;padding:2px 4px;border-radius:3px}
    .hpa-month-divider th{text-align:left;background:#d9e5ef;color:#203f58;height:27px}.hpa-month-divider span{position:sticky;left:8px}
    .hpa-chart{display:flex;align-items:center;gap:24px;margin-top:16px;padding:12px;border:1px solid var(--border-color,#e1e6eb);border-radius:8px}
    .hpa-pie{width:150px;height:150px;border-radius:50%;flex-shrink:0}.hpa-legend{font-size:12px;display:grid;gap:8px}
    .hpa-legend>div{display:grid;grid-template-columns:12px minmax(0,1fr) auto auto;gap:8px;align-items:center}.hpa-legend i{width:10px;height:10px;border:1px solid #71808c}
    .hpa-cell[tabindex]{cursor:pointer}.hpa-cell:focus-visible{outline:3px solid var(--primary,#2490ef);outline-offset:-3px}
    .hpa-hour-card{position:fixed;z-index:1060;width:340px;max-width:calc(100vw - 24px);max-height:calc(100dvh - 24px);
      box-sizing:border-box;overflow:auto;padding:16px;border:1px solid var(--border-color,#d1d8dd);border-radius:8px;
      background:var(--popover-bg,var(--card-bg,#fff));color:var(--text-color,#243447);box-shadow:0 6px 24px #0003;font-size:13px;line-height:1.5}
    .hpa-hour-card[hidden]{display:none}.hpa-hour-card h3{font-size:14px;font-weight:600;margin:0 24px 12px 0}
    .hpa-hour-card h4{font-size:12px;font-weight:600;margin:12px 0 6px}.hpa-hour-card dl{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:8px 12px;margin:0}
    .hpa-hour-card dt{font-weight:400;color:var(--text-muted,#667582)}.hpa-hour-card dd{margin:0;font-weight:600;overflow-wrap:anywhere}
    .hpa-card-close{position:absolute;right:5px;top:5px;width:32px;height:32px;border:0;border-radius:4px;background:transparent;color:inherit;font-size:20px;cursor:pointer}
    .hpa-load-times{display:flex;flex-wrap:wrap;gap:4px 12px;padding:6px 0;border-top:1px solid var(--border-color,#e1e6eb)}
    .hpa-load-times strong{width:100%;overflow-wrap:anywhere}.hpa-load-times b{margin-left:4px;font-weight:500}
    .hpa-message{padding:20px;font-size:14px}.hpa-shell[aria-busy=true]{opacity:.7}
    .hpa-mobile{display:none}
    @media(max-width:600px){.hpa-shell{--date-width:82px;--day-width:38px;--month-width:90px}.hpa-filters{grid-template-columns:1fr 1fr;gap:8px}
      .hpa-shift-day span,.hpa-shift-night span{position:sticky;left:calc(var(--date-width) + var(--day-width) + var(--month-width) + 8px);display:block;width:max-content;text-align:left}
      .hpa-filters>div:first-child,.hpa-filters button{grid-column:1/-1}.hpa-chart{gap:12px;flex-wrap:wrap}.hpa-pie{width:110px;height:110px}
      .hpa-legend{font-size:11px;width:100%}.hpa-table{min-width:1746px;font-size:10px}.hpa-table th,.hpa-table td{padding:3px}
      .hpa-filters :is(input,select,button){font-size:16px;min-height:44px}
      .hpa-content{display:flex;flex-direction:column}.hpa-scroll{display:none}.hpa-mobile{display:block}
      .hpa-chart{order:-1;margin:0 0 14px;flex-wrap:nowrap;padding:12px;gap:12px}.hpa-pie{width:80px;height:80px}
      .hpa-legend{width:auto;min-width:0;font-size:13px}.hpa-legend>div{display:none}
      .hpa-mobile-key{display:flex;flex-wrap:wrap;gap:7px 12px;margin-bottom:16px;font-size:11px}
      .hpa-key-item{display:inline-flex;align-items:center;gap:5px}.hpa-key-item i{width:9px;height:9px;border-radius:50%;flex-shrink:0}
      .hpa-mobile-date-label{display:block;font-size:13px}.hpa-mobile-date{display:block;width:100%;min-height:48px;font:inherit;font-size:16px;margin-top:6px;padding:10px;border:1px solid var(--border-color,#d1d8dd);border-radius:8px;background:var(--card-bg,#fff);color:var(--text-color,#243447)}
      .hpa-mobile h3{font-size:16px;margin:18px 0 10px}.hpa-mobile-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}
      .hpa-mobile-cell{box-sizing:border-box;min-width:0;min-height:60px;border:0;border-radius:8px;padding:7px 3px;font:inherit;color:#172a1a;cursor:pointer;font-variant-numeric:tabular-nums}
      .hpa-mobile-cell.hpa-bright_red{color:#fff}.hpa-mobile-cell.hpa-grey{color:#333}
      .hpa-mobile-cell[aria-pressed=true]{outline:3px solid var(--text-color,#243447);outline-offset:2px}
      .hpa-mobile-cell small{display:block;font-size:11px}.hpa-mobile-cell strong{display:block;font-size:18px;font-weight:600;overflow-wrap:anywhere}.hpa-mobile-cell sup{font-size:10px}
      .hpa-mobile-note{font-size:11px;margin:12px 0}.hpa-mobile-detail{padding:14px;border:1px solid var(--border-color,#d1d8dd);border-radius:10px;margin:14px 0;background:var(--card-bg,#fff)}
      .hpa-mobile-detail h3{font-size:14px;margin:0 0 12px}.hpa-mobile-detail h4{font-size:13px;margin:14px 0 8px}
      .hpa-mobile-detail dl{display:grid;grid-template-columns:1fr 1fr;gap:8px 12px;font-size:13px;margin:0}.hpa-mobile-detail dt{font-weight:400}.hpa-mobile-detail dd{margin:0;font-weight:600;overflow-wrap:anywhere}}
    @media(max-width:350px){.hpa-mobile-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
  </style>`;

  let cardSequence = 0;
  class HourlyProductivityAnalysisPage {
    constructor(wrapper, page = null) {
      this.page = page || frappe.ui.make_app_page({parent:wrapper,title:'Hourly Productivity Analysis',single_column:true});
      this.method = 'is_production.production.page.hourly_productivity_analysis.hourly_productivity_analysis.';
      this.cardId = `hpa-hour-card-${++cardSequence}`;
      this.requestToken = 0;
      this.initializing = true;
      this.page.main.html(`${styles}<div class="hpa-shell"><div class="hpa-filters"><div class="hpa-site"></div>
        <div class="hpa-from_date"></div><div class="hpa-to_date"></div><button class="btn btn-primary hpa-refresh">Refresh</button></div>
        <div class="hpa-content" aria-live="polite"></div>
        <div id="${this.cardId}" class="hpa-hour-card" role="dialog" aria-label="Hourly production details" hidden>
          <button type="button" class="hpa-card-close" aria-label="Close hourly details">×</button><div class="hpa-card-body"></div>
        </div></div>`);
      this.bindHourCard();
      for (const [name,label,type,options] of [['site','Site','Link','Location'],['from_date','From Date','Date'],['to_date','To Date','Date']]) {
        this[name] = frappe.ui.form.make_control({parent:this.page.main.find('.hpa-' + name),render_input:true,
          df:{fieldname:name,label,fieldtype:type,options,reqd:1,onchange:()=>this.load()}});
      }
      this.page.main.on('click','.hpa-refresh',()=>this.load());
      this.message('Select a site and date range.');
      this.initialize();
    }
    bindHourCard() {
      const main = this.page.main;
      this.card = main.find('.hpa-hour-card')[0];
      main.on('change','.hpa-mobile-date',event => {
        this.hideHourCard();
        const index = Number(event.currentTarget.value);
        if (!this.analysisData?.rows[index]) return;
        main.find('.hpa-mobile').replaceWith(renderMobileAnalysis(this.analysisData,this.cardId,index));
      });
      main.on('click','.hpa-mobile-cell',event => {
        const button = event.currentTarget;
        const row = this.analysisData?.rows[Number(button.dataset.hpaRow)];
        const index = Number(button.dataset.hpaHour);
        if (!row?.cells[index]) return;
        main.find('.hpa-mobile-cell').attr('aria-pressed','false');
        button.setAttribute('aria-pressed','true');
        main.find('.hpa-mobile-detail').html(renderHourDetails(row,row.cells[index],index));
      });
      main.on('pointerover focusin', '.hpa-cell', event => {
        if (event.type === 'pointerover' && (this.hoverDismissed || event.originalEvent?.pointerType === 'touch')) return;
        if (this.cardPinned && event.type === 'pointerover') return;
        this.showHourCard(event.currentTarget);
      });
      main.on('pointerout focusout', '.hpa-cell, .hpa-hour-card', event => {
        const next = event.relatedTarget;
        if (this.card.contains(next) || this.activeCell?.contains(next)) return;
        if (!this.cardPinned || event.type === 'focusout') {
          clearTimeout(this.cardTimer);
          this.cardTimer = setTimeout(() => this.hideHourCard(), 180);
        }
      });
      main.on('pointerover focusin', '.hpa-hour-card', () => clearTimeout(this.cardTimer));
      main.on('click', '.hpa-cell', event => {
        if (this.activeCell === event.currentTarget && this.cardPinned) this.hideHourCard();
        else {
          this.showHourCard(event.currentTarget);
          this.cardPinned = true;
          this.card.querySelector('.hpa-card-close').focus({preventScroll:true});
        }
      });
      main.on('keydown', '.hpa-cell', event => {
        if (event.key === 'Enter' || event.key === ' ') {event.preventDefault();event.currentTarget.click();}
      });
      document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && this.activeCell) {
          event.preventDefault();
          this.closeHourCard();
        }
      });
      main.on('click', '.hpa-card-close', () => this.closeHourCard());
      document.addEventListener('pointermove', event => {
        if (this.hoverDismissed && event.pointerType !== 'touch' && (event.movementX || event.movementY)) {
          this.hoverDismissed = false;
          const cell = event.target.closest?.('.hpa-cell');
          if (cell && main[0].contains(cell)) this.showHourCard(cell);
        }
      });
      document.addEventListener('pointerdown', event => {
        if (!this.card.contains(event.target) && !this.activeCell?.contains(event.target)) this.hideHourCard();
      });
      document.addEventListener('scroll', event => {
        if (!this.card.contains(event.target)) {this.hoverDismissed = true;this.hideHourCard();}
      }, true);
      window.addEventListener('resize', () => {this.hoverDismissed = true;this.hideHourCard();});
    }
    showHourCard(cell) {
      clearTimeout(this.cardTimer);
      const row = this.analysisData?.rows[Number(cell.dataset.hpaRow)];
      const index = Number(cell.dataset.hpaHour);
      if (!row?.cells[index]) return;
      if (this.activeCell !== cell) this.hideHourCard();
      this.activeCell = cell;
      cell.setAttribute('aria-expanded', 'true');
      this.card.querySelector('.hpa-card-body').innerHTML = renderHourDetails(row, row.cells[index], index);
      this.card.hidden = false;
      const anchor = cell.getBoundingClientRect(), bounds = this.card.getBoundingClientRect();
      const left = Math.max(12, Math.min(anchor.left, window.innerWidth - bounds.width - 12));
      let top = anchor.bottom + 8;
      if (top + bounds.height > window.innerHeight - 12) top = anchor.top - bounds.height - 8;
      this.card.style.left = `${left}px`;
      this.card.style.top = `${Math.max(12, top)}px`;
    }
    hideHourCard() {
      clearTimeout(this.cardTimer);
      this.activeCell?.setAttribute('aria-expanded', 'false');
      this.activeCell = null;
      this.cardPinned = false;
      if (this.card) this.card.hidden = true;
    }
    closeHourCard() {
      this.hoverDismissed = true;
      const cell = this.activeCell;
      cell?.focus({preventScroll:true});
      this.hideHourCard();
    }
    message(text) {this.hideHourCard();this.analysisData = null;this.page.main.find('.hpa-content').html(`<div class="hpa-message">${escape(text)}</div>`);}
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
        this.analysisData = response.message;
        this.page.main.find('.hpa-content').html(renderAnalysis(response.message, this.cardId));
      } catch (error) {
        if (token !== this.requestToken) return;
        this.message('Unable to load. Every date needs a covering MPP, and read access is required for Site, MPP, production, Assets and PBM. Select at most 366 days.');
      } finally {if (token === this.requestToken) this.page.main.find('.hpa-shell').attr('aria-busy','false');}
    }
  }
  function renderProductivityTabs() {
    return `<style>
      .hpt-tabs{display:flex;gap:24px;border-bottom:1px solid var(--border-color,#dce4e9);margin-bottom:12px}
      .hpt-tabs button{font:inherit;font-size:14px;font-weight:600;min-height:48px;padding:14px 2px;border:0;border-bottom:3px solid transparent;background:transparent;color:var(--text-muted,#667582)}
      .hpt-tabs button[aria-selected=true]{color:var(--text-color,#243447);border-bottom-color:currentColor}
      .hpt-shell>[hidden]{display:none!important}.hpt-message{padding:20px;font-size:14px}
      @media(max-width:600px){.hpt-tabs{gap:8px}.hpt-tabs button{flex:1;min-width:0;font-size:0;line-height:1.4;padding:12px 2px;border-radius:8px}.hpt-tabs button::after{content:attr(data-mobile-label);font-size:14px}.hpt-tabs button[aria-selected=true]{background:var(--control-bg,#f3f6f8)}}
    </style><div class="hpt-shell">
      <div class="hpt-tabs" role="tablist" aria-label="Hourly productivity reports">
        <button type="button" role="tab" id="hpt-analysis-tab" aria-controls="hpt-analysis-panel" aria-selected="true" data-report-tab="analysis" data-mobile-label="Analysis">Hourly Productivity Analysis</button>
        <button type="button" role="tab" id="hpt-summary-tab" aria-controls="hpt-summary-panel" aria-selected="false" tabindex="-1" data-report-tab="summary" data-mobile-label="Summary">Hourly Productivity Summary</button>
      </div>
      <div class="hpt-analysis-panel" id="hpt-analysis-panel" role="tabpanel" aria-labelledby="hpt-analysis-tab"></div>
      <div class="hpt-summary-panel" id="hpt-summary-panel" role="tabpanel" aria-labelledby="hpt-summary-tab" hidden></div>
    </div>`;
  }

  function mountProductivityTabs(wrapper) {
    const page = frappe.ui.make_app_page({parent:wrapper,title:'Hourly Productivity',single_column:true});
    page.main.html(renderProductivityTabs());
    const root = page.main.find('.hpt-shell')[0];
    wrapper.hourlyProductivityAnalysis = new HourlyProductivityAnalysisPage(wrapper,{main:page.main.find('.hpt-analysis-panel')});
    let summaryLoading = null;
    const loadSummary = () => {
      if (wrapper.hourlyProductivitySummary) return Promise.resolve(wrapper.hourlyProductivitySummary);
      if (summaryLoading) return summaryLoading;
      const summaryPage = {main:page.main.find('.hpt-summary-panel')};
      summaryPage.main.html('<div class="hpt-message">Loading Hourly Productivity Summary…</div>');
      const ready = frappe.hourlyProductivitySummaryView ? Promise.resolve() : (() => {
        if (!frappe.hourlySummaryViewLoading) {
          frappe.hourlySummaryViewLoading = new Promise((resolve,reject) => {
            const script = document.createElement('script');
            script.src = '/assets/is_production/js/hourly_productivity_summary_view.js?v=20261016-full-day';
            script.onload = () => frappe.hourlyProductivitySummaryView ? resolve() : reject(new Error('Summary view unavailable'));
            script.onerror = () => {script.remove();reject(new Error('Summary view could not be loaded'));};
            document.head.appendChild(script);
          });
          frappe.hourlySummaryViewLoading.catch(() => {frappe.hourlySummaryViewLoading = null;});
        }
        return frappe.hourlySummaryViewLoading;
      })();
      summaryLoading = ready.then(() => {
        wrapper.hourlyProductivitySummary = new frappe.hourlyProductivitySummaryView.HourlyProductivitySummaryPage(wrapper,summaryPage);
        return wrapper.hourlyProductivitySummary;
      }).catch(() => {
        summaryLoading = null;
        summaryPage.main.html('<div class="hpt-message">Could not load Summary. Select this tab again to retry.</div>');
      });
      return summaryLoading;
    };
    const tabs = Array.from(root.querySelectorAll('[role=tab]'));
    const activate = name => {
      if (!['analysis','summary'].includes(name)) return;
      wrapper.hourlyProductivityAnalysis.hideHourCard();
      for (const tab of tabs) {
        const active = tab.dataset.reportTab === name;
        tab.setAttribute('aria-selected',String(active));
        tab.tabIndex = active ? 0 : -1;
        root.querySelector('#'+tab.getAttribute('aria-controls')).hidden = !active;
      }
      if (name === 'summary') loadSummary();
    };
    for (const tab of tabs) {
      tab.addEventListener('click',() => activate(tab.dataset.reportTab));
      tab.addEventListener('keydown',event => {
        if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
        event.preventDefault();
        const index = tabs.indexOf(tab);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length-1 : (index+(event.key === 'ArrowRight' ? 1 : -1)+tabs.length)%tabs.length;
        activate(tabs[next].dataset.reportTab);
        tabs[next].focus();
      });
    }
    wrapper.hourlyProductivityTabs = {activate};
    return wrapper.hourlyProductivityTabs;
  }

  if (typeof frappe !== 'undefined') frappe.pages['hourly-productivity-analysis'].on_page_load = mountProductivityTabs;
  if (typeof module !== 'undefined' && module.exports) module.exports = {renderAnalysis,renderHourDetails,renderMobileAnalysis,styles,HourlyProductivityAnalysisPage,renderProductivityTabs,mountProductivityTabs};
})();
