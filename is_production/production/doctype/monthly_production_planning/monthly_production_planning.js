// monthly_production_planning.js
// Combined functionality: existing Monthly Production Planning logic + drag-and-drop Excavator Teams & Trucks

// helper for logging errors without breaking your flow
function logError(section, err) {
  console.error(`Error in ${section}:`, err);
}

console.log("🛠 Monthly Production Planning JS loaded");
console.log("📦 Custom JS: monthly_production_planning.js loaded — version 2025-06-16 20:34");



function calculateGeoRefDescription(frm, cdt, cdn) {
  const row  = locals[cdt][cdn];

  const desc = row.geo_mat_type_description || '';
  const rows = frm.doc.geo_mat_layer || [];
  const idx  = rows.findIndex(r => r.name === row.name) + 1;
  const combined = `${idx} - ${desc}`;
  frappe.model.set_value(cdt, cdn, 'geo_ref_description', combined);
}


// ──────────────────────────────────────────────────────────────────────────────
//                      Now start the actual event‐handler object
// ──────────────────────────────────────────────────────────────────────────────


// MPP_SAFE_MACHINE_ROLE_CONTROL
var MPP_MACHINE_MANAGER_ROLES = [
  "Production Area Manager",
  "Engineering Area Manager",
  "Information Officer"
];

function canManageMonthlyPlanningMachines() {
  const roles = Array.isArray(frappe.user_roles)
    ? frappe.user_roles
    : [];

  return MPP_MACHINE_MANAGER_ROLES.some(role =>
    roles.includes(role)
  );
}

function showMachinePlanningPermissionMessage() {
  frappe.msgprint({
    title: __("Permission Required"),
    indicator: "orange",
    message: __(
      "Only Production Area Manager, Engineering Area Manager or Information Officer may change Excavator, ADT or Dozer allocations."
    )
  });
}

function applySafeMachinePlanningReadOnly(frm) {
  if (canManageMonthlyPlanningMachines()) {
    return;
  }

  [
    "dnd_html_truck_ui",
    "dnd_dozer_assigned"
  ].forEach(fieldname => {
    const field = frm.get_field(fieldname);

    if (!field || !field.$wrapper) {
      return;
    }

    const wrapper = field.$wrapper;

    // Hide only controls belonging to the custom machine UI.
    wrapper
      .find(
        "#add-excavators, " +
        "#add-trucks, " +
        "#add-dozers, " +
        "button.btn-danger"
      )
      .hide();

    // Dozing Type remains visible but cannot be changed.
    wrapper
      .find("select")
      .prop("disabled", true);

    // Drag functions are separately blocked below.
    wrapper
      .find(".truck-item, .excavator-item, .dozer-item")
      .css({
        cursor: "default",
        "user-select": "text"
      });
  });
}


