/* Bookmark compatibility: the report now lives in the canonical two-tab page. */
(() => {
  const route = 'hourly-productivity-analysis';
  function openCombined() {
    if (frappe.get_route()[0] !== route) return;
    frappe.route_options = {...(frappe.route_options || {}), tab: 'analysis'};
    return frappe.set_route('hourly-productivity');
  }
  frappe.pages[route].on_page_load = openCombined;
  frappe.pages[route].on_page_show = openCombined;
})();
