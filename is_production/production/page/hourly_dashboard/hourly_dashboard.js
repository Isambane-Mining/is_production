// Copyright (c) 2026, BuFf0k and contributors
// For license information, please see license.txt

frappe.pages["hourly-dashboard"].on_page_load = function (wrapper) {
  const REPORT_NAME = "Hourly Dashboard";
  const SITE_COLOUR_METHOD =
    "is_production.production.doctype.production_dashboard_setup.production_dashboard_setup.get_site_colour_map";

  const SLOT_LABELS = [
    "06-07", "07-08", "08-09", "09-10", "10-11", "11-12", "12-13",
    "13-14", "14-15", "15-16", "16-17", "17-18",
    "18-19", "19-20", "20-21", "21-22", "22-23", "23-24",
    "24-01", "01-02", "02-03", "03-04", "04-05", "05-06"
  ];

  const page = frappe.ui.make_app_page({
    parent: wrapper,
    title: "Hourly Dashboard",
    single_column: true
  });

  const OPERATIONAL_DAY_METHOD =
    "is_production.production.report.hourly_dashboard.hourly_dashboard.get_operational_day";
  let _setting_date = false;
  let _live_day = null;
  let _request_id = 0;
  const production_date = page.add_field({
    fieldtype: "Date",
    label: __("Production Date"),
    fieldname: "production_date",
    change: () => {
      if (!_setting_date) load_and_render(false);
    }
  });

  // -------------------------
  // Runtime site colour mapping
  // Source: Production Dashboard Setup singleton via whitelisted method
  // -------------------------
  let _site_colour_map = null;

  function get_site_colour_map() {
    if (_site_colour_map) {
      return Promise.resolve(_site_colour_map);
    }

    return frappe.call({
      method: SITE_COLOUR_METHOD,
      freeze: false
    })
      .then((r) => {
        _site_colour_map = r.message || {};
        return _site_colour_map;
      })
      .catch((e) => {
        console.error("Could not load Production Dashboard Setup site colours:", e);
        _site_colour_map = {};
        return _site_colour_map;
      });
  }

  function clear_site_colour_cache() {
    _site_colour_map = null;
  }

  // -------------------------
  // Dashboard container
  // -------------------------
  const $wrap = $(`<div class="isd-dashboard isd-dashboard--hourly"></div>`).appendTo(page.main);
  const $status = $(`<div class="isd-dashboard-status text-muted"></div>`).appendTo($wrap);
  const $dash = $(`<div class="isd-dashboard-grid"></div>`).appendTo($wrap);

  // -------------------------
  // Refresh scheduler (:10 and :30)
  // -------------------------
  let _auto_refresh_started = false;
  let _refreshing = false;
  let _timer = null;

  function ms_until_next_refresh() {
    const now = new Date();
    const minutes = now.getMinutes();
    const seconds = now.getSeconds();
    const ms = now.getMilliseconds();

    let nextMinute;

    if (minutes < 10) {
      nextMinute = 10;
    } else if (minutes < 30) {
      nextMinute = 30;
    } else {
      nextMinute = 70; // next hour + 10
    }

    return (nextMinute - minutes) * 60 * 1000 - seconds * 1000 - ms;
  }

  function clear_existing_timer() {
    if (_timer) {
      clearTimeout(_timer);
      _timer = null;
    }
  }

  function start_aligned_refresh() {
    if (_auto_refresh_started) {
      return;
    }

    _auto_refresh_started = true;

    const schedule_next = () => {
      clear_existing_timer();
      _timer = setTimeout(auto_refresh, ms_until_next_refresh());
    };

    const auto_refresh = () => {
      if (_refreshing) {
        schedule_next();
        return;
      }

      _refreshing = true;

      load_and_render(true)
        .finally(() => {
          _refreshing = false;
          schedule_next();
        });
    };

    schedule_next();
  }

  // -------------------------
  // Report runner
  // -------------------------
  function run_report(filters) {
    return frappe.call({
      method: "frappe.desk.query_report.run",
      args: {
        report_name: REPORT_NAME,
        filters: filters
      },
      freeze: false
    });
  }

  // -------------------------
  // Helpers
  // -------------------------
  function escape_html(value) {
    return frappe.utils.escape_html(value == null ? "" : String(value));
  }

  function slot_field(slotNumber) {
    return `slot_${String(slotNumber).padStart(2, "0")}`;
  }

  function normalise_int(value) {
    const parsed = parseInt(value, 10);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function get_header_background_style(site, siteColourMap) {
    const mappedColour = siteColourMap && siteColourMap[site]
      ? String(siteColourMap[site]).trim()
      : "";

    if (!mappedColour) {
      return "background:transparent;";
    }

    return `background:${mappedColour};`;
  }

  function get_cell_display(value) {
    value = normalise_int(value);

    if (value === 0) {
      return {
        css_class: "",
        display: "",
        title: ""
      };
    }

    if (value >= 1 && value <= 199) {
      return {
        css_class: "isd-dashboard-cell-low",
        display: String(value),
        title: `${value} bcm`
      };
    }

    if (value >= 200 && value <= 219) {
      return {
        css_class: "isd-dashboard-cell-medium",
        display: String(value),
        title: `${value} bcm`
      };
    }

    return {
      css_class: "isd-dashboard-cell-high",
      display: String(value),
      title: `${value} bcm`
    };
  }

  function extract_rows_from_response(res) {
    const payload = res && res.message ? res.message : res;

    if (!payload) {
      return [];
    }

    if (Array.isArray(payload.result)) {
      return payload.result;
    }

    if (Array.isArray(payload.data)) {
      return payload.data;
    }

    if (Array.isArray(payload)) {
      return payload;
    }

    return [];
  }

  function group_rows_by_site(rows) {
    const siteMap = new Map();

    rows.forEach((row) => {
      const site = String(row.site || "").trim();

      if (!site) {
        return;
      }

      if (!siteMap.has(site)) {
        siteMap.set(site, {
          site: site,
          site_order: normalise_int(row.site_order),
          production_day: row.production_day || "",
          rows: []
        });
      }

      const group = siteMap.get(site);

      if (!group.production_day && row.production_day) {
        group.production_day = row.production_day;
      }

      if (!row.is_empty_site) {
        group.rows.push(row);
      }
    });

    return Array.from(siteMap.values()).sort((a, b) => {
      if (a.site_order !== b.site_order) {
        return a.site_order - b.site_order;
      }

      return a.site.localeCompare(b.site);
    });
  }

  // -------------------------
  // Render helpers
  // -------------------------
  function render_hour_header() {
    const hourHeaders = SLOT_LABELS.map((label) => {
      const escapedLabel = escape_html(label);
      const displayLabel = escapedLabel.replace("-", "<br>");

      return `
        <th class="isd-dashboard-th" title="${escapedLabel}">
          ${displayLabel}
        </th>
      `;
    }).join("");

    return `
      <tr>
        <th class="isd-dashboard-th">Excavator</th>
        ${hourHeaders}
      </tr>
    `;
  }

  function render_excavator_row(row) {
    const excavator = escape_html(row.excavator || "");

    const cells = [
      `<td class="isd-dashboard-td isd-dashboard-excavator" title="${excavator}">${excavator}</td>`
    ];

    for (let slot = 1; slot <= 24; slot++) {
      const value = normalise_int(row[slot_field(slot)]);
      const cell = get_cell_display(value);
      const titleAttr = cell.title ? ` title="${escape_html(cell.title)}"` : "";
      const cssClass = cell.css_class ? ` ${cell.css_class}` : "";

      cells.push(
        `<td class="isd-dashboard-td${cssClass}"${titleAttr}>${escape_html(cell.display)}</td>`
      );
    }

    return `<tr>${cells.join("")}</tr>`;
  }

  function render_site_block(group, siteColourMap) {
    const rowsHtml = group.rows.map(render_excavator_row).join("");
    const headerBackground = get_header_background_style(group.site, siteColourMap);

    return `
      <div class="isd-dashboard-card">
        <div class="isd-dashboard-card-header" style="${headerBackground}">
          <div class="isd-dashboard-card-title">Site: ${escape_html(group.site)}</div>
          <div class="isd-dashboard-card-subtitle">
            Production Day: ${escape_html(group.production_day)}
          </div>
        </div>

        <div class="isd-dashboard-table-wrap">
          <table class="isd-dashboard-table isd-dashboard-table--hourly">
            ${render_hour_header()}
            ${rowsHtml}
          </table>
        </div>
      </div>
    `;
  }

  function render_dashboard(rows, siteColourMap) {
    if (!rows.length) {
      $dash.html(`<div class="text-muted">No data for the selected filter.</div>`);
      return;
    }

    const groupedSites = group_rows_by_site(rows);

    if (!groupedSites.length) {
      $dash.html(`<div class="text-muted">No sites found for the selected filter.</div>`);
      return;
    }

    $dash.html(
      groupedSites.map((group) => render_site_block(group, siteColourMap)).join("")
    );
  }

  // -------------------------
  // Main loader
  // -------------------------
  async function load_and_render(is_auto) {
    const request_id = ++_request_id;
    const selected_date = production_date.get_value();
    $status.text(is_auto ? "Refreshing..." : "Loading...");

    try {
      const response = await frappe.call({ method: OPERATIONAL_DAY_METHOD, freeze: false });
      if (request_id !== _request_id) return;
      const current_day = response.message;
      const date = !selected_date || (is_auto && selected_date === _live_day)
        ? current_day : selected_date;
      _live_day = current_day;
      if (production_date.get_value() !== date) {
        _setting_date = true;
        try {
          await production_date.set_value(date);
        } finally {
          _setting_date = false;
        }
      }
      const [res, siteColourMap] = await Promise.all([
        run_report({ production_date: date }),
        get_site_colour_map()
      ]);
      if (request_id !== _request_id) return;
      render_dashboard(extract_rows_from_response(res), siteColourMap);

      const time = new Date().toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit"
      });
      $status.text(`Last updated: ${time} (refreshes at :10 and :30)`);
      if (is_auto) {
        frappe.show_alert({
          message: `Hourly Dashboard updated at ${time}`,
          indicator: "green"
        }, 5);
      }
    } catch (e) {
      if (request_id !== _request_id) return;
      console.error(e);
      $status.text("Error loading dashboard data.");
      $dash.html(`<div class="text-danger">Could not load data. Check console / server logs.</div>`);
    }
  }

  // Default date comes from the site's clock, independent of browser timezone.
  load_and_render(false);
  start_aligned_refresh();

  // -------------------------
  // Cleanup
  // -------------------------
  frappe.pages["hourly-dashboard"].on_page_unload = function () {
    clear_existing_timer();
    _auto_refresh_started = false;
    _refreshing = false;
  };
};