frappe.ui.form.on('Monthly Production Planning', {

  setup(frm) {
    // hide the native child-tables
    //frm.get_field('excavator_truck_assignments').$wrapper.hide();
    //frm.get_field('dozer_table').$wrapper.hide();

    frm.set_df_property('dozer_table', 'cannot_delete', false);
    frm.set_query('excavator', 'excavator_truck_assignments', function () {
      return {
        filters: {
          asset_category: 'Excavator',
          location: frm.doc.location
        }
      };
    });

    frm.set_query('truck', 'excavator_truck_assignments', function () {
      return {
        filters: {
          asset_category: 'ADT',
          location: frm.doc.location
        }
      };
    });
  },

  // Section 0: Before Save
  before_save(frm) {
    try {
      // ✅ Keep this: Patch hourly_production_reference for each day
      frm.doc.month_prod_days?.forEach(r => {
        r.hourly_production_reference = `${frm.doc.name}-${r.shift_start_date}`;
      });
      frm.refresh_field('month_prod_days');

    } catch (e) {
      logError('before_save', e);
    }
    
        if (!frm.doc.prod_trucks) {
            frm.doc.prod_trucks = [];
        }
    
  },

  // Section 1: Refresh

 refresh(frm) {
  try {
    console.log("🌀 Refresh triggered");
    console.log("📍 Location is:", frm.doc.location);

    // ─────────────────────────────────────────────
    // Hide native Monthly Production Days child table
    // Users will interact ONLY with the HTML grid (month_prod_days_field)
    // ─────────────────────────────────────────────
    if (frm.fields_dict.month_prod_days && frm.fields_dict.month_prod_days.$wrapper) {
      frm.fields_dict.month_prod_days.$wrapper.hide();
    }

// ─────────────────────────────────────────────
// Default Production Adjustment Factor
// ─────────────────────────────────────────────
if (frm.doc.prod_adjust_factor == null) {
  frm.set_value('prod_adjust_factor', 1);
}

    // on every refresh, re-enable the delete icon in the grid
    if (frm.fields_dict.dozer_table && frm.fields_dict.dozer_table.grid) {
      // allow the grid to delete rows
      frm.fields_dict.dozer_table.grid.can_delete = true;
      // show the little “trash” icon in each row
      frm.fields_dict.dozer_table.grid.wrapper
        .find('.grid-delete-row, .grid-remove-rows, .grid-delete')
        .show();
    }

    // Custom buttons
    frm.add_custom_button(
      __('Populate Monthly Production Days'),
      () => frm.trigger('populate_monthly_prod_days'),
      __('Actions')
    );
    frm.add_custom_button(
      __('Clear Production Days'),
      () => frm.trigger('clear_production_days'),
      __('Actions')
    );
    // Add refresh machines button (will appear in form header under "Site Details and Plant")
    frm.add_custom_button(
      __('🔄 Refresh Machines'),
      () => frm.trigger('refresh_machines_from_assets'),
      __('Site Details and Plant')
    );

    // Calculate geo layer descriptions
    (frm.doc.geo_mat_layer || []).forEach(row => {
      calculateGeoRefDescription(frm, row.doctype, row.name);
    });

    // ── Update equipment counts and volume fields ──
    frm.trigger('update_equipment_counts');
    frm.trigger('update_ts_planned_volumes');
    frm.trigger('update_planned_dozer_volumes');

    // Render drag-and-drop UI if location exists
    if (frm.doc.location) {
      renderTruckAssignmentUI(frm);
      renderDozerAssignmentUI(frm);
    } else {
      frm.get_field('dnd_html_truck_ui').$wrapper.html('<p>Please select a location first.</p>');
      frm.get_field('dnd_dozer_assigned').$wrapper.html('<p>Please select a location first.</p>');
    }

    // ── Requirement 1: dynamically populate geo_description options ──
    const opts = (frm.doc.geo_mat_layer || [])
      .map(r => r.geo_ref_description)
      .join('\n');

    // tell the Blasting Plan child‐table’s grid to use these as the select options
    frm.fields_dict.blasting_plan.grid.update_docfield_property(
      'geo_description', 'options', opts
    );
  renderMonthProdDaysHTML(frm);
  } catch (e) {
    logError('refresh', e);
  }
},

  // ────────────────────────────────────────────────────────────────────
  // Volume recalculation triggers
  // 1) TS volumes when either input changes
  total_month_prod_hours: frm => frm.trigger('update_ts_planned_volumes'),
  ts_tempo:               frm => frm.trigger('update_ts_planned_volumes'),

  // 2) Dozer volumes whenever a Blasting Plan row is added or removed
  blasting_plan_add:    frm => frm.trigger('update_planned_dozer_volumes'),
  blasting_plan_remove: frm => frm.trigger('update_planned_dozer_volumes'),

  // 3) Keep your location handler
  location(frm) {
    console.log("🧭 Location changed:", frm.doc.location);

    if (!frm.doc.location) {
      return;
    }

    frm.refresh();

    if (!frm.is_new() || (frm.doc.tub_factors || []).length) {
      return;
    }

    frappe.call({
      method: "is_production.production.doctype.monthly_production_planning.monthly_production_planning.get_previous_tub_factor_rows",
      args: {
        location: frm.doc.location
      },
      freeze: true,
      freeze_message: __("Loading previous Tub Factors..."),
      callback(r) {
        const rows = r.message || [];

        rows.forEach(values => {
          const row = frm.add_child("tub_factors");
          row.tub_factor = values.tub_factor;
          row.item_name = values.item_name;
          row.mat_type = values.mat_type;
          row.factor_value = values.factor_value;
        });

        frm.refresh_field("tub_factors");

        if (rows.length) {
          frappe.show_alert({
            message: __(` previous Tub Factor row(s) loaded.`),
            indicator: "green"
          }, 5);
        }
      }
    });
  },
 // Refresh Machines synchronises site membership without resetting allocations.
async refresh_machines_from_assets(frm) {
  if (!canManageMonthlyPlanningMachines()) {
    showMachinePlanningPermissionMessage();
    return;
  }
  if (!frm.doc.location) {
    frappe.msgprint(__('Please select a location first.'));
    return;
  }

  const location = frm.doc.location;
  const doc = frm.doc;
  const assets = [];
  // Fetch every page before removing anything: a truncated list is not site membership.
  for (let start = 0; ; start += 500) {
    const r = await frappe.call({
      method: "frappe.client.get_list",
      args: {
        doctype: "Asset",
        filters: {
          location,
          docstatus: 1,
          asset_category: ["in", ["Excavator", "ADT", "Dozer"]]
        },
        fields: ["name", "item_name", "asset_category"],
        order_by: "name asc",
        limit_start: start,
        limit_page_length: 500
      }
    });
    if (r.exc || !Array.isArray(r.message)) {
      return;
    }
    assets.push(...r.message);
    if (r.message.length < 500) break;
  }

  // Do not apply a response to a different site/document or after access changed.
  if (frm.doc !== doc || frm.doc.location !== location) return;
  if (!canManageMonthlyPlanningMachines()) {
    showMachinePlanningPermissionMessage();
    return;
  }

  const excavators = new Set(assets.filter(a => a.asset_category === "Excavator").map(a => a.name));
  const trucks = new Set(assets.filter(a => a.asset_category === "ADT").map(a => a.name));
  const dozers = new Set(assets.filter(a => a.asset_category === "Dozer").map(a => a.name));
  const existing = new Set();
  const productionExcavators = new Set(getActiveEmptyExcavators(frm));
  (doc.excavator_truck_assignments || []).forEach(row => {
    if (row.excavator) existing.add(row.excavator);
    if (row.truck) existing.add(row.truck);
    if (row.excavator && row.truck) productionExcavators.add(row.excavator);
  });
  (doc.dozer_table || []).forEach(row => {
    if (row.asset_name) existing.add(row.asset_name);
  });
  getActiveEmptyExcavators(frm).forEach(id => existing.add(id));

  let changed = false;
  function removeRow(row) {
    frappe.model.clear_doc(row.doctype, row.name);
    changed = true;
  }

  doc.excavator_truck_assignments = (doc.excavator_truck_assignments || []).filter(row => {
    // Keep the onsite half of an invalid pair, with its row identity and planning data.
    if (row.excavator && !excavators.has(row.excavator)) {
      row.excavator = null;
      row.excavator_model = null;
      changed = true;
    }
    if (row.truck && !trucks.has(row.truck)) {
      row.truck = null;
      row.truck_model = null;
      changed = true;
    }
    if (row.excavator || row.truck) return true;
    removeRow(row);
    return false;
  });
  doc.dozer_table = (doc.dozer_table || []).filter(row => {
    if (dozers.has(row.asset_name)) return true;
    removeRow(row);
    return false;
  });

  const activeEmpty = getActiveEmptyExcavators(frm).filter(id => excavators.has(id));
  productionExcavators.forEach(id => {
    if (excavators.has(id) && !activeEmpty.includes(id) &&
        !doc.excavator_truck_assignments.some(row => row.excavator === id && row.truck)) {
      activeEmpty.push(id);
    }
  });
  if (JSON.stringify(activeEmpty) !== JSON.stringify(getActiveEmptyExcavators(frm))) {
    setActiveEmptyExcavators(frm, activeEmpty);
    changed = true;
  }

  // Add only missing machines; never attach a new ADT or assign a new dozer.
  assets.forEach(asset => {
    if (asset.asset_category === "Dozer") {
      if (doc.dozer_table.some(row => row.asset_name === asset.name)) return;
      const row = frm.add_child("dozer_table");
      row.asset_name = asset.name;
      row.item_name = asset.item_name;
      row.dozing_type = '';
    } else {
      const isExcavator = asset.asset_category === "Excavator";
      const key = isExcavator ? "excavator" : "truck";
      if (doc.excavator_truck_assignments.some(row => row[key] === asset.name)) return;
      const row = frm.add_child("excavator_truck_assignments");
      row[key] = asset.name;
      row[`${key}_model`] = asset.item_name;
      row[isExcavator ? "truck" : "excavator"] = null;
    }
    changed = true;
  });

  if (changed) frm.dirty();
  frm.refresh_field("excavator_truck_assignments");
  frm.refresh_field("dozer_table");
  await frm.trigger('update_equipment_counts');
  renderTruckAssignmentUI(frm);
  renderDozerAssignmentUI(frm);

  const onsite = new Set(assets.map(a => a.name));
  const added = [...onsite].filter(id => !existing.has(id)).length;
  const removed = [...existing].filter(id => !onsite.has(id)).length;
  const preserved = [...existing].filter(id => onsite.has(id)).length;
  frappe.msgprint(__("Machines synchronised: {0} added, {1} removed, {2} preserved.",
    [added, removed, preserved]));
},


  // Section 2: Populate Monthly Production Days


  populate_monthly_prod_days(frm) {
    try {
      if (!frm.doc.prod_month_start_date || !frm.doc.prod_month_end_date) {
        frappe.msgprint(__('Please select production start and end dates.'));
        return;
      }

      if (
        [frm.doc.weekday_shift_hours, frm.doc.saturday_shift_hours, frm.doc.num_sat_shifts]
          .some(v => v == null || v === '')
      ) {
        frappe.msgprint(__('Please enter shift hours and number of Saturday shifts.'));
        return;
      }

      const start = frappe.datetime.str_to_obj(frm.doc.prod_month_start_date);
      const end = frappe.datetime.str_to_obj(frm.doc.prod_month_end_date);

      if (end < start) {
        frappe.msgprint(__('Production Month End Date cannot be before Production Month Start Date.'));
        return;
      }

      frm.clear_table('month_prod_days');

      const weekdayHours = flt(frm.doc.weekday_shift_hours);
      const saturdayHours = flt(frm.doc.saturday_shift_hours);
      const saturdayShifts = cint(frm.doc.num_sat_shifts);

      for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
        const dow = d.toLocaleDateString('en-US', { weekday: 'long' });

        let dayHours = 0;
        let nightHours = 0;

        // Control must use Day/Night only for 2x12Hour.
        if (dow === 'Saturday') {
          dayHours = saturdayHours;
          nightHours = saturdayShifts > 1 ? saturdayHours : 0;
        } else if (dow !== 'Sunday') {
          dayHours = weekdayHours;
          nightHours = weekdayHours;
        }

        const row = frm.add_child('month_prod_days');

        row.shift_start_date = frappe.datetime.obj_to_str(d);
        row.day_week = dow;

        row.shift_day_hours = dayHours;
        row.shift_night_hours = nightHours;

        // Keep Morning/Afternoon zero for 2x12Hour.
        row.shift_morning_hours = 0;
        row.shift_afternoon_hours = 0;

        row.production_excavators = frm.doc.num_excavators || 0;

        const totalShiftHours =
          flt(row.shift_day_hours) +
          flt(row.shift_night_hours);

        row.bcm_per_day =
          flt(row.production_excavators) *
          totalShiftHours *
          220;
      }

      frm.refresh_field('month_prod_days');

      calculateAndSetProductionStats(frm);
      frm.trigger('recalculate_totals');

      if (typeof renderProductionDaysUI === 'function') {
        renderProductionDaysUI(frm);
      }

      if (typeof recalc_pre_target === 'function') {
        recalc_pre_target(frm);
      }

      frappe.msgprint(__('Monthly Production Days populated with Day/Night shifts.'));
    } catch (e) {
      logError('populate_monthly_prod_days', e);
    }
  },



  // Section 4: Clear Production Days

  clear_production_days(frm) {
    try {
      frm.clear_table('month_prod_days');
      frm.refresh_field('month_prod_days');

      frm.set_value({
        tot_shift_day_hours: 0,
        tot_shift_night_hours: 0,
        tot_shift_morning_hours: 0,
        tot_shift_afternoon_hours: 0,
        total_month_prod_hours: 0,
        num_prod_days: 0,
        prod_days_completed: 0,
        month_prod_hours_completed: 0,
        month_remaining_production_days: 0,
        month_remaining_prod_hours: 0,
        target_bcm_day: 0,
        target_bcm_hour: 0
      });

      frm.refresh_fields([
        'tot_shift_day_hours',
        'tot_shift_night_hours',
        'tot_shift_morning_hours',
        'tot_shift_afternoon_hours',
        'total_month_prod_hours',
        'num_prod_days',
        'prod_days_completed',
        'month_prod_hours_completed',
        'month_remaining_production_days',
        'month_remaining_prod_hours',
        'target_bcm_day',
        'target_bcm_hour'
      ]);

      frappe.msgprint(__('Production Days cleared.'));
    } catch (e) {
      logError('clear_production_days', e);
    }
  },


  // Section 5: prod_month_end_date

  prod_month_end_date(frm) {
    try {
      // Naming uses Production Month End Date directly.
      // Do not force Production Month End Invoicing to month-end here.
      } catch (e) {
      logError('prod_month_end_date', e);
    }
  },


  // Section 6: recalculate_totals
  //monthly_target_bcm: frm => frm.trigger('recalculate_totals'),


  recalculate_totals(frm) {
    try {
      (frm.doc.month_prod_days || []).forEach(r => {
        const totalShiftHours =
          flt(r.shift_day_hours) +
          flt(r.shift_night_hours);

        if (!r.production_excavators) {
          r.production_excavators = frm.doc.num_excavators || 0;
        }

        r.bcm_per_day =
          flt(r.production_excavators) *
          totalShiftHours *
          220;
      });

      frm.refresh_field('month_prod_days');

      calculateAndSetProductionStats(frm);

      if (typeof renderProductionDaysUI === 'function') {
        renderProductionDaysUI(frm);
      }

      if (typeof recalc_pre_target === 'function') {
        recalc_pre_target(frm);
      }
    } catch (e) {
      logError('recalculate_totals', e);
    }
  },




  // Section 7: Update MTD Production


update_mtd_production(frm) {
    if (!frm.doc.name) {
        frappe.msgprint(__('Save the Monthly Production Planning document first.'));
        return;
    }

    frappe.call({
        method: 'is_production.production.doctype.monthly_production_planning.monthly_production_planning.update_mtd_production',
        args: {
            name: frm.doc.name
        },
        freeze: true,
        freeze_message: __('Updating Month to Date Production...'),

        callback: function(r) {
            if (r.message && r.message.status === 'success') {
                frm.reload_doc().then(() => {
                    frappe.show_alert({
                        message: __('Month to Date Production updated successfully.'),
                        indicator: 'green'
                    });
                });
            } else {
                frappe.msgprint(
                    __('Month to Date Production update failed. Check the Error Log.')
                );
            }
        },

        error: function() {
            frappe.msgprint(
                __('Month to Date Production update failed. Check the Error Log.')
            );
        }
    });
},


  // Section 8: Child Table Triggers
  //'Monthly Production Days': {
    //shift_day_hours:       frm => frm.trigger('recalculate_totals'),
    //shift_night_hours:     frm => frm.trigger('recalculate_totals'),
    //shift_morning_hours:   frm => frm.trigger('recalculate_totals'),
    //shift_afternoon_hours: frm => frm.trigger('recalculate_totals')
  //},

   // Section 9: Update Equipment Counts (with logging)
  
update_equipment_counts(frm) {
  try {
    const assignments = frm.doc.excavator_truck_assignments || [];

    const productionExcavators = new Set();

    // Excavators with assigned ADTs are production excavators.
    assignments.forEach(r => {
      if (r.excavator && r.truck) {
        productionExcavators.add(r.excavator);
      }
    });

    // Empty excavators that user dragged to Assigned side must still count.
    getActiveEmptyExcavators(frm).forEach(excavatorId => {
      productionExcavators.add(excavatorId);
    });

    const numExcavators = productionExcavators.size;

    // Only ADTs assigned to production excavators count.
    const numTrucks = assignments.filter(r => r.excavator && r.truck).length;

    // Any dozer with a selected dozing_type is a production dozer.
    const numDozers = (frm.doc.dozer_table || []).filter(r => !!r.dozing_type).length;

    console.log(`Equipment counts -> Excavators: ${numExcavators}, Trucks: ${numTrucks}, Dozers: ${numDozers}`);

    frm.set_value({
      num_excavators: numExcavators,
      num_trucks: numTrucks,
      num_dozers: numDozers
    });

    frm.refresh_fields(['num_excavators', 'num_trucks', 'num_dozers']);
  } catch (e) {
    logError('update_equipment_counts', e);
  }
},

  // Section X: Update TS Planned Volumes (with logging, using precomputed excavator count)
  update_ts_planned_volumes(frm) {
  try {
    // TS production now comes ONLY from Monthly Production Days
   const tsTotal = (frm.doc.month_prod_days || []).reduce(
  (sum, r) => sum + flt(r.bcm_per_day),
  0
);



    frm.set_value('total_ts_planned_volumes', tsTotal);

    // Recalculate pre_target whenever TS changes
    recalc_pre_target(frm);

  } catch (e) {
    logError('update_ts_planned_volumes', e);
  }
},


  // Section Y: Update Planned Dozer Volumes (with logging)
  update_planned_dozer_volumes(frm) {
  try {
    const dozerTotal = (frm.doc.blasting_plan || []).reduce(
      (sum, row) => sum + (row.block_dozing_bcms || 0),
      0
    );

    frm.set_value('planned_dozer_volumes', dozerTotal);

    // Recalculate pre_target whenever dozing changes
    recalc_pre_target(frm);

  } catch (e) {
    logError('update_planned_dozer_volumes', e);
  }
},

prod_adjust_factor: function(frm) {
  // When the production adjustment factor changes,
  // recalculate the pre-target value
  recalc_pre_target(frm);
},
 coal_tons_planned: function(frm) {
        // Calculate the derived fields when coal_tons_planned changes
        if (frm.doc.coal_tons_planned) {
            // Calculate coal_planned_bcm
            const coal_planned_bcm = frm.doc.coal_tons_planned / 1.5;
            
            // Calculate waste_bcms_planned (only if monthly_target_bcm exists)
            const waste_bcms_planned = frm.doc.monthly_target_bcm ? 
                frm.doc.monthly_target_bcm - coal_planned_bcm : 0;
            
            // Calculate planned_strip_ratio (avoid division by zero)
            const planned_strip_ratio = frm.doc.coal_tons_planned ? 
                waste_bcms_planned / frm.doc.coal_tons_planned : 0;
            
            // Set the values in the form
            frm.set_value('coal_planned_bcm', coal_planned_bcm);
            frm.set_value('waste_bcms_planned', waste_bcms_planned);
            frm.set_value('planned_strip_ratio', planned_strip_ratio);
        } else {
            // Clear the fields if coal_tons_planned is empty
            frm.set_value('coal_planned_bcm', 0);
            frm.set_value('waste_bcms_planned', 0);
            frm.set_value('planned_strip_ratio', 0);
        }
    },


    monthly_target_bcm: function(frm) {
        // Also recalculate when monthly_target_bcm changes if coal_tons_planned exists
        if (frm.doc.coal_tons_planned) {
            const coal_planned_bcm = frm.doc.coal_tons_planned / 1.5;
            const waste_bcms_planned = frm.doc.monthly_target_bcm ? 
                frm.doc.monthly_target_bcm - coal_planned_bcm : 0;
            const planned_strip_ratio = frm.doc.coal_tons_planned ? 
                waste_bcms_planned / frm.doc.coal_tons_planned : 0;
            
            frm.set_value('waste_bcms_planned', waste_bcms_planned);
            frm.set_value('planned_strip_ratio', planned_strip_ratio);
        }
        // ALSO trigger totals
        frm.trigger('recalculate_totals');
        
        console.log('📣 monthly_target_bcm changed — triggering recalc');
    }



});

