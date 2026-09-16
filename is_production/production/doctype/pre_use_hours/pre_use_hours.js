// Copyright (c) 2026, BuFf0k and contributors
// For license information, please see license.txt

// listen for server-side “logs” and print them to the browser console
frappe.realtime.on("preuse:update_log", (data) => {
  console.log("%c[PreUseHours]", "color:teal;font-weight:bold;", data.msg);
});

// Category order definition
const CATEGORY_ORDER = [
  "Excavator",
  "ADT",
  "Dozer",
  "Water Bowser",
  "Diesel Bowsers",
  "Service Truck",
  "Grader",
  "TLB",
  "Loader",
  "Drills"
];

// Sort helper (display by Asset.asset_name when available)
const PRE_USE_EXCLUDED_MACHINES = new Set([
  "426-492",
  "426-492GWAB",
  "IS127",
  "IS127GWAB",
  "IS0126KLP",
  "IS077KLP"
]);

function is_pre_use_excluded(frm, asset) {
  const assetName = String(
    (asset && asset.name) || ""
  ).trim().toUpperCase().replace(/\s+/g, "");

  const plantNo = String(
    (asset && asset.asset_name) || ""
  ).trim().toUpperCase().replace(/\s+/g, "");

  return (
    PRE_USE_EXCLUDED_MACHINES.has(assetName) ||
    PRE_USE_EXCLUDED_MACHINES.has(plantNo)
  );
}

function remove_pre_use_excluded_rows(frm) {
  if (!frm || !frm.doc) return false;

  const rows = frm.doc.pre_use_assets || [];

  if (!rows.length) return false;

  const originalCount = rows.length;

  frm.doc.pre_use_assets = rows.filter((row) => {
    const asset = {
      name: row.asset_name || "",
      asset_name: row.plant_no || row.asset_name || ""
    };

    return !is_pre_use_excluded(frm, asset);
  });

  const removed =
    originalCount - frm.doc.pre_use_assets.length;

  if (removed > 0) {
    frm.refresh_field("pre_use_assets");

    console.log(
      `[PreUseHours] Removed ${removed} excluded machine row(s).`
    );

    return true;
  }

  return false;
}



function sort_assets(list) {
  return (list || []).slice().sort((a, b) => {
    return (a.asset_name || a.name).localeCompare(b.asset_name || b.name);
  });
}

/**
 * Build lookup maps for the Asset list
 * - byName: Asset.name -> asset
 * - byCode: Asset.asset_name (Plant No) -> asset
 */
function build_asset_maps(assets) {
  const byName = {};
  const byCode = {};
  (assets || []).forEach((a) => {
    if (a.name) byName[a.name] = a;
    if (a.asset_name) byCode[a.asset_name] = a;
  });
  return { byName, byCode };
}

/**
 * Migration-safe normalize:
 * If existing child rows have stored Plant No (Asset.asset_name) inside the Link field,
 * convert them to Asset.name so link validation passes and everything stays consistent.
 */
function normalize_child_rows(frm, assets) {
  const { byName, byCode } = build_asset_maps(assets);

  (frm.doc.pre_use_assets || []).forEach((row) => {
    if (!row.asset_name) return;

    // already correct (Asset.name)
    if (byName[row.asset_name]) {
      const a = byName[row.asset_name];
      if (row.plant_no !== undefined) row.plant_no = a.asset_name || "";
      row.item_name = a.item_name;
      row.asset_category = a.asset_category;
      return;
    }

    // stored Plant No/code -> convert to Asset.name
    if (byCode[row.asset_name]) {
      const a = byCode[row.asset_name];
      row.asset_name = a.name; // ✅ store real Asset.name in Link
      if (row.plant_no !== undefined) row.plant_no = a.asset_name || "";
      row.item_name = a.item_name;
      row.asset_category = a.asset_category;
    }
  });
}

/**
 * Dynamically set Shift options based on the shift system
 */
function update_shift_options(frm, shift_system) {
  const shift_options = {
    "3x8Hour": ["Morning", "Afternoon", "Night"],
    "2x12Hour": ["Day", "Night"]
  };
  frm.set_df_property("shift", "options", shift_options[shift_system] || []);
}

/**
 * Fetch relevant assets for the selected location (initial fill)
 */
function fetch_assets(frm) {
  frappe.call({
    method: "frappe.client.get_list",
    args: {
      doctype: "Asset",
      filters: {
        location: frm.doc.location,
        asset_category: ["in", CATEGORY_ORDER],
        docstatus: 1
      },
      fields: ["name", "asset_name", "item_name", "asset_category"],
      limit_page_length: 1000
    },
    callback: function (response) {
      const assets = sort_assets((response.message || []).filter((asset) => !is_pre_use_excluded(frm, asset)));

      frm.clear_table("pre_use_assets");
      assets.forEach((asset) => {
        const row = frm.add_child("pre_use_assets");

        // ✅ Link field MUST store Asset.name
        row.asset_name = asset.name;

        // ✅ Display field (only if you've added it)
        if (row.plant_no !== undefined) row.plant_no = asset.asset_name || "";

        row.item_name = asset.item_name;
        row.asset_category = asset.asset_category;
      });

      frm.refresh_field("pre_use_assets");
    }
  });
}

