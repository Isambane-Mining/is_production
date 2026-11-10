/* Compact operational-day matrix; no writes to planning or production. */
(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const number = (value, digits = 0) => value == null ? 'unknown' :
    Number(value).toLocaleString(undefined, {maximumFractionDigits: digits});
  const categories = [
    ['green', '#00b050', 'Green · 220 and above per production excavator'],
    ['amber', '#ff9f1c', 'Orange · 200–219 per production excavator'],
    ['red', '#d63c4b', 'Red · Below 200 per production excavator'],
    ['grey', '#f5f5f5', 'Fatigue / Non-working']
  ];

  const colours = Object.fromEntries(categories.map(([key, colour]) => [key, colour]));
  const categoryOf = cell => categories.some(([key]) => key === cell.category) ? cell.category : 'red';
  const cellBackground = cell => (cell.segments || []).length ? `background:linear-gradient(to right,${cell.segments.map(segment =>
    `${colours[segment.category] || colours.red} ${Number(segment.start_percent)}% ${Number(segment.end_percent)}%`).join(',')})` : '';
  const needsTextBacking = cell => (cell.segments || []).some(segment => segment.category === 'red') &&
    cell.segments.some(segment => segment.category !== 'red');
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
    const keyLabels = categories.map(([, , label]) => label);
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
      <p class="hpa-mobile-note">Thresholds combine excavators and scale for partial hours. * No production record · † Unfinished hour</p>
      <div class="hpa-mobile-detail" id="${escape(detailId)}" aria-live="polite">${row.cells[hourIndex] ? renderHourDetails(row,row.cells[hourIndex],hourIndex) : 'No hour selected.'}</div>
    </section>`;
  }

  function renderShiftAverageDetails(row, shift) {
    const cells = shift === "day"
      ? row.cells.slice(0, 12)
      : row.cells.slice(12, 24);

    const recorded = cells.filter(cell =>
      cell.completed && cell.has_record &&
      cell.actual_bcm != null &&
      Number.isFinite(Number(cell.actual_bcm))
    );

    const total = recorded.reduce(
      (sum, cell) => sum + Number(cell.actual_bcm), 0
    );

    const average = recorded.length ? total / recorded.length : null;

    const label = shift === "day"
      ? "DAY SHIFT · 06:00–18:00"
      : "NIGHT SHIFT · 18:00–06:00";

    return `<h3>${escape(row.date)} · ${label}</h3>
      <div class="hpa-average-popup">
        <div class="hpa-average-popup-main">
          <span>Average BCM / hour</span>
          <strong>${average == null ? "—" : number(average, 1)}</strong>
        </div>
        <div class="hpa-average-popup-total">
          <span>Shift total</span>
          <strong>${number(total, 1)} BCM</strong>
        </div>
      </div>`;
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
    const shiftAverage = cells => {
      const recorded = cells.filter(cell =>
        cell.completed &&
        cell.has_record &&
        cell.actual_bcm != null &&
        Number.isFinite(Number(cell.actual_bcm))
      );

      if (!recorded.length) return null;

      return recorded.reduce(
        (sum, cell) => sum + Number(cell.actual_bcm), 0
      ) / recorded.length;
    };

    const aria = categories.map(([key, , label]) => `${label}: ${number(data.pie.categories[key].hours, 2)} hours, ${number(data.pie.categories[key].percent, 1)}%`).join('; ');
    return `      <section class="hpa-chart" aria-label="Proportion of hourly performance categories"><div class="hpa-pie" role="img"
        aria-label="${escape(aria)}" style="background:conic-gradient(${gradient})"></div>
        <div class="hpa-legend"><strong>Hourly performance · ${number(data.pie.total_hours)} hours</strong><small>Thresholds combine excavators and scale for partial hours.</small>
          ${categories.map(([key, colour, label]) => `<div><i style="background:${colour}"></i><span>${escape(label)}</span>
            <b>${number(data.pie.categories[key].percent, 1)}%</b><small>${number(data.pie.categories[key].hours, 2)} hours</small></div>`).join('')}
        </div></section><div class="hpa-scroll hpa-analysis-scroll" tabindex="0" role="region" aria-label="Hourly BCM table; scroll horizontally">
      <table class="hpa-table hpa-analysis-matrix"><caption>Hourly recorded excavator BCM · ${escape(data.site)}</caption>
        <thead><tr><th rowspan="2" class="hpa-sticky-date">Date</th><th rowspan="2" class="hpa-sticky-day">Day</th>
          <th rowspan="2" class="hpa-sticky-month">Month</th><th colspan="13" class="hpa-shift-day"><span>DAY SHIFT · 06:00–18:00</span></th>
          <th colspan="13" class="hpa-shift-night"><span>NIGHT SHIFT · 18:00–06:00</span></th></tr>
          <tr>${data.hours.map((hour, i) => `<th class="hpa-hour ${i === 12 ? 'hpa-night-start' : ''}" title="${escape(hour)}">
            ${escape(hour.replaceAll(':00', ''))}</th>${i === 11 ? '<th class="hpa-hour hpa-average-heading">Day Avg</th>' : ''}${i === 23 ? '<th class="hpa-hour hpa-average-heading">Night Avg</th>' : ''}`).join('')}</tr></thead>
        <tbody>${data.rows.map((row, rowIndex) => {
          const divider = row.month_key !== lastMonth ? `<tr class="hpa-month-divider"><th colspan="29"><span>${escape(row.month)}</span></th></tr>` : '';
          lastMonth = row.month_key;

          const dayAverage = shiftAverage(row.cells.slice(0, 12));
          const nightAverage = shiftAverage(row.cells.slice(12, 24));

          const dayAverageCell = `<td class="hpa-cell hpa-shift-average hpa-day-average"
            data-hpa-row="${rowIndex}" data-hpa-average-shift="day"
            tabindex="0" aria-haspopup="dialog" aria-expanded="false"
            aria-controls="${escape(cardId)}"
            title="Open Day shift average details">${dayAverage == null ? '—' : number(dayAverage, 1)}</td>`;

          const nightAverageCell = `<td class="hpa-cell hpa-shift-average hpa-night-average"
            data-hpa-row="${rowIndex}" data-hpa-average-shift="night"
            tabindex="0" aria-haspopup="dialog" aria-expanded="false"
            aria-controls="${escape(cardId)}"
            title="Open Night shift average details">${nightAverage == null ? '—' : number(nightAverage, 1)}</td>`;

          return `${divider}<tr><th scope="row" class="hpa-sticky-date" title="MPP: ${escape(row.plan_name)}">${escape(row.date)}</th>
            <td class="hpa-sticky-day">${escape(row.day)}</td><td class="hpa-sticky-month" title="${escape(row.month)}">${escape(row.month.replace(/^(\w{3})\w+/, '$1'))}</td>
            ${row.cells.map((cell, index) => {
              const category = categoryOf(cell);
              const segments = cell.segments || [];
              const background = cellBackground(cell);
              return `<td class="hpa-cell hpa-${category} ${index === 12 ? 'hpa-night-start' : ''} ${segments.length > 1 ? 'hpa-partial' : ''}" style="${escape(background)}" tabindex="0"
                data-hpa-row="${rowIndex}" data-hpa-hour="${index}" aria-haspopup="dialog" aria-expanded="false" aria-controls="${escape(cardId)}"
                aria-label="${escape(hourHeading(row, cell))}; ${number(cell.actual_bcm, 1)} BCM. View hourly production details.">${needsTextBacking(cell) ? `<span class="hpa-mixed-text">${cellValue(cell)}</span>` : cellValue(cell)}</td>${index === 11 ? dayAverageCell : ''}${index === 23 ? nightAverageCell : ''}`;
            }).join('')}</tr>`;
        }).join('')}</tbody></table></div>
      ${renderMobileAnalysis(data,cardId)}
`;
  }


  let cardSequence = 0;
  class HourlyProductivityAnalysisPage {
    constructor(wrapper, page = null) {
      this.page = page || frappe.ui.make_app_page({parent:wrapper,title:'Hourly Productivity Analysis',single_column:true});
      this.method = 'is_production.production.page.hourly_productivity_analysis.hourly_productivity_analysis.';
      this.cardId = `hpa-hour-card-${++cardSequence}`;
      this.requestToken = 0;
      this.initializing = true;
      this.page.main.html(`<div class="hpa-shell"><div class="hpa-filters"><div class="hpa-site"></div>
        <div class="hpa-from_date"></div><div class="hpa-to_date"></div><div class="hpa-scope-control">
          <label for="hpa-scope-select">Machines</label>
          <select id="hpa-scope-select" class="form-control hpa-scope">
            <option value="all">All</option>
            <option value="production">Production Machines</option>
            <option value="spare">Spare Machines</option>
          </select>
        </div><button class="btn btn-primary hpa-refresh">Refresh</button><button type="button" class="btn btn-default hpa-export">Export Excel</button></div>
        <div class="hpa-content" aria-live="polite"></div>
        <div id="${this.cardId}" class="hpa-hour-card" role="dialog" aria-label="Hourly production details" hidden>
          <button type="button" class="hpa-card-close" aria-label="Close hourly details">×</button><div class="hpa-card-body"></div>
        </div></div>`);
      this.bindHourCard();
      for (const [name,label,type,options] of [['site','Site','Link','Location'],['from_date','From Date','Date'],['to_date','To Date','Date']]) {
        this[name] = frappe.ui.form.make_control({parent:this.page.main.find('.hpa-' + name),render_input:true,
          df:{fieldname:name,label,fieldtype:type,options,reqd:1,onchange:()=>{
            if (!this.initializing) {
              const tabs = wrapper.hourlyProductivityTabs;
              if (name === 'site') tabs?.markSite('analysis');
              else tabs?.markDates('analysis');
            }
            this.load();
          }}});
      }
      this.page.main.on('change','.hpa-scope', () => {
        wrapper.hourlyProductivityTabs?.recordScope(
          'analysis', this.page.main.find('.hpa-scope').val()
        );
        this.load();
      });
      this.page.main.on('click','.hpa-refresh',()=>this.load());
      this.page.main.on('click','.hpa-export', () => {
        const site = this.site.get_value();
        const from_date = this.from_date.get_value();
        const to_date = this.to_date.get_value();
        if (!site || !from_date || !to_date || from_date > to_date) {
          frappe.msgprint('Select a valid Site, From Date and To Date.');
          return;
        }
        const machine_scope = this.page.main.find('.hpa-scope').val();
        const params = new URLSearchParams({
          site, from_date, to_date, machine_scope
        });
        const endpoint = this.method + 'export_excel';
        window.location.href =
          '/api/method/' + endpoint + '?' + params.toString();
      });
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
      main.on('pointerover focusin', '.hpa-analysis-matrix .hpa-cell:not(.hpa-shift-average)', event => {
        if (event.type === 'pointerover' && (this.hoverDismissed || event.originalEvent?.pointerType === 'touch')) return;
        if (this.cardPinned && event.type === 'pointerover') return;
        this.showHourCard(event.currentTarget);
      });

      main.on('pointerover focusin', '.hpa-analysis-matrix .hpa-shift-average', event => {
        if (event.type === 'pointerover' && (this.hoverDismissed || event.originalEvent?.pointerType === 'touch')) return;
        if (this.cardPinned && event.type === 'pointerover') return;
        this.showShiftAverageCard(event.currentTarget);
      });
      main.on('pointerout focusout', '.hpa-analysis-matrix .hpa-cell:not(.hpa-shift-average), .hpa-analysis-matrix .hpa-shift-average, .hpa-hour-card:not(.hm-machine-card)', event => {
        const next = event.relatedTarget;
        if (this.card.contains(next) || this.activeCell?.contains(next)) return;
        if (!this.cardPinned || event.type === 'focusout') {
          clearTimeout(this.cardTimer);
          this.cardTimer = setTimeout(() => this.hideHourCard(), 180);
        }
      });
      main.on('pointerover focusin', '.hpa-hour-card:not(.hm-machine-card)', () => clearTimeout(this.cardTimer));
      main.on('click', '.hpa-analysis-matrix .hpa-cell:not(.hpa-shift-average)', event => {
        if (this.activeCell === event.currentTarget && this.cardPinned) this.hideHourCard();
        else {
          this.showHourCard(event.currentTarget);
          this.cardPinned = true;
          this.card.querySelector('.hpa-card-close').focus({preventScroll:true});
        }
      });
      main.on('click', '.hpa-analysis-matrix .hpa-shift-average', event => {
        if (this.activeCell === event.currentTarget && this.cardPinned) this.hideHourCard();
        else {
          this.showShiftAverageCard(event.currentTarget);
          this.cardPinned = true;
          this.card.querySelector('.hpa-card-close').focus({preventScroll:true});
        }
      });

      main.on('keydown', '.hpa-analysis-matrix .hpa-cell:not(.hpa-shift-average)', event => {
        if (event.key === 'Enter' || event.key === ' ') {event.preventDefault();event.currentTarget.click();}
      });
      main.on('keydown', '.hpa-analysis-matrix .hpa-shift-average', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          event.currentTarget.click();
        }
      });

      document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && this.activeCell) {
          event.preventDefault();
          this.closeHourCard();
        }
      });
      main.on('click', '.hpa-hour-card:not(.hm-machine-card) .hpa-card-close', () => this.closeHourCard());
      document.addEventListener('pointermove', event => {
        if (this.hoverDismissed && event.pointerType !== 'touch' && (event.movementX || event.movementY)) {
          this.hoverDismissed = false;
          const cell = event.target.closest?.('.hpa-analysis-matrix .hpa-cell:not(.hpa-shift-average), .hpa-analysis-matrix .hpa-shift-average');
          if (cell && main[0].contains(cell)) {
            if (cell.classList.contains('hpa-shift-average')) this.showShiftAverageCard(cell);
            else this.showHourCard(cell);
          }
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
    showShiftAverageCard(cell) {
      clearTimeout(this.cardTimer);

      const row = this.analysisData?.rows[Number(cell.dataset.hpaRow)];
      const shift = cell.dataset.hpaAverageShift;

      if (!row || !["day", "night"].includes(shift)) return;

      if (this.activeCell !== cell) this.hideHourCard();

      this.activeCell = cell;
      cell.setAttribute("aria-expanded", "true");

      this.card.querySelector(".hpa-card-body").innerHTML =
        renderShiftAverageDetails(row, shift);

      this.card.hidden = false;

      const anchor = cell.getBoundingClientRect();
      const bounds = this.card.getBoundingClientRect();
      const left = Math.max(12, Math.min(
        anchor.left, window.innerWidth - bounds.width - 12
      ));

      let top = anchor.bottom + 8;
      if (top + bounds.height > window.innerHeight - 12) {
        top = anchor.top - bounds.height - 8;
      }

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
      const args = {
        site:this.site.get_value(),
        from_date:this.from_date.get_value(),
        to_date:this.to_date.get_value(),
        machine_scope:this.page.main.find('.hpa-scope').val()
      };
      this.page.main.find('.hpa-shell').attr('aria-busy','false');
      if (!args.site || !args.from_date || !args.to_date) {this.message('Select a site and date range.');return;}
      if (args.from_date > args.to_date) {this.message('From Date must be on or before To Date.');return;}
      this.message('Loading hourly performance…');
      this.page.main.find('.hpa-shell').attr('aria-busy','true');
      try {
        const response = await frappe.call({method:this.method + 'get_analysis',args});
        if (token !== this.requestToken) return;
        this.analysisData = response.message;
        const scoped = args.machine_scope !== 'all';
        const scopeNotice = scoped
          ? '<p class="hpa-message">Machine availability/utilisation is filtered by historical MPP. BCM, targets and productivity remain site-wide.</p>'
          : '';
        this.page.main.find('.hpa-content').html(
          scopeNotice + renderAnalysis(response.message, this.cardId)
        );
      } catch (error) {
        if (token !== this.requestToken) return;
        this.message('Unable to load. Every date needs a covering MPP, and read access is required for Site, MPP, production, Assets and PBM. Select at most 366 days.');
      } finally {if (token === this.requestToken) this.page.main.find('.hpa-shell').attr('aria-busy','false');}
    }
  }

  function machineColour(production, available, utilised, verified) {
    const valid = value => value != null && String(value).trim() !== '' &&
      Number.isFinite(Number(value)) && Number(value) >= 0;
    if (!verified || ![production, available, utilised].every(valid) ||
        Number(production) <= 0 || Number(available) <= 0) return 'neutral';

    const expected = Math.min(Number(production), Number(available));
    const shortfall = expected - Number(utilised);
    if (shortfall <= 0) return 'green';
    if (shortfall < 2) return 'orange';
    return 'red';
  }

  function matchesUnavailableRange(total, available, selected) {
    if (selected == null || available == null) return false;
    if (!Number.isFinite(Number(total)) ||
        !Number.isFinite(Number(available)) ||
        !Number.isInteger(selected) || selected < 0 ||
        Number(total) <= 0) return false;

    const missing = Number(total) - Number(available);
    const epsilon = 1e-9;

    if (selected === 0) return Math.abs(missing) <= epsilon;

    return missing > selected - 1 + epsilon &&
           missing <= selected + epsilon;
  }


  function renderMachineDetails(row, cell, index, viewMode = "utilisation", scope = "all") {
    const valid = value => value !== null && value !== undefined &&
      String(value).trim() !== "" && Number.isFinite(Number(value));

    const fmt = (value, digits = 1) => valid(value)
      ? Number(value).toLocaleString(undefined, {maximumFractionDigits: digits})
      : "—";

    const verified = Boolean(cell.completed && cell.has_record);

    if (viewMode === "availability") {
      const available = verified && valid(cell.available)
        ? Number(cell.available) : null;
      const total = valid(row.fleet_total) ? Number(row.fleet_total) : null;
      const unavailable = available !== null && total !== null
        ? Math.max(0, total - available) : null;

      return `<h3>${escape(hourHeading(row, cell))}</h3>
        <div class="hm-popup-unavailable">
          <span>Unavailable ADTs</span>
          <strong>${escape(fmt(unavailable, 0))}</strong>
        </div>`;
    }

    const utilised = verified && valid(cell.utilised)
      ? fmt(cell.utilised, 0) : "—";
    const available = verified && valid(cell.available)
      ? fmt(cell.available, 2) : "—";

    return `<h3>${escape(hourHeading(row, cell))}</h3>
      <div class="hm-popup-utilisation">
        <strong>${escape(utilised)} / ${escape(available)}</strong>
        <span>Utilised / Available ADTs</span>
      </div>`;
  }

  function renderMachineAverageDetails(row, shift) {
    const cells = shift === "day"
      ? row.cells.slice(0, 12)
      : row.cells.slice(12, 24);

    const values = cells
      .filter(cell => cell.completed && cell.has_record &&
        cell.available != null && String(cell.available).trim() !== "" &&
        Number.isFinite(Number(cell.available)) && Number(cell.available) >= 0)
      .map(cell => Number(cell.available));

    const average = values.length
      ? values.reduce((sum, value) => sum + value, 0) / values.length
      : null;

    const label = shift === "day"
      ? "DAY SHIFT · 06:00–18:00"
      : "NIGHT SHIFT · 18:00–06:00";

    const formatted = average == null ? "—" :
      average.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
      });

    return `<h3>${escape(row.date)} · ${label}</h3>
      <div class="hm-popup-average">
        <span>Average available ADTs</span>
        <strong>${formatted}</strong>
      </div>`;
  }

  function bindMachinePopup(panel, getData, getMode, getScope) {
    const card = panel.find(".hm-machine-card")[0];
    let activeCell = null;
    let pinned = false;
    let timer = null;

    const hide = () => {
      clearTimeout(timer);
      activeCell?.setAttribute("aria-expanded", "false");
      activeCell = null;
      pinned = false;
      card.hidden = true;
    };

    const close = () => {
      const cell = activeCell;
      hide();
      cell?.focus({preventScroll:true});
    };

    const show = cell => {
      clearTimeout(timer);
      const data = getData();
      const row = data?.rows?.[Number(cell.dataset.hmRow)];
      const index = Number(cell.dataset.hmHour);
      const averageShift = cell.dataset.hmAverageShift;

      if (!row || (!averageShift && !row.cells?.[index])) return;

      if (activeCell !== cell) hide();

      activeCell = cell;
      cell.setAttribute("aria-expanded", "true");
      card.querySelector(".hpa-card-body").innerHTML = averageShift
        ? renderMachineAverageDetails(row, averageShift)
        : renderMachineDetails(row, row.cells[index], index, getMode(), getScope());

      card.hidden = false;

      const anchor = cell.getBoundingClientRect();
      const bounds = card.getBoundingClientRect();
      const left = Math.max(12, Math.min(
        anchor.left, window.innerWidth - bounds.width - 12
      ));

      let top = anchor.bottom + 8;
      if (top + bounds.height > window.innerHeight - 12) {
        top = anchor.top - bounds.height - 8;
      }

      card.style.left = `${left}px`;
      card.style.top = `${Math.max(12, top)}px`;
    };

    panel.on("pointerover focusin", ".hm-hour-cell, .hm-average-cell", event => {
      if (event.type === "pointerover" &&
          event.originalEvent?.pointerType === "touch") return;
      if (pinned && event.type === "pointerover") return;
      show(event.currentTarget);
    });

    panel.on("pointerout focusout", ".hm-hour-cell, .hm-average-cell, .hm-machine-card", event => {
      const next = event.relatedTarget;
      if (card.contains(next) || activeCell?.contains(next)) return;
      if (!pinned || event.type === "focusout") {
        clearTimeout(timer);
        timer = setTimeout(hide, 180);
      }
    });

    panel.on("pointerover focusin", ".hm-machine-card", () => clearTimeout(timer));

    panel.on("click", ".hm-hour-cell, .hm-average-cell", event => {
      if (activeCell === event.currentTarget && pinned) {
        hide();
      } else {
        show(event.currentTarget);
        pinned = true;
        card.querySelector(".hpa-card-close").focus({preventScroll:true});
      }
    });

    panel.on("keydown", ".hm-hour-cell, .hm-average-cell", event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        event.currentTarget.click();
      }
    });

    panel.on("click", ".hm-machine-card-close", close);

    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && activeCell) {
        event.preventDefault();
        close();
      }
    });

    document.addEventListener("pointerdown", event => {
      if (!card.contains(event.target) && !activeCell?.contains(event.target)) hide();
    });

    document.addEventListener("scroll", event => {
      if (!card.contains(event.target)) hide();
    }, true);

    window.addEventListener("resize", hide);

    return {hide};
  }

  function renderMachineMatrix(data, unavailableNumber = null, viewMode = 'utilisation') {
    let lastMonth = '';
    const isAvailability = viewMode === 'availability';

    const averageAvailable = cells => {
      const values = cells
        .filter(cell => cell.completed && cell.has_record &&
          cell.available != null && String(cell.available).trim() !== '' &&
          Number.isFinite(Number(cell.available)) &&
          Number(cell.available) >= 0)
        .map(cell => Number(cell.available));

      return values.length
        ? values.reduce((sum, value) => sum + value, 0) / values.length
        : null;
    };

    const metric = (value, cls, digits) =>
      Number.isFinite(Number(value)) && value != null && Number(value) >= 0
        ? `<span class="${cls}">${Number(value).toLocaleString('en-US',{maximumFractionDigits:digits})}</span>`
        : '—';

    const rows = data.rows.map((row, rowIndex) => {
      const divider = row.month_key !== lastMonth
        ? `<tr class="hpa-month-divider">
             <th colspan="${isAvailability ? 29 : 27}"><span>${escape(row.month)}</span></th>
           </tr>`
        : '';

      lastMonth = row.month_key;

      const averageTotal = metric(row.fleet_total, 'hm-total', 0);

      const renderAverage = (sourceCells, shift) => {
        const average = averageAvailable(sourceCells);
        const value = average == null ? '—' :
          `<span class="hm-avail">${average.toLocaleString('en-US', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
          })}</span>`;

        return `<td class="hpa-cell hm-cell hm-availability-average hm-average-cell"
          data-hm-row="${rowIndex}" data-hm-average-shift="${shift}"
          tabindex="0" aria-haspopup="dialog" aria-expanded="false"
          aria-controls="hm-machine-card"
          aria-label="${escape(row.date)} ${shift} shift average available ADTs. Open average details.">${value} / ${averageTotal}</td>`;
      };

      const dayAverageCell = isAvailability
        ? renderAverage(row.cells.slice(0, 12), 'day') : '';

      const nightAverageCell = isAvailability
        ? renderAverage(row.cells.slice(12, 24), 'night') : '';

      const cells = row.cells.map((cell, index) => {
        const verified = cell.completed && cell.has_record;
        const total = Number(row.fleet_total);
        const highlighted = verified &&
          matchesUnavailableRange(total, cell.available, unavailableNumber);
        const available = verified
          ? metric(cell.available,'hm-avail',2) : '—';
        const utilised = verified
          ? metric(cell.utilised,'hm-used',0) : '—';

        const totalFleet = metric(row.fleet_total, 'hm-total', 0);

        const value = isAvailability
          ? (available === '—' && totalFleet === '—' ? '—' : `${available} / ${totalFleet}`)
          : (available === '—' && utilised === '—' ? '—' : `${utilised} / ${available}`);

        const productionVerified = verified && data.machine_scope !== 'spare';
        const colour = machineColour(
          row.production_fleet_total, cell.production_available,
          cell.production_utilised, productionVerified
        );

        const cellClass = isAvailability ? 'hm-availability-cell' : `hm-${colour}`;

        return `<td class="hpa-cell hm-cell hm-hour-cell ${cellClass} ${
          index === 12 ? 'hpa-night-start' : ''
        } ${highlighted ? 'hm-unavailable-match' : ''}"
          data-hm-row="${rowIndex}" data-hm-hour="${index}"
          tabindex="0" aria-haspopup="dialog" aria-expanded="false"
          aria-controls="hm-machine-card"
          aria-label="${escape(hourHeading(row, cell))}; ${isAvailability ? 'Availability' : 'Utilisation'} details. Open hourly machine details.">${value}</td>${isAvailability && index === 11 ? dayAverageCell : ""}${isAvailability && index === 23 ? nightAverageCell : ""}`;
      }).join('');

      return `${divider}<tr>
        <th scope="row" class="hpa-sticky-date">${escape(row.date)}</th>
        <td class="hpa-sticky-day">${escape(row.day)}</td>
        <td class="hpa-sticky-month">${escape(row.month)}</td>
        ${cells}
      </tr>`;
    }).join('');

    return `${viewMode === 'availability' ? `
    <section class="hpa-chart hm-chart" aria-label="Availability legend">
      <div class="hm-availability-legend">
        <strong>Availability</strong>
        <div><i class="hm-available-key"></i><span>Available ADTs</span></div>
        <div><i class="hm-total-key"></i><span>Total fleet</span></div>
      </div>
    </section>` : `
    <section class="hpa-chart hm-chart" aria-label="Machine utilisation legend">
      <div class="hm-legend hm-legend-card">
        <section class="hm-legend-section hm-legend-counts">
          <h3><span class="hm-legend-heading-icon">▱</span>Machine counts</h3>
          <div class="hm-legend-items">
            <div class="hm-legend-item">
              <i class="hm-utilised-key"></i><span>Utilised ADTs</span>
            </div>
            <div class="hm-legend-item">
              <i class="hm-available-key"></i><span>Available ADTs</span>
            </div>
          </div>
        </section>
        <section class="hm-legend-section hm-legend-target">
          <h3><span class="hm-legend-heading-icon">◎</span>Production target</h3>
          <div class="hm-legend-formula">Expected = min(P, A)</div>
          <p><strong>P</strong> = MPP production ADT count</p>
          <p><strong>A</strong> = available production ADTs</p>
        </section>
        <section class="hm-legend-section hm-legend-classification">
          <h3><span class="hm-legend-heading-icon">▥</span>Performance classification</h3>
          <div class="hm-classification-list">
            <div class="hm-classification-item">
              <i class="hm-green-key"></i>
              <span><strong>Green</strong> · Production utilisation meets or exceeds expected</span>
            </div>
            <div class="hm-classification-item">
              <i class="hm-orange-key"></i>
              <span><strong>Orange</strong> · Shortfall above 0 and below 2 machines</span>
            </div>
            <div class="hm-classification-item">
              <i class="hm-red-key"></i>
              <span><strong>Red</strong> · Shortfall of 2 or more machines</span>
            </div>
            <div class="hm-classification-item">
              <i class="hm-unknown-key"></i>
              <span><strong>Grey</strong> · Zero available / unknown / missing / pending / spare-only</span>
            </div>
          </div>
        </section>
      </div>
    </section>`}<div class="hpa-scroll ${isAvailability ? "hm-availability-scroll" : ""}" tabindex="0" role="region"
      aria-label="${viewMode === "availability" ? "Available versus total fleet hourly table" : "Machine utilised versus available hourly table"}; scroll horizontally and vertically">
      <table class="hpa-table hm-matrix ${isAvailability ? "hm-availability-matrix" : ""}">
        <caption>${viewMode === "availability" ? "Available / Total fleet ADTs" : "Utilised / Available ADTs"} · ${escape(data.site)}</caption>
        <thead>
          <tr>
            <th rowspan="2" class="hpa-sticky-date">Date</th>
            <th rowspan="2" class="hpa-sticky-day">Day</th>
            <th rowspan="2" class="hpa-sticky-month">Month</th>
            <th colspan="${isAvailability ? 13 : 12}" class="hpa-shift-day">
              DAY SHIFT · 06:00–18:00
            </th>
            <th colspan="${isAvailability ? 13 : 12}" class="hpa-shift-night">
              NIGHT SHIFT · 18:00–06:00
            </th>
          </tr>
          <tr>
            ${data.hours.map((hour,index) =>
              `<th class="hpa-hour ${
                index === 12 ? 'hpa-night-start' : ''
              }">${escape(hour.replaceAll(':00',''))}</th>${isAvailability && index === 11 ? '<th class="hpa-hour hm-availability-average-heading">Day Avg</th>' : ''}${isAvailability && index === 23 ? '<th class="hpa-hour hm-availability-average-heading">Night Avg</th>' : ''}`
            ).join('')}
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    `;
  }

  function renderProductivityTabs() {
    return `<div class="hpt-shell">
      <div class="hpt-tabs" role="tablist" aria-label="Hourly productivity reports">
        <button type="button" role="tab" id="hpt-summary-tab" aria-controls="hpt-summary-panel" aria-selected="true" data-report-tab="summary" data-mobile-label="Current">Current Productivity Analysis</button>
        <button type="button" role="tab" id="hpt-analysis-tab" aria-controls="hpt-analysis-panel" aria-selected="false" tabindex="-1" data-report-tab="analysis" data-mobile-label="Historical">Hourly Productivity Analysis</button>
        <button type="button" role="tab" id="hpt-availability-tab" aria-controls="hpt-machines-panel" aria-selected="false" tabindex="-1" data-report-tab="availability" data-mobile-label="Availability">Availability</button>
        <button type="button" role="tab" id="hpt-machines-tab" aria-controls="hpt-machines-panel" aria-selected="false" tabindex="-1" data-report-tab="machines" data-mobile-label="Utilisation">Utilisation</button>
      </div>
      <div class="hpt-summary-panel" id="hpt-summary-panel" role="tabpanel" aria-labelledby="hpt-summary-tab"></div>
      <div class="hpt-analysis-panel" id="hpt-analysis-panel" role="tabpanel" aria-labelledby="hpt-analysis-tab" hidden></div>
      <div class="hpt-machines-panel" id="hpt-machines-panel" role="tabpanel" aria-labelledby="hpt-machines-tab" hidden></div>
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
      summaryPage.main.html('<div class="hpt-message">Loading Current Productivity Analysis…</div>');
      const ready = frappe.hourlyProductivitySummaryView ? Promise.resolve() : (() => {
        if (!frappe.hourlySummaryViewLoading) {
          frappe.hourlySummaryViewLoading = new Promise((resolve,reject) => {
            const script = document.createElement('script');
            script.src = '/assets/is_production/js/hourly_productivity_summary_view.js?v=20261110-utilised-available';
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


    let machineViewMode = 'utilisation';
    let machineLoading = null;
    const loadMachines = () => {
      if (machineLoading) return machineLoading;

      const panel = page.main.find('.hpt-machines-panel');

      panel.html(`
        
        <div class="hpa-shell hm-shell">
          <div class="hpa-filters hm-filters">
            <div class="hm-site"></div>
            <div class="hm-from_date"></div>
            <div class="hm-to_date"></div>
            <div class="hm-scope-control">
              <label for="hm-scope-select">Machines</label>
              <select id="hm-scope-select" class="form-control hm-scope">
                <option value="all">All</option>
                <option value="production">Production Machines</option>
                <option value="spare">Spare Machines</option>
              </select>
            </div>
            <div class="hm-unavailable-control">
              <label for="hm-unavailable-select">Machine Number Unavailable</label>
              <select id="hm-unavailable-select" class="form-control hm-unavailable">
                <option value="">No highlight</option>
              </select>
            </div>
            <button type="button" class="btn btn-primary hm-refresh">
              Refresh
            </button>
            <button type="button" class="btn btn-default hm-export">
              Export Excel
            </button>
          </div>
          <div class="hm-status" role="status" aria-live="polite">
            Select a site and date range.
          </div>
          <div class="hm-content"></div>
          <div id="hm-machine-card" class="hpa-hour-card hm-machine-card"
               role="dialog" aria-label="Hourly machine details" hidden>
            <button type="button" class="hpa-card-close hm-machine-card-close"
                    aria-label="Close machine details">×</button>
            <div class="hpa-card-body"></div>
          </div>
        </div>`);

      let initializing = true;
      let requestToken = 0;
      let machineData = null;

      const controls = {};

      let machinePopup = null;

      const drawMachineData = () => {
        if (!machineData) return;
        machinePopup?.hide();
        const value = panel.find('.hm-unavailable').val();
        const number = value === '' ? null : Number(value);
        panel.find('.hm-content').html(renderMachineMatrix(machineData, number, machineViewMode));
      };

      machinePopup = bindMachinePopup(
        panel,
        () => machineData,
        () => machineViewMode,
        () => panel.find('.hm-scope').val()
      );

      const refresh = async () => {
        if (initializing) return;
        machinePopup?.hide();

        const token = ++requestToken;
        const site = controls.site.get_value();
        const from_date = controls.from_date.get_value();
        const to_date = controls.to_date.get_value();
        const scope = panel.find('.hm-scope').val();

        const status = panel.find('.hm-status');
        const content = panel.find('.hm-content');

        content.empty();

        if (!site || !from_date || !to_date ||
            from_date > to_date) {
          status.text('Select a valid site and date range.');
          return;
        }

        status.text('Loading machine counts…');

        try {
          const response = await frappe.call({
            method:
              'is_production.production.page.hourly_productivity_analysis.hourly_productivity_analysis.get_machine_analysis',
            args: {site,from_date,to_date,machine_scope:scope}
          });

          if (token !== requestToken) return;

          const data = response.message;

          if (data?.planning_error || data?.configuration_error) {
            const error = data.planning_error ||
              data.configuration_error;
            status.text(error.title || 'Configuration unavailable.');
            return;
          }

          if (!data?.rows?.length) {
            status.text('No data available for this period.');
            return;
          }

          machineData = data;
          const selector = panel.find('.hm-unavailable');
          const previous = selector.val();
          const maximum = Math.max(0, ...data.rows.map(
            row => Number(row.fleet_total) || 0
          ));
          selector.empty().append(
            $('<option>').val('').text('No highlight')
          );
          if (maximum > 0) {
            selector.append($('<option>').val('0').text('0 · Fully available'));
          }
          for (let n = 1; n <= maximum; n++) {
            selector.append(
              $('<option>').val(String(n)).text(`${n - 1}–${n}`)
            );
          }
          selector.val(previous && Number(previous) <= maximum ? previous : '');
          drawMachineData();
          status.empty();

        } catch (error) {
          if (token === requestToken) {
            status.text(
              'Unable to load machine data. Check permissions and MPP.'
            );
          }
        }
      };

      for (const [name,label,type,options] of [
        ['site','Site','Link','Location'],
        ['from_date','From Date','Date'],
        ['to_date','To Date','Date']
      ]) {
        controls[name] = frappe.ui.form.make_control({
          parent: panel.find('.hm-' + name),
          render_input: true,
          df: {
            fieldname: 'hm_' + name,
            label,
            fieldtype: type,
            options,
            reqd: 1,
            onchange: () => {
              if (!initializing) {
                const tabs = wrapper.hourlyProductivityTabs;
                if (name === 'site') tabs?.markSite('machines');
                else tabs?.markDates('machines');
              }
              refresh();
            }
          }
        });
      }


      panel.find('.hm-export').on('click', () => {
        const site = controls.site.get_value();
        const from_date = controls.from_date.get_value();
        const to_date = controls.to_date.get_value();
        const machine_scope = panel.find('.hm-scope').val();

        if (!site || !from_date || !to_date || from_date > to_date) {
          frappe.msgprint('Select valid site and dates.');
          return;
        }

        const unavailable_number = panel.find('.hm-unavailable').val();
        const params = new URLSearchParams({
          site, from_date, to_date, machine_scope, unavailable_number,
          view_mode: machineViewMode
        });

        window.location.href =
          '/api/method/is_production.production.page.hourly_productivity_analysis.hourly_productivity_analysis.export_machine_excel?' +
          params.toString();
      });

      panel.find('.hm-unavailable').on('change',drawMachineData);
      panel.find('.hm-refresh').on('click',refresh);
      panel.find('.hm-scope').on('change', () => {
        wrapper.hourlyProductivityTabs?.recordScope(
          'machines', panel.find('.hm-scope').val()
        );
        refresh();
      });

      const initialize = async () => {
        try {
          const analysis = wrapper.hourlyProductivityAnalysis;
          let from_date = analysis.from_date.get_value();
          let to_date = analysis.to_date.get_value();

          if (!from_date || !to_date) {
            const response = await frappe.call({
              method:
                'is_production.production.page.hourly_productivity_analysis.hourly_productivity_analysis.get_defaults'
            });
            from_date = response.message.from_date;
            to_date = response.message.to_date;
          }

          await controls.from_date.set_value(from_date);
          await controls.to_date.set_value(to_date);

          const site = analysis.site.get_value();
          if (site) await controls.site.set_value(site);

          initializing = false;
          if (site) await refresh();
        } catch (error) {
          initializing = false;
          panel.find('.hm-status').text(
            'Select a site and date range.'
          );
        }
      };

      wrapper.hourlyProductivityMachines = {
        refresh,
        draw: drawMachineData,
        site: controls.site,
        from_date: controls.from_date,
        to_date: controls.to_date
      };

      machineLoading = Promise.resolve(initialize());
      return machineLoading;
    };

    // Machine scope defaults are shared, but each tab remains independent.
    let lastMachineScope = 'all';
    const editedMachineScopes = new Set();

    const scopeSelector = {
      summary: '.hps-scope',
      analysis: '.hpa-scope',
      machines: '.hm-scope'
    };

    const scopePanel = {
      summary: '.hpt-summary-panel',
      analysis: '.hpt-analysis-panel',
      machines: '.hpt-machines-panel'
    };

    const recordScope = (name, value) => {
      if (!['all','production','spare'].includes(value)) return;
      editedMachineScopes.add(name);
      lastMachineScope = value;
    };

    const applyScope = name => {
      if (editedMachineScopes.has(name)) return;

      const panel = page.main.find(scopePanel[name]);
      const select = panel.find(scopeSelector[name]);
      if (!select.length) return;

      if (select.val() === lastMachineScope) return;

      select.val(lastMachineScope);

      if (name === 'summary') {
        wrapper.hourlyProductivitySummary?.load();
      } else if (name === 'analysis') {
        wrapper.hourlyProductivityAnalysis?.load();
      } else {
        wrapper.hourlyProductivityMachines?.refresh();
      }
    };

    // Share defaults without linking independently edited filters.
    const tabFilterState = {
      active: 'summary',
      syncing: false,
      editedSites: new Set(),
      editedDates: new Set()
    };

    const markSite = name => {
      if (!tabFilterState.syncing) tabFilterState.editedSites.add(name);
    };
    const markDates = name => {
      if (!tabFilterState.syncing) tabFilterState.editedDates.add(name);
    };

    const syncFilters = async (from, to) => {
      const analysis = wrapper.hourlyProductivityAnalysis;
      const summary = wrapper.hourlyProductivitySummary;
      const machines = wrapper.hourlyProductivityMachines;

      const sourceName = from === 'availability' ? 'machines' : from;
      const targetName = to === 'availability' ? 'machines' : to;
      const source = sourceName === 'summary' ? summary :
        sourceName === 'analysis' ? analysis : machines;
      const target = targetName === 'summary' ? summary :
        targetName === 'analysis' ? analysis : machines;

      if (!source || !target) return;

      tabFilterState.syncing = true;
      try {
        // Current Productivity Analysis is the default site source.
        if (targetName !== 'summary' && summary?.site &&
            !tabFilterState.editedSites.has(targetName)) {
          const site = summary.site.get_value();
          if (site && target.site.get_value() !== site) {
            await target.site.set_value(site);
          }
        }

        // Historical tabs exchange date defaults only.
        if (sourceName !== 'summary' && targetName !== 'summary' &&
            !tabFilterState.editedDates.has(targetName)) {
          for (const field of ['from_date','to_date']) {
            if (!source[field] || !target[field]) continue;
            const value = source[field].get_value();
            if (value && target[field].get_value() !== value) {
              await target[field].set_value(value);
            }
          }
        }
      } finally {
        tabFilterState.syncing = false;
      }
    };

    const tabs = Array.from(root.querySelectorAll('[role=tab]'));
    const activate = name => {
      if (!['analysis','summary','machines','availability'].includes(name)) return;
      const previous = tabFilterState.active;
      tabFilterState.active = name;
      wrapper.hourlyProductivityAnalysis.hideHourCard();
      const machineTab = name === 'machines' || name === 'availability';
      for (const tab of tabs) {
        const active = tab.dataset.reportTab === name;
        tab.setAttribute('aria-selected',String(active));
        tab.tabIndex = active ? 0 : -1;
      }
      const activePanelId = machineTab ? 'hpt-machines-panel' : `hpt-${name}-panel`;
      for (const panel of root.querySelectorAll('[role="tabpanel"]')) {
        panel.hidden = panel.id !== activePanelId;
      }
      const machinePanel = root.querySelector('#hpt-machines-panel');
      if (machinePanel) {
        machinePanel.setAttribute(
          'aria-labelledby',
          name === 'availability' ? 'hpt-availability-tab' : 'hpt-machines-tab'
        );
      }
      if (name === 'summary') {
        loadSummary().then(() => {
          if (tabFilterState.active === 'summary') applyScope('summary');
        });
      }
      if (name === 'machines' || name === 'availability') {
        machineViewMode = name === 'availability' ? 'availability' : 'utilisation';
        loadMachines().then(async () => {
          if (tabFilterState.active !== name) return;
          wrapper.hourlyProductivityMachines?.draw();
          await syncFilters(previous,name);
          if (tabFilterState.active === name) applyScope('machines');
        });
      } else if (name === 'analysis') {
        syncFilters(previous,name).then(() => {
          if (tabFilterState.active === 'analysis') applyScope('analysis');
        });
      }
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
    activate('summary');
    wrapper.hourlyProductivityTabs = {
      activate, markSite, markDates, recordScope,
      get syncing() { return tabFilterState.syncing; }
    };
    return wrapper.hourlyProductivityTabs;
  }

  if (typeof frappe !== 'undefined') frappe.pages['hourly-productivity-analysis'].on_page_load = mountProductivityTabs;
  if (typeof module !== 'undefined' && module.exports) module.exports = {renderAnalysis, renderHourDetails, renderMobileAnalysis, HourlyProductivityAnalysisPage, renderProductivityTabs, mountProductivityTabs, renderMachineMatrix, renderMachineDetails, matchesUnavailableRange, renderShiftAverageDetails, renderMachineAverageDetails};
})();