//Child Table Triggers (diagnostic version)
frappe.ui.form.on('Monthly Production Days', {

  production_excavators: function (frm, cdt, cdn) {
    recalc_bcm_per_day(cdt, cdn);
    recalc_pre_target(frm);
    renderMonthProdDaysHTML(frm);

  },

  shift_day_hours: function (frm, cdt, cdn) {
    recalc_bcm_per_day(cdt, cdn);
    recalc_pre_target(frm);
    frm.trigger('recalculate_totals');
    renderMonthProdDaysHTML(frm);

  },

  shift_night_hours: function (frm, cdt, cdn) {
    recalc_bcm_per_day(cdt, cdn);
    recalc_pre_target(frm);
    frm.trigger('recalculate_totals');
    renderMonthProdDaysHTML(frm);

  },

  shift_morning_hours: function (frm, cdt, cdn) {
    frm.trigger('recalculate_totals');
    renderMonthProdDaysHTML(frm);

  },

  shift_afternoon_hours: function (frm, cdt, cdn) {
    frm.trigger('recalculate_totals');
    renderMonthProdDaysHTML(frm);

  }
});



// When either source field changes on a Geo_mat_layer row:
frappe.ui.form.on('Geo_mat_layer', {
  geo_mat_layer_ref: function(frm, cdt, cdn) {
    calculateGeoRefDescription(frm, cdt, cdn);
  },
  geo_mat_type_description: function(frm, cdt, cdn) {
    calculateGeoRefDescription(frm, cdt, cdn);
  }
});

// after the Monthly Production Planning block, add:

