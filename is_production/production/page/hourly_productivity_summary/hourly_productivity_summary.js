/* Preserve the standalone Summary route; its view is also reused by the tabbed page. */
(() => {
  if (typeof module !== 'undefined' && module.exports && typeof require === 'function') {
    module.exports = require('../../../public/js/hourly_productivity_summary_view.js');
    return;
  }
  if (typeof frappe === 'undefined') return;
  frappe.pages['hourly-productivity-summary'].on_page_load = wrapper => {
    const page = frappe.ui.make_app_page({parent:wrapper,title:'Hourly Productivity Summary',single_column:true});
    const mount = () => {
      wrapper.hourlyProductivitySummary = new frappe.hourlyProductivitySummaryView.HourlyProductivitySummaryPage(wrapper,page);
    };
    if (frappe.hourlyProductivitySummaryView) {mount();return;}
    page.main.html('<div class="hps-message">Loading Hourly Productivity Summary…</div>');
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
    frappe.hourlySummaryViewLoading.then(mount).catch(() => {
      page.main.html('<div class="hps-message">Could not load Hourly Productivity Summary. Refresh this page to retry.</div>');
    });
  };
})();
