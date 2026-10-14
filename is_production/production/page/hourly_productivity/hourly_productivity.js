/* Canonical Desk host. Each report owns its controls, requests and cached DOM. */
(() => {
  const TABS = ['summary', 'analysis'];
  class HourlyProductivityPage {
    constructor(wrapper) {
      this.page = frappe.ui.make_app_page({parent: wrapper, title: 'Hourly Productivity', single_column: true});
      this.controllers = {};
      this.navigationToken = 0;
      this.page.main.html(`<div class="hourly-productivity"><div class="hp-tabs" role="tablist" aria-label="Hourly Productivity">
        <button type="button" id="hp-tab-summary" role="tab" aria-controls="hp-panel-summary" data-tab="summary">Hourly Productivity Summary</button>
        <button type="button" id="hp-tab-analysis" role="tab" aria-controls="hp-panel-analysis" data-tab="analysis">Hourly Productivity Analysis</button></div>
        <section id="hp-panel-summary" role="tabpanel" aria-labelledby="hp-tab-summary" hidden></section>
        <section id="hp-panel-analysis" role="tabpanel" aria-labelledby="hp-tab-analysis" hidden></section></div>`);
      this.page.main.on('click', '[data-tab]', event => this.show(event.currentTarget.dataset.tab));
      this.page.main.on('keydown', '[data-tab]', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const tab = event.key === 'Home' ? 'summary' : event.key === 'End' ? 'analysis' :
          event.currentTarget.dataset.tab === 'summary' ? 'analysis' : 'summary';
        this.page.main.find(`[data-tab=${tab}]`).trigger('focus');
        this.show(tab);
      });
      this.assetsReady = frappe.require('hourly_productivity.bundle.js');
      this.ready = this.show(this.routeTab() || 'summary');
    }

    routeTab() {
      const tab = frappe.route_options?.tab;
      if (!TABS.includes(tab)) return null;
      delete frappe.route_options.tab;
      return tab;
    }

    async show(tab) {
      if (!TABS.includes(tab)) return;
      const token = ++this.navigationToken;
      try {
        await this.assetsReady;
        if (token !== this.navigationToken) return;
        if (!this.controllers[tab]) {
          const source = this.controllers[tab === 'summary' ? 'analysis' : 'summary'];
          if (source) await source.filtersReady;
          if (token !== this.navigationToken) return;
          const initialFilters = source ? Object.fromEntries(['site', 'from_date', 'to_date'].map(name =>
            [name, source[name].get_value()])) : {};
          const Report = window.is_production.hourly_productivity[tab === 'summary' ? 'Summary' : 'Analysis'];
          this.controllers[tab] = new Report(null, {main: this.page.main.find(`#hp-panel-${tab}`), initialFilters,
            onOpenSummary: () => this.show('summary')});
        }
        this.activeTab = tab;
        for (const name of TABS) {
          this.page.main.find(`#hp-panel-${name}`).prop('hidden', name !== tab);
          this.page.main.find(`[data-tab=${name}]`).attr('aria-selected', String(name === tab)).attr('tabindex', name === tab ? '0' : '-1');
        }
        await this.controllers[tab].ready;
      } catch (error) {
        if (token !== this.navigationToken) return;
        this.page.main.find(`#hp-panel-${tab}`).prop('hidden', false).text('Unable to load report assets. Refresh the page to retry.');
      }
    }
  }
  if (typeof frappe !== 'undefined') {
    frappe.pages['hourly-productivity'].on_page_load = wrapper => {
      wrapper.hourlyProductivity = new HourlyProductivityPage(wrapper);
    };
    frappe.pages['hourly-productivity'].on_page_show = wrapper => {
      const host = wrapper.hourlyProductivity;
      const tab = host?.routeTab();
      if (tab) host.show(tab);
    };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {HourlyProductivityPage};
})();