// ──────────────────────────────────────────────────────────────────────────────
//                    Blasting Plan child‐table event handlers (2-decimal precision)
// ──────────────────────────────────────────────────────────────────────────────
frappe.ui.form.on('Blasting Blocks Planned', {
  block_length: (frm, cdt, cdn) => {
    const row = locals[cdt][cdn];
    // 🔹 Force 2 decimal places immediately on entry
    frappe.model.set_value(cdt, cdn, 'block_length', flt(row.block_length, 2));
    recompute(frm, cdt, cdn);
    frm.trigger('update_planned_dozer_volumes');
  },
  block_width: (frm, cdt, cdn) => {
    const row = locals[cdt][cdn];
    frappe.model.set_value(cdt, cdn, 'block_width', flt(row.block_width, 2));
    recompute(frm, cdt, cdn);
    frm.trigger('update_planned_dozer_volumes');
  },
  blasting_depth: (frm, cdt, cdn) => {
    const row = locals[cdt][cdn];
    frappe.model.set_value(cdt, cdn, 'blasting_depth', flt(row.blasting_depth, 2));
    recompute(frm, cdt, cdn);
    frm.trigger('update_planned_dozer_volumes');
  },
  dozing_percentage: (frm, cdt, cdn) => {
    const row = locals[cdt][cdn];
    // Dozing % already uses precision 2, but we ensure consistency here too
    frappe.model.set_value(cdt, cdn, 'dozing_percentage', flt(row.dozing_percentage, 2));
    recompute(frm, cdt, cdn);
    frm.trigger('update_planned_dozer_volumes');
  }
});


// Hook equipment-count update on child-table changes
frappe.ui.form.on('Excavator Truck Link', {
  excavator: frm => frm.trigger('update_equipment_counts'),
  truck:     frm => frm.trigger('update_equipment_counts')
});

frappe.ui.form.on('Dozers Planned', {
  asset_name: frm => frm.trigger('update_equipment_counts')
});

function recompute(frm, cdt, cdn) {
  const row = locals[cdt][cdn];
  console.log('Recompute called', row.block_length, row.block_width, row.blasting_depth);

  // 🔹 Round inputs to 2 decimals before computing
  const length = flt(row.block_length || 0, 2);
  const width  = flt(row.block_width  || 0, 2);
  const depth  = flt(row.blasting_depth || 0, 2);

  // Requirement 2: block_bcm = length × width × depth
  const bcm = flt(length * width * depth, 2);

  frappe.model.set_value(cdt, cdn, 'block_bcm', bcm);

  // once bcm is set, update dozing too
  recompute_dozing(frm, cdt, cdn);
}


function recompute_dozing(frm, cdt, cdn) {
  const row = locals[cdt][cdn];
  console.log('Recompute dozing', row.block_bcm, row.dozing_percentage);
  // Requirement 3: block_dozing_bcms = block_bcm × dozing_percentage
const dz = flt((row.block_bcm || 0) * ((row.dozing_percentage || 0) / 100), 2);

frappe.model.set_value(cdt, cdn, 'block_dozing_bcms', dz);

}

function recalc_bcm_per_day(cdt, cdn) {
  const row = locals[cdt][cdn];

  const excavators = flt(row.production_excavators);
  const hours =
    flt(row.shift_day_hours) +
    flt(row.shift_night_hours);


  const bcm = excavators * hours * 220;

  frappe.model.set_value(cdt, cdn, 'bcm_per_day', bcm);
}


function updateProductionTimeSummary(frm) {
  try {
    let sums = {
      day: 0,
      night: 0,
      morning: 0,
      afternoon: 0,
      prodDays: 0
    };

    const rows = frm.doc.month_prod_days || [];

    rows.forEach(r => {
      const day = flt(r.shift_day_hours);
      const night = flt(r.shift_night_hours);
      const morning = flt(r.shift_morning_hours);
      const afternoon = flt(r.shift_afternoon_hours);
      const hrs = day + night + morning + afternoon;

      sums.day += day;
      sums.night += night;
      sums.morning += morning;
      sums.afternoon += afternoon;

      if (hrs > 0) {
        sums.prodDays += 1;
      }
    });

    const totalHours = sums.day + sums.night + sums.morning + sums.afternoon;

    const today = frappe.datetime.str_to_obj(frappe.datetime.get_today());
    let completedDays = 0;
    let completedHours = 0;

    rows.forEach(r => {
      if (!r.shift_start_date) return;

      const rowDate = frappe.datetime.str_to_obj(r.shift_start_date);
      const day = flt(r.shift_day_hours);
      const night = flt(r.shift_night_hours);
      const morning = flt(r.shift_morning_hours);
      const afternoon = flt(r.shift_afternoon_hours);
      const hrs = day + night + morning + afternoon;

      // Completed means production date is before today.
      // Today's date is still in progress, so it remains incomplete.
      if (hrs > 0 && rowDate < today) {
        completedDays += 1;
        completedHours += hrs;
      }
    });

    const remainingDays = Math.max(sums.prodDays - completedDays, 0);
    const remainingHours = Math.max(totalHours - completedHours, 0);

    frm.set_value({
      tot_shift_day_hours: sums.day,
      tot_shift_night_hours: sums.night,
      tot_shift_morning_hours: sums.morning,
      tot_shift_afternoon_hours: sums.afternoon,
      total_month_prod_hours: totalHours,
      num_prod_days: totalHours / getFullProductionDayHours(frm),
      prod_days_completed: completedHours / getFullProductionDayHours(frm),
      month_prod_hours_completed: completedHours,
      month_remaining_production_days: remainingHours / getFullProductionDayHours(frm),
      month_remaining_prod_hours: remainingHours
    });

    if (frm.doc.monthly_target_bcm) {
      frm.set_value({
        target_bcm_day: flt(frm.doc.monthly_target_bcm) / ((totalHours / getFullProductionDayHours(frm)) || 1),
        target_bcm_hour: flt(frm.doc.monthly_target_bcm) / (totalHours || 1)
      });
    } else {
      frm.set_value({
        target_bcm_day: 0,
        target_bcm_hour: 0
      });
    }

    frm.refresh_fields([
      'tot_shift_day_hours',
      'tot_shift_night_hours',
      'tot_shift_morning_hours',
      'tot_shift_afternoon_hours',
      'total_month_prod_hours',
      'num_prod_days',
      'prod_days_completed',
      'month_prod_hours_completed',
      'month_remaining_production_days',
      'month_remaining_prod_hours',
      'target_bcm_day',
      'target_bcm_hour'
    ]);
  } catch (e) {
    logError('updateProductionTimeSummary', e);
  }
}





// -----------------------------------------------------------------------------
// Equivalent Production Day
//
// 2x12Hour:
//     Normal full day = 2 shifts x configured weekday working hours
//     Example: 9 + 9 = 18 hours
//
//     18 / 18 = 1.000 day
//      6 / 18 = 0.333 day
//     14 / 18 = 0.778 day
//
// 3x8Hour:
//     Normal full day = 3 shifts x configured weekday working hours
// -----------------------------------------------------------------------------
function getFullProductionDayHours(frm) {
  const perShiftHours = flt(frm.doc.weekday_shift_hours);

  if (frm.doc.shift_system === '3x8Hour') {
    const fullDayHours = perShiftHours * 3;
    return fullDayHours > 0 ? fullDayHours : 24;
  }

  const fullDayHours = perShiftHours * 2;
  return fullDayHours > 0 ? fullDayHours : 18;
}


function calculateAndSetProductionStats(frm) {
  try {
    let sums = {
      day: 0,
      night: 0,
      morning: 0,
      afternoon: 0,
      days: 0
    };

    const rows = frm.doc.month_prod_days || [];

    rows.forEach(r => {
      const d = flt(r.shift_day_hours);
      const n = flt(r.shift_night_hours);
      const m = flt(r.shift_morning_hours);
      const a = flt(r.shift_afternoon_hours);
      const hrs = d + n + m + a;

      if (hrs > 0) {
        sums.days += 1;
      }

      sums.day += d;
      sums.night += n;
      sums.morning += m;
      sums.afternoon += a;
    });

    const totalHrs =
      sums.day +
      sums.night +
      sums.morning +
      sums.afternoon;

    let completedDays = 0;
    let completedHours = 0;

    rows.forEach(r => {
      const actualBcm =
        flt(r.total_daily_bcms) +
        flt(r.total_ts_bcms) +
        flt(r.total_dozing_bcms) +
        flt(r.day_shift_bcms) +
        flt(r.night_shift_bcms);

      const plannedHours =
        flt(r.shift_day_hours) +
        flt(r.shift_night_hours) +
        flt(r.shift_morning_hours) +
        flt(r.shift_afternoon_hours);

      if (actualBcm > 0 && plannedHours > 0) {
        completedDays += 1;
        completedHours += plannedHours;
      }
    });

    const remainingDays = Math.max(sums.days - completedDays, 0);
    const remainingHrs = Math.max(totalHrs - completedHours, 0);

    frm.set_value({
      tot_shift_day_hours: sums.day,
      tot_shift_night_hours: sums.night,
      tot_shift_morning_hours: sums.morning,
      tot_shift_afternoon_hours: sums.afternoon,
      total_month_prod_hours: totalHrs,
      num_prod_days: totalHrs / getFullProductionDayHours(frm),
      prod_days_completed: completedHours / getFullProductionDayHours(frm),
      month_prod_hours_completed: completedHours,
      month_remaining_production_days: remainingHrs / getFullProductionDayHours(frm),
      month_remaining_prod_hours: remainingHrs
    });

    if (frm.doc.monthly_target_bcm) {
      frm.set_value({
        target_bcm_day: flt(frm.doc.monthly_target_bcm) / ((totalHrs / getFullProductionDayHours(frm)) || 1),
        target_bcm_hour: flt(frm.doc.monthly_target_bcm) / (totalHrs || 1)
      });
    } else {
      frm.set_value({
        target_bcm_day: 0,
        target_bcm_hour: 0
      });
    }

    frm.refresh_fields([
      'tot_shift_day_hours',
      'tot_shift_night_hours',
      'tot_shift_morning_hours',
      'tot_shift_afternoon_hours',
      'total_month_prod_hours',
      'num_prod_days',
      'prod_days_completed',
      'month_prod_hours_completed',
      'month_remaining_production_days',
      'month_remaining_prod_hours',
      'target_bcm_day',
      'target_bcm_hour'
    ]);
  } catch (e) {
    logError('calculateAndSetProductionStats', e);
  }
}