/**
 * Fetch the shift system from Monthly Production Planning
 */
function fetch_shift_system(frm) {
  if (!frm.doc.location || !frm.doc.shift_date) return;

  frappe.call({
    method: "frappe.client.get_list",
    args: {
      doctype: "Monthly Production Planning",
      filters: {
        location: frm.doc.location,
        prod_month_start_date: ["<=", frm.doc.shift_date],
        prod_month_end_date: [">=", frm.doc.shift_date],
        site_status: "Producing"
      },
      fields: ["name", "prod_month_start_date", "prod_month_end_date", "shift_system"],
      limit_page_length: 1
    },
    callback: function (response) {
      if (response.message && response.message.length) {
        update_shift_options(frm, response.message[0].shift_system);
      }
    }
  });
}

/**
 * Render HTML field correctly.
 * In v16, HTML fields don't reliably auto-bind the stored value to the wrapper.
 * This forces the wrapper content from the doc value (works for historic docs too).
 */
function render_integrity_summary(frm) {
  const fname = "data_integrity_summary";
  const field = frm.fields_dict && frm.fields_dict[fname];

  if (!field || !field.$wrapper) return;

  // `data_integrity_summary` may be HTML field (preferred) or text; either way we render.
  field.$wrapper.html(frm.doc[fname] || "");
}

frappe.ui.form.on("Pre-Use Hours", {
  refresh: function (frm) {
    remove_pre_use_excluded_rows(frm);
    frm.add_custom_button(
      __("🔄 Refresh Machines"),
      () => frm.trigger("refresh_machines_from_assets"),
      __("Actions")
    );

    render_integrity_summary(frm);
  },

  onload_post_render: function (frm) {
    remove_pre_use_excluded_rows(frm);
    render_integrity_summary(frm);
  },

  after_save: function (frm) {
    render_integrity_summary(frm);
  },

  location: function (frm) {
    if (frm.doc.location) fetch_assets(frm);
  },

  shift_date: function (frm) {
    fetch_shift_system(frm);
  },

  /**
   * Sync machines list to submitted Assets at this location
   * - keeps existing rows (doesn't wipe entered hours)
   * - adds new assets
   * - removes assets no longer in list
   */
  refresh_machines_from_assets: function (frm) {
    if (!frm.doc.location) {
      frappe.msgprint(__("Please select a location first."));
      return;
    }

    frappe.call({
      method: "frappe.client.get_list",
      args: {
        doctype: "Asset",
        filters: {
          location: frm.doc.location,
          asset_category: ["in", CATEGORY_ORDER],
          docstatus: 1
        },
        fields: ["name", "asset_name", "item_name", "asset_category"],
        limit_page_length: 1000
      },
      callback: function (r) {
        const assets = sort_assets((r.message || []).filter((asset) => !is_pre_use_excluded(frm, asset)));
        const { byName } = build_asset_maps(assets);

        // 0) Normalize existing rows (handles any legacy rows that stored codes)
        normalize_child_rows(frm, assets);

        // 1) Track which Asset.name values we already have in the table
        const keepNames = new Set();
        (frm.doc.pre_use_assets || []).forEach((row) => {
          if (!row.asset_name) return;
          if (byName[row.asset_name]) {
            const a = byName[row.asset_name];
            row.item_name = a.item_name;
            row.asset_category = a.asset_category;
            if (row.plant_no !== undefined) row.plant_no = a.asset_name || "";
            keepNames.add(a.name);
          }
        });

        // 2) Add missing assets
        assets.forEach((asset) => {
          if (!keepNames.has(asset.name)) {
            const row = frm.add_child("pre_use_assets");
            row.asset_name = asset.name; // ✅ store Asset.name
            if (row.plant_no !== undefined) row.plant_no = asset.asset_name || "";
            row.item_name = asset.item_name;
            row.asset_category = asset.asset_category;
          }
        });

        // 3) Remove rows not in Asset list anymore
        frm.doc.pre_use_assets = (frm.doc.pre_use_assets || []).filter(
          (row) => row.asset_name && byName[row.asset_name]
        );

        frm.refresh_field("pre_use_assets");
        frappe.msgprint(__("✅ Machines list synchronized with Asset list (added/removed only)."));

        // Re-render summary (in case server updated it during save/refresh)
        render_integrity_summary(frm);
      }
    });
  }
});