function recalc_pre_target(frm) {
  const ts     = flt(frm.doc.total_ts_planned_volumes);
  const dozing = flt(frm.doc.planned_dozer_volumes);

  const factor = frm.doc.prod_adjust_factor || 1;

  // 1️⃣ Base target (NO adjustment factor)
  const preTarget = ts + dozing;

  // 2️⃣ Adjusted monthly target
  const monthlyTarget = preTarget * factor;

  frm.set_value({
    pre_target: preTarget,
    monthly_target_bcm: monthlyTarget
  });
}




function renderTeam(container, excavatorId, excavatorModel, truckList) {
  const teamId = excavatorId || 'unassigned';
  const label = excavatorId
    ? `⛏️ ${excavatorId} - ${excavatorModel}`
    : '📦 Unassigned Trucks';

  const section = $(`
    <div class="team-section" style="margin-bottom:20px;">
      <h4>${label}</h4>
      <ul class="truck-bin" data-excavator="${teamId}" style="min-height: 50px; background: #f9f9f9; border: 1px solid #ccc; padding: 10px; border-radius: 4px; list-style: none;"></ul>
    </div>
  `);

  truckList.forEach(truck => {
    const item = $(`
      <li class="truck-item" data-truck="${truck.id}" style="margin: 5px 0; padding: 6px 12px; background: #e3f2fd; border-radius: 3px;">
        🚚 ${truck.id} - ${truck.model}
      </li>
    `);
    section.find('.truck-bin').append(item);
  });

  container.append(section);

  console.log(`🧱 Rendered team: ${label} with ${truckList.length} truck(s)`);

}





























function getMppLocalKey(frm, key) {
  return `mpp_${frm.doc.name}_${key}`;
}

function getActiveEmptyExcavators(frm) {
  try {
    return JSON.parse(localStorage.getItem(getMppLocalKey(frm, 'active_empty_excavators')) || '[]');
  } catch (e) {
    return [];
  }
}

function setActiveEmptyExcavators(frm, list) {
  localStorage.setItem(
    getMppLocalKey(frm, 'active_empty_excavators'),
    JSON.stringify([...new Set(list.filter(Boolean))])
  );
}

function addActiveEmptyExcavator(frm, excavatorId) {
  if (!excavatorId) return;

  const list = getActiveEmptyExcavators(frm);

  if (!list.includes(excavatorId)) {
    list.push(excavatorId);
  }

  setActiveEmptyExcavators(frm, list);
}

function removeActiveEmptyExcavator(frm, excavatorId) {
  const list = getActiveEmptyExcavators(frm).filter(x => x !== excavatorId);
  setActiveEmptyExcavators(frm, list);
}

function moveTruckBackToSpare(frm, truckId) {
  console.log("Moving ADT back to Spare/Swing unit:", truckId);

  const row = (frm.doc.excavator_truck_assignments || []).find(r => r.truck === truckId);

  if (!row) {
    console.warn("Truck row not found:", truckId);
    return;
  }

  const oldExcavator = row.excavator;

  // Keep excavator on production side after its ADT is moved back to spare.
  if (oldExcavator) {
    addActiveEmptyExcavator(frm, oldExcavator);
  }

  row.excavator = null;
  row.excavator_model = null;

  frm.refresh_field('excavator_truck_assignments');
  frm.dirty();

  frm.save().then(() => {
    frm.trigger('update_equipment_counts');
    renderTruckAssignmentUI(frm);
  });
}

function enableDragAndDrop(frm) {
  // SortableJS comes with production_dependencies.bundle.js, which is not on every desk page
  frappe.require("production_dependencies.bundle.js", () => {
    enableTruckDragAndDrop(frm);
    enableExcavatorDragAndDrop(frm);
    enableDozerDragAndDrop(frm);
  });
}

function enableTruckDragAndDrop(frm) {
  if (!canManageMonthlyPlanningMachines()) {
    return;
  }

  $('.truck-bin').each(function () {
    Sortable.create(this, {
      group: 'trucks',
      animation: 150,
      draggable: '.truck-item',
      filter: 'button, select, input, textarea',
      preventOnFilter: false,
      onAdd(evt) {
        const truckId = evt.item.dataset.truck;
        const newExcavator = evt.to.dataset.excavator === 'spare'
          ? null
          : evt.to.dataset.excavator;

        const excavatorModel = newExcavator
          ? (
              (frm.doc.excavator_truck_assignments || [])
                .find(r => r.excavator === newExcavator)?.excavator_model || ''
            )
          : null;

        console.log(`Truck '${truckId}' moved to -> ${newExcavator || 'Spare/Swing unit'}`);

        const row = (frm.doc.excavator_truck_assignments || []).find(r => r.truck === truckId);

        if (row) {
          row.excavator = newExcavator;
          row.excavator_model = excavatorModel;

          if (newExcavator) {
            removeActiveEmptyExcavator(frm, newExcavator);
          }

          frm.refresh_field('excavator_truck_assignments');
          frm.dirty();

          frm.save()
            .then(() => {
              frm.trigger('update_equipment_counts');
              renderTruckAssignmentUI(frm);
            })
            .catch(err => console.error(`Save failed for ${truckId}:`, err));
        } else {
          console.warn("Could not find truck row:", truckId);
        }
      }
    });
  });
}

function enableExcavatorDragAndDrop(frm) {
  if (!canManageMonthlyPlanningMachines()) {
    return;
  }

  $('.excavator-bin').each(function () {
    Sortable.create(this, {
      group: 'excavators',
      animation: 150,
      draggable: '.excavator-item',
      filter: 'button, select, input, textarea, .truck-bin, .truck-item',
      preventOnFilter: false,
      onAdd(evt) {
        const excavatorId = evt.item.dataset.excavator;
        const movedTo = evt.to.dataset.zone;

        console.log(`Excavator '${excavatorId}' moved to -> ${movedTo}`);

        if (movedTo === 'assigned') {
          addActiveEmptyExcavator(frm, excavatorId);
          renderTruckAssignmentUI(frm);
          return;
        }

        if (movedTo === 'spare') {
          removeActiveEmptyExcavator(frm, excavatorId);

          (frm.doc.excavator_truck_assignments || []).forEach(row => {
            if (row.excavator === excavatorId && row.truck) {
              row.excavator = null;
              row.excavator_model = null;
            }
          });

          frm.refresh_field('excavator_truck_assignments');
          frm.dirty();

          frm.save()
            .then(() => renderTruckAssignmentUI(frm))
            .catch(err => console.error("Save failed while moving excavator:", err));
        }
      }
    });
  });
}


function enableDozerDragAndDrop(frm) {
  if (!canManageMonthlyPlanningMachines()) {
    return;
  }

  $('.dozer-bin').each(function () {
    Sortable.create(this, {
      group: 'dozers',
      animation: 150,
      draggable: '.dozer-card',
      handle: '.dozer-drag-handle',
      filter: 'button, select, input, textarea',
      preventOnFilter: false,

      onAdd(evt) {
        const dozerName = evt.item.dataset.dozer;
        const movedTo = evt.to.dataset.zone;

        console.log(`Dozer '${dozerName}' moved to -> ${movedTo}`);

        const row = (frm.doc.dozer_table || []).find(r => r.asset_name === dozerName);

        if (!row) {
          console.warn("Could not find dozer row:", dozerName);
          renderDozerAssignmentUI(frm);
          return;
        }

        const df = frappe.meta.get_docfield('Dozers Planned', 'dozing_type', frm.doc.name);
        const options = (df.options || '').split('\n').filter(Boolean);
        const defaultType = options[0] || 'Tip';

        const newType = movedTo === 'assigned'
          ? (row.dozing_type || defaultType)
          : '';

        frappe.model.set_value(row.doctype, row.name, 'dozing_type', newType)
          .then(() => {
            frm.refresh_field('dozer_table');
            frm.dirty();

            frm.save().then(() => {
              frm.trigger('update_equipment_counts');
              renderDozerAssignmentUI(frm);
            });
          });
      }
    });
  });
}



function addExcavatorsOnce(frm) {
  if (!canManageMonthlyPlanningMachines()) {
    showMachinePlanningPermissionMessage();
    return;
  }

  console.log("🔄 Attempting to add unassigned Excavators...");

  frappe.db.get_list('Asset', {
    filters: { asset_category: 'Excavator', location: frm.doc.location, docstatus: 1 },
    fields: ['name', 'item_name'],
    limit: 100
  }).then(excavators => {
    let added = 0;
    excavators.forEach(asset => {
      const alreadyExists = frm.doc.excavator_truck_assignments.some(
        r => r.excavator === asset.name
      );
      if (!alreadyExists) {
        const row = frm.add_child("excavator_truck_assignments");
        row.excavator = asset.name;
        row.excavator_model = asset.item_name;
        row.truck = null;
        added++;
      }
    });
    if (added) {
      frm.save().then(() => {
        console.log(`✅ Added ${added} excavator(s).`);
        renderTruckAssignmentUI(frm);
      });
    } else {
      frappe.msgprint("No new excavators to add.");
    }
  });
}

function addTrucksOnce(frm) {
  if (!canManageMonthlyPlanningMachines()) {
    showMachinePlanningPermissionMessage();
    return;
  }

  console.log("🔄 Attempting to add unassigned Trucks...");
  frappe.db.get_list('Asset', {
    filters: { asset_category: 'ADT', location: frm.doc.location, docstatus: 1 },
    fields: ['name', 'item_name'],
    limit: 100
  }).then(trucks => {
    let added = 0;
    trucks.forEach(asset => {
      if (!frm.doc.excavator_truck_assignments.some(r => r.truck === asset.name)) {
        const row = frm.add_child("excavator_truck_assignments");
        row.truck       = asset.name;
        row.truck_model = asset.item_name;
        row.excavator   = null;
        added++;
      }
    });
    if (added) {
      frm.save().then(() => {
        console.log(`✅ Added ${added} truck(s).`);
        renderTruckAssignmentUI(frm);
      });
    } else {
      frappe.msgprint("No new trucks to add.");
    }
  });
}



// Add Dozers handler
function addDozersOnce(frm) {
  if (!canManageMonthlyPlanningMachines()) {
    showMachinePlanningPermissionMessage();
    return;
  }

  console.log("Attempting to add Spare/Swing unit Dozers...");

  frappe.db.get_list('Asset', {
    filters: {
      asset_category: 'Dozer',
      location: frm.doc.location,
      docstatus: 1
    },
    fields: ['name', 'item_name'],
    limit: 100
  }).then(dozers => {
    console.log('Assets fetched for dozers:', dozers);

    let added = 0;

    dozers.forEach(asset => {
      const exists = (frm.doc.dozer_table || []).some(r => r.asset_name === asset.name);

      if (!exists) {
        console.log('Adding dozer as Spare/Swing unit:', asset.name, asset.item_name);

        const row = frm.add_child('dozer_table');
        row.asset_name = asset.name;
        row.item_name = asset.item_name;
        row.dozing_type = '';

        added++;
      }
    });

    if (added) {
      frm.refresh_field('dozer_table');

      frm.save().then(() => {
        console.log(`Added ${added} dozer(s) and saved form`);
        renderDozerAssignmentUI(frm);
      });
    } else {
      frappe.msgprint("No new dozers to add.");
    }
  });
}



function renderTruckAssignmentUI(frm) {
  // MPP_DIRECT_RENDER_ROLE_CONTROL
  const canManage = canManageMonthlyPlanningMachines();

  console.log(
    "Rendering Excavator/Truck UI",
    "user=",
    frappe.session.user,
    "canManage=",
    canManage
  );

  const wrapper = frm.get_field('dnd_html_truck_ui').$wrapper.empty();

  if (canManage) {
    const controls = $(`
      <div style="margin-bottom:12px;display:flex;gap:8px;">
        <button id="add-excavators" class="btn btn-primary btn-sm">+ Add Excavators</button>
        <button id="add-trucks" class="btn btn-secondary btn-sm">+ Add Trucks</button>
      </div>
    `).appendTo(wrapper);

    controls
      .find('#add-excavators')
      .off('click')
      .on('click', () => addExcavatorsOnce(frm));

    controls
      .find('#add-trucks')
      .off('click')
      .on('click', () => addTrucksOnce(frm));
  }

  const assignments = frm.doc.excavator_truck_assignments || [];
  const teamsMap = {};
  const spareTrucks = [];
  const spareExcavators = [];
  const excavatorModels = {};
  const activeEmptyExcavators = getActiveEmptyExcavators(frm);

  assignments.forEach(r => {
    if (r.excavator) {
      excavatorModels[r.excavator] = r.excavator_model || excavatorModels[r.excavator] || '';
    }

    if (r.excavator && r.truck) {
      teamsMap[r.excavator] = teamsMap[r.excavator] || {
        model: r.excavator_model || '',
        trucks: []
      };

      teamsMap[r.excavator].trucks.push({
        id: r.truck,
        model: r.truck_model || ''
      });
    }
  });

  activeEmptyExcavators.forEach(excId => {
    if (!teamsMap[excId]) {
      teamsMap[excId] = {
        model: excavatorModels[excId] || '',
        trucks: []
      };
    }
  });

  assignments.forEach(r => {
    if (!r.excavator && r.truck) {
      spareTrucks.push({
        id: r.truck,
        model: r.truck_model || ''
      });
    }

    if (r.excavator && !r.truck && !teamsMap[r.excavator]) {
      const alreadyListed = spareExcavators.some(e => e.id === r.excavator);

      if (!alreadyListed) {
        spareExcavators.push({
          id: r.excavator,
          model: r.excavator_model || ''
        });
      }
    }
  });

  const container = $(`
    <div style="display:flex;gap:24px;">
      <div id="teams-col" style="flex:1;"></div>
      <div id="spare-col" style="flex:1;"></div>
    </div>
  `).appendTo(wrapper);

  const teamsCol = container.find('#teams-col');
  const spareCol = container.find('#spare-col');

  const assignedExcSec = $(`
    <div style="padding:12px;margin-bottom:20px;
                background:#fafafa;border:1px solid #ddd;border-radius:4px;">
      <h4>Assigned Excavators</h4>
      <div class="excavator-bin" data-zone="assigned"
           style="min-height:80px;padding:4px;"></div>
    </div>
  `).appendTo(teamsCol);

  const assignedExcBin = assignedExcSec.find('.excavator-bin');

  if (!Object.keys(teamsMap).length) {
    assignedExcBin.append('<p><em>Drag excavators here to assign them.</em></p>');
  }

  Object.entries(teamsMap).forEach(([excId, data]) => {
    const section = $(`
      <div class="excavator-item"
           data-excavator="${excId}"
           style="position:relative;padding:12px;margin-bottom:16px;
                  background:#ffffff;border:1px solid #ddd;border-radius:4px;
                  cursor:grab;">
        <div style="font-weight:bold;margin-bottom:8px;">
          ⛏️ ${excId} — ${data.model || ''}
        </div>
        <ul class="truck-bin" data-excavator="${excId}"
            style="min-height:50px;list-style:none;padding:0;margin:0;cursor:default;">
        </ul>
      </div>
    `);

    if (canManage) {
      $('<button class="btn btn-danger btn-xs" ' +
        'style="position:absolute;background:transparent;top:8px;right:8px;">🗑️</button>')
        .appendTo(section)
        .off('click')
        .on('click', () => {
          console.log("Moving excavator team to Spare/Swing unit:", excId);

          removeActiveEmptyExcavator(frm, excId);

          (frm.doc.excavator_truck_assignments || []).forEach(row => {
            if (row.excavator === excId && row.truck) {
              row.excavator = null;
              row.excavator_model = null;
            }
          });

          frm.refresh_field('excavator_truck_assignments');
          frm.dirty();

          frm.save().then(() => {
            renderTruckAssignmentUI(frm);
          });
        });
    }

    if (!data.trucks.length) {
      section.find('.truck-bin').append('<li><em>No trucks assigned. Drag ADTs here.</em></li>');
    }

    data.trucks.forEach(t => {
      const li = $(`
        <li class="truck-item"
            data-truck="${t.id}"
            style="display:flex;justify-content:space-between;align-items:center;
                   padding:6px 12px;margin:4px 0;
                   background:#e3f2fd;border-radius:3px;cursor:grab;">
          <span>🚚 ${t.id} — ${t.model}</span>
        </li>
      `);

      if (canManage) {
        $('<button class="btn btn-danger btn-xs" style="background:transparent;">🗑️</button>')
          .appendTo(li)
          .off('click')
          .on('click', () => {
            moveTruckBackToSpare(frm, t.id);
          });
      }

      section.find('.truck-bin').append(li);
    });

    assignedExcBin.append(section);
  });

  const spareExcSec = $(`
    <div style="padding:12px;margin-bottom:20px;
                background:#f5f5f5;border:1px solid #ddd;border-radius:4px;">
      <h4>Spare/Swing unit Excavators</h4>
      <div class="excavator-bin" data-zone="spare"
           style="min-height:80px;padding:4px;"></div>
    </div>
  `).appendTo(spareCol);

  const spareExcBin = spareExcSec.find('.excavator-bin');

  if (!spareExcavators.length) {
    spareExcBin.append('<p><em>No Spare/Swing unit excavators.</em></p>');
  }

  spareExcavators.forEach(e => {
    const excCard = $(`
      <div class="excavator-item"
           data-excavator="${e.id}"
           style="position:relative;padding:10px;margin:8px 0;
                  background:#ffffff;border:1px solid #ddd;border-radius:4px;
                  cursor:grab;">
        <strong>⛏️ ${e.id} — ${e.model || ''}</strong>
      </div>
    `);

    if (canManage) {
      $('<button class="btn btn-danger btn-xs" ' +
        'style="position:absolute;background:transparent;top:6px;right:6px;">🗑️</button>')
        .appendTo(excCard)
        .off('click')
        .on('click', () => {
          console.log("Removing Spare/Swing unit excavator:", e.id);

          const row = (frm.doc.excavator_truck_assignments || [])
            .find(r => r.excavator === e.id && !r.truck);

          if (row) {
            frappe.model.clear_doc(row.doctype, row.name);

            frm.doc.excavator_truck_assignments = frm.doc.excavator_truck_assignments
              .filter(r => r.name !== row.name);

            removeActiveEmptyExcavator(frm, e.id);

            frm.refresh_field('excavator_truck_assignments');

            frm.save().then(() => {
              renderTruckAssignmentUI(frm);
            });
          }
        });
    }

    spareExcBin.append(excCard);
  });

  const spareTruckSec = $(`
    <div style="padding:12px;margin-bottom:20px;
                background:#fff8e1;border:1px solid #f0e6a1;border-radius:4px;">
      <h4>Spare/Swing unit Trucks</h4>
      <ul class="truck-bin" data-excavator="spare"
          style="min-height:50px;list-style:none;padding:0;margin:0;">
      </ul>
    </div>
  `).appendTo(spareCol);

  if (!spareTrucks.length) {
    spareTruckSec.find('.truck-bin').append('<li><em>No Spare/Swing unit trucks.</em></li>');
  }

  spareTrucks.forEach(t => {
    const li = $(`
      <li class="truck-item"
          data-truck="${t.id}"
          style="display:flex;justify-content:space-between;align-items:center;
                 padding:6px 12px;margin:4px 0;
                 background:#ffefd5;border-radius:3px;cursor:grab;">
        <span>🚚 ${t.id} — ${t.model}</span>
      </li>
    `);

    if (canManage) {
      $('<button class="btn btn-danger btn-xs" style="background:transparent;">🗑️</button>')
        .appendTo(li)
        .off('click')
        .on('click', () => {
          moveTruckBackToSpare(frm, t.id);
        });
    }

    spareTruckSec.find('.truck-bin').append(li);
  });

  if (canManage) {
    enableDragAndDrop(frm);
  }
}




//
// Single, canonical renderDozerAssignmentUI
//

//
// Single, canonical renderDozerAssignmentUI
//

function renderDozerAssignmentUI(frm) {
  const canManage = canManageMonthlyPlanningMachines();

  console.log(
    "Rendering Dozer UI",
    "user=",
    frappe.session.user,
    "canManage=",
    canManage
  );

  const wrapper = frm.get_field('dnd_dozer_assigned').$wrapper.empty();

  if (canManage) {
    $('<div style="margin-bottom:12px;">' +
      '<button type="button" id="add-dozers" class="btn btn-primary btn-sm">' +
      '+ Add Dozers' +
      '</button>' +
      '</div>').appendTo(wrapper)
      .find('#add-dozers')
      .off('click')
      .on('click', () => addDozersOnce(frm));
  }

  const container = $(`
    <div style="display:flex;gap:24px;">
      <div id="assigned-dozers-col" style="flex:1;"></div>
      <div id="spare-dozers-col" style="flex:1;"></div>
    </div>
  `).appendTo(wrapper);

  const assignedCol = container.find('#assigned-dozers-col');
  const spareCol = container.find('#spare-dozers-col');

  if (!frm.doc.dozer_table || !frm.doc.dozer_table.length) {
    assignedCol.append('<p><em>No dozers added yet.</em></p>');
    return;
  }

  const df = frappe.meta.get_docfield('Dozers Planned', 'dozing_type', frm.doc.name);
  const DOZING_TYPE_OPTIONS = (df.options || '')
    .split('\n')
    .filter(Boolean)
    .map(o => ({ value: o, label: o }));

  const assignedDozers = [];
  const spareDozers = [];

  (frm.doc.dozer_table || []).forEach((row, idx) => {
    if (!row || !row.asset_name) return;

    if (row.dozing_type) {
      assignedDozers.push({ row, idx });
    } else {
      spareDozers.push({ row, idx });
    }
  });

  const assignedSection = $(`
    <div style="padding:12px;margin-bottom:20px;
                background:#fafafa;border:1px solid #ddd;border-radius:4px;">
      <h4>Assigned Dozers</h4>
      <div class="dozer-bin" data-zone="assigned"
           style="min-height:140px;padding:8px;border:1px dashed #bbb;border-radius:4px;">
      </div>
    </div>
  `).appendTo(assignedCol);

  const spareSection = $(`
    <div style="padding:12px;margin-bottom:20px;
                background:#fff8e1;border:1px solid #f0e6a1;border-radius:4px;">
      <h4>Spare/Swing unit Dozers</h4>
      <div class="dozer-bin" data-zone="spare"
           style="min-height:140px;padding:8px;border:1px dashed #d8bd63;border-radius:4px;">
      </div>
    </div>
  `).appendTo(spareCol);

  const assignedBin = assignedSection.find('.dozer-bin');
  const spareBin = spareSection.find('.dozer-bin');

  if (!assignedDozers.length) {
    assignedBin.append('<p><em>Drag dozers here to assign them.</em></p>');
  }

  if (!spareDozers.length) {
    spareBin.append('<p><em>No Spare/Swing unit dozers.</em></p>');
  }

  function makeDozerCard(row, idx, isAssigned) {
    const card = $(`
      <div class="dozer-card"
           data-dozer="${row.asset_name}"
           data-dozer-name="${row.asset_name}"
           style="position:relative;padding:12px;margin-bottom:16px;
                  background:${isAssigned ? '#f0f0f0' : '#fff3cd'};
                  border:1px solid ${isAssigned ? '#ddd' : '#f0e6a1'};
                  border-radius:4px;">
        <div class="dozer-drag-handle"
             style="cursor:grab;background:#eaf1ff;border:1px dashed #6f9cff;
                    border-radius:4px;padding:7px 10px;margin-bottom:8px;
                    font-weight:bold;">
          Drag Dozer: ${row.asset_name}
        </div>

        <small>${row.item_name || ''}</small><br>

        <label style="display:block;margin-top:8px;">
          Dozing Type:
          <select class="dozing-type-select">
            <option value="">-- Spare/Swing unit --</option>
          </select>
        </label>
      </div>
    `);

    DOZING_TYPE_OPTIONS.forEach(opt => {
      card.find('.dozing-type-select').append(
        $(`<option value="${opt.value}">${opt.label}</option>`)
      );
    });

    card.find('.dozing-type-select')
      .val(row.dozing_type || '')
      .prop('disabled', !canManage);

    if (canManage) {
      card.find('.dozing-type-select')
        .off('change')
        .on('change', e => {
          const newType = e.target.value;

          frappe.model.set_value(row.doctype, row.name, 'dozing_type', newType)
            .then(() => {
              frm.refresh_field('dozer_table');
              frm.dirty();

              frm.save().then(() => {
                frm.trigger('update_equipment_counts');
                renderDozerAssignmentUI(frm);
              });
            });
        });
    }

    if (canManage) {
      $('<button class="btn btn-danger btn-xs" ' +
        'style="position:absolute;background:transparent;top:8px;right:8px;">Trash</button>')
        .appendTo(card)
        .off('click')
        .on('click', () => {
          const dozerName = card.attr('data-dozer-name');

          frm.doc.dozer_table = (frm.doc.dozer_table || []).filter(
            r => r.asset_name !== dozerName
          );

          frm.refresh_field('dozer_table');
          frm.dirty();

          frm.save().then(() => {
            frm.trigger('update_equipment_counts');
            renderDozerAssignmentUI(frm);
          });
        });
    }

    return card;
  }

  assignedDozers.forEach(({ row, idx }) => {
    assignedBin.append(makeDozerCard(row, idx, true));
  });

  spareDozers.forEach(({ row, idx }) => {
    spareBin.append(makeDozerCard(row, idx, false));
  });

  if (canManage) {
    enableDragAndDrop(frm);
  }
}





function getMPPDaysMetaColumns(frm) {
  // UI JSON (DocType) is the control mechanism:
  // - order: meta.field_order / meta.fields order
  // - label: df.label
  // - visibility: df.hidden
  // - editability: df.read_only and shift_system rules
  // - precision/fieldtype: df.fieldtype / df.precision
  const meta = frappe.get_meta('Monthly Production Days');
  if (!meta || !meta.fields) return [];

  // Build in DocType field order (excluding layout fields)
  const layoutTypes = new Set(['Section Break','Column Break','Tab Break','Fold','HTML','Button','Table','Table MultiSelect','Heading']);
  let fields = meta.fields.filter(df => df.fieldname && !layoutTypes.has(df.fieldtype) && !df.hidden);

  // Prefer DocType-export order if available
  if (Array.isArray(meta.field_order) && meta.field_order.length) {
    const idx = Object.create(null);
    meta.field_order.forEach((fn, i) => { idx[fn] = i; });
    fields.sort((a,b) => (idx[a.fieldname] ?? 9999) - (idx[b.fieldname] ?? 9999));
  }

  // Only show what the UI JSON says is "in_list_view" OR the core frozen columns (shift_start_date, day_week)
  const mustShow = new Set(['shift_start_date','day_week']);
  fields = fields.filter(df => mustShow.has(df.fieldname) || df.in_list_view);

  // Map to column defs
  const shiftSystem = frm.doc.shift_system;

  return fields.map(df => {
    // Base editability is controlled by DocType read_only
    let editable = !(df.read_only || df.read_only === 1);

    // Shift-system-aware alignment (keeps structure consistent and prevents "irrelevant" edits)
    if (shiftSystem === '2x12Hour') {
      if (['shift_morning_hours','shift_afternoon_hours'].includes(df.fieldname)) editable = false;
    }
    if (shiftSystem === '3x8Hour') {
      // In 3x8, day/night may still exist, but typically morning/afternoon/night are used.
      // Keep them editable only if DocType allows it.
      // (No extra lock here other than DocType read_only)
    }

    // Enforce computed field locks (even if DocType wasn't updated yet)
    if (['bcm_per_day','total_daily_bcms','total_ts_bcms','total_dozing_bcms',
         'cum_ts_bcms','tot_cumulative_dozing_bcms',
         'tot_cum_dozing_survey','tot_cum_ts_survey',
         'cum_dozing_variance','cum_ts_variance'].includes(df.fieldname)) {
      editable = false;
    }

    // Determine input type + step from fieldtype/precision
    const precision = (df.precision != null && df.precision !== '') ? parseInt(df.precision, 10) : null;
    let inputType = 'text';
    let step = 'any';

    if (['Float','Currency','Percent'].includes(df.fieldtype)) {
      inputType = 'number';
      step = precision != null ? String(Math.pow(10, -precision)) : '0.01';
    } else if (df.fieldtype === 'Int') {
      inputType = 'number';
      step = '1';
    } else if (df.fieldtype === 'Date') {
      inputType = 'date';
      step = null;
    }

    return {
      fieldname: df.fieldname,
      label: df.label || df.fieldname,
      editable,
      df,
      inputType,
      step
    };
  });
}

function renderMonthProdDaysHTML(frm) {
  const rows = frm.doc.month_prod_days || [];
  const wrapper = frm.get_field('month_prod_days_field').$wrapper;

  if (!rows.length) {
    wrapper.html('<p><em>No production days yet.</em></p>');
    return;
  }

  const columns = getMPPDaysMetaColumns(frm);
  if (!columns.length) {
    wrapper.html('<p><em>Unable to load Monthly Production Days meta.</em></p>');
    return;
  }

  // Freeze first 2 columns + sticky header row. Horizontal scroll from col 3 onward.
  const style = `
    <style>
      .mpp-days-wrap { overflow:auto; max-height:520px; border:1px solid #ddd; border-radius:6px; }
      table.mpp-days { border-collapse: separate; border-spacing: 0; width: max-content; min-width:100%; font-size:11px; line-height:1.2; }
      table.mpp-days th, table.mpp-days td { border:1px solid #e6e6e6; padding:6px 8px; white-space:nowrap; background:#fff; }
      table.mpp-days thead th { position: sticky; top: 0; z-index: 7; background:#f7f7f7; }

      /* CSS variables set dynamically after render */
      .mpp-days-wrap { --mpp-col1w: 180px; --mpp-col2w: 160px; }

      /* Freeze first 2 columns */
      table.mpp-days th:nth-child(1), table.mpp-days td:nth-child(1) {
        position: sticky; left: 0; z-index: 8; background:#fff;
      }
      table.mpp-days th:nth-child(2), table.mpp-days td:nth-child(2) {
        position: sticky; left: var(--mpp-col1w); z-index: 8; background:#fff;
      }

      /* Keep header above sticky columns */
      table.mpp-days thead th:nth-child(1),
      table.mpp-days thead th:nth-child(2) {
        z-index: 10; background:#f0f0f0;
      }

      .mpp-edit { width:100px; padding:2px 4px; font-size:11px; }
      .mpp-readonly { color:#555; }
    </style>
  `;

  let html = `${style}<div class="mpp-days-wrap"><table class="mpp-days"><thead><tr>`;
  html += columns.map(c => `<th data-fieldname="${c.fieldname}">${frappe.utils.escape_html(c.label)}</th>`).join('');
  html += `</tr></thead><tbody>`;

  rows.forEach(row => {
    html += `<tr data-rowname="${row.name}" data-doctype="${row.doctype}">`;

    columns.forEach(col => {
      const val = row[col.fieldname] ?? '';

      if (col.editable) {
        const stepAttr = col.step ? `step="${col.step}"` : '';
        html += `
          <td>
            <input
              class="mpp-edit"
              data-fieldname="${col.fieldname}"
              type="${col.inputType}"
              ${stepAttr}
              value="${val}"
            />
          </td>
        `;
      } else {
        html += `<td class="mpp-readonly">${frappe.utils.escape_html(String(val))}</td>`;
      }
    });

    html += `</tr>`;
  });

  html += `</tbody></table></div>`;
  wrapper.html(html);

  // Dynamically set sticky left offsets so the 2nd frozen column never overlaps
  try {
    const $wrap = wrapper.find('.mpp-days-wrap').get(0);
    const th1 = wrapper.find('table.mpp-days thead th:nth-child(1)').get(0);
    const th2 = wrapper.find('table.mpp-days thead th:nth-child(2)').get(0);
    if ($wrap && th1 && th2) {
      const w1 = Math.ceil(th1.getBoundingClientRect().width);
      const w2 = Math.ceil(th2.getBoundingClientRect().width);
      $wrap.style.setProperty('--mpp-col1w', `${w1}px`);
      $wrap.style.setProperty('--mpp-col2w', `${w2}px`);
    }
  } catch (e) {
    console.warn('MPP Days sticky width calc failed', e);
  }

  // Single delegated handler (prevents duplicate bindings on rerender)
  wrapper.off('change.mppdays');
  wrapper.on('change.mppdays', 'input.mpp-edit', function () {
    const $input = $(this);
    const fieldname = $input.data('fieldname');

    const $tr = $input.closest('tr');
    const rowname = $tr.data('rowname');
    const doctype = $tr.data('doctype');

    const child = (frm.doc.month_prod_days || []).find(r => r.name === rowname);
    if (!child) return;

    let newVal = $input.val();
    const col = (getMPPDaysMetaColumns(frm) || []).find(c => c.fieldname === fieldname);

    if (col && col.inputType === 'number') {
      newVal = (newVal === '' || newVal == null) ? 0 : flt(newVal);
    }

    frappe.model.set_value(doctype, rowname, fieldname, newVal).then(() => {
      // Recalc BCM/day + totals + pre-target where relevant (structure stays meta-driven)
      if (['shift_day_hours', 'shift_night_hours', 'production_excavators'].includes(fieldname)) {
        recalc_bcm_per_day(doctype, rowname);
        frm.trigger('update_ts_planned_volumes');
        recalc_pre_target(frm);
      }

      if (['shift_day_hours','shift_night_hours','shift_morning_hours','shift_afternoon_hours'].includes(fieldname)) {
        frm.trigger('recalculate_totals');
      }

      // Re-render to reflect calculated columns immediately (and keep sticky columns correct)
      renderMonthProdDaysHTML(frm);
    });
  });
}


