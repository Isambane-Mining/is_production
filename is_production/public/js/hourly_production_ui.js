// apps/is_production/public/js/hourly_production_ui.js

frappe.provide('is_production.ui');

is_production.ui.HourlyProductionUI = class {
    constructor(frm) {
        console.log('Initializing HourlyProductionUI');
        this.frm = frm;
        this.isInitialized = false;
        this.eventNamespace = `hourlyProductionUI_${Math.random().toString(36).substr(2, 9)}`;
        this.init();
    }

    init() {
        // Prevent double initialization
        if (this.isInitialized) {
            console.log('UI already initialized, skipping');
            return;
        }

        console.log('Setting up UI');
        this.cleanup(); // Clean up any existing UI first
        this.setupEvents();
        this.loadUI();
        this.isInitialized = true;
        
        // Store reference on the form to prevent multiple instances
        this.frm._hourlyProductionUI = this;
    }

    cleanup() {
        console.log('Cleaning up existing UI');
        
        // Remove existing DOM elements
        const container = this.frm.fields_dict.dnd_html_excavator_ui.$wrapper[0];
        if (container) {
            container.innerHTML = '';
        }
        
        // Remove namespaced event listeners
        if (this.eventNamespace) {
            $(document).off(`.${this.eventNamespace}`);
        }
        
        // Clear drag and drop event listeners
        document.removeEventListener('dragend', this.stopAutoScroll);
        
        this.isInitialized = false;
    }

    setupEvents() {
        const me = this;
        const ns = this.eventNamespace;
        
        // Use namespaced events to prevent conflicts
        $(document).on(`change.${ns}`, '.truck-loads', function(e) {
            const rowName = this.getAttribute('data-row-name');
            const value = parseFloat(this.value) || 0;
            me.updateTruckField(rowName, 'loads', value);
        });

        $(document).on(`change.${ns}`, '.truck-geo-layer', function(e) {
            const rowName = this.getAttribute('data-row-name');
            const value = this.value;
            me.updateTruckField(rowName, 'geo_mat_layer_truck', value);
        });

        $(document).on(`change.${ns}`, '.dozer-production', function(e) {
            const rowName = $(this).data('dozer-name');
            const value = parseFloat(this.value) || 0;
            me.updateDozerField(rowName, 'bcm_hour', value);
        });

        $(document).on(`click.${ns}`, '.btn-remove-truck', function(e) {
            e.preventDefault();
            e.stopPropagation();
            
            // Prevent multiple clicks
            if ($(this).hasClass('processing')) {
                return;
            }
            $(this).addClass('processing');
            
            const rowName = this.getAttribute('data-row-name');
            const truckData = me.frm.doc.truck_loads.find(r => r.name === rowName);
            if (!truckData) {
                $(this).removeClass('processing');
                return;
            }

            // Save current values before unassigning
            const currentMatType = truckData.mat_type;
            const currentGeoLayer = truckData.geo_mat_layer_truck;

            // Mark document as dirty
            me.frm.dirty = true;
            me.frm.doc.__unsaved = 1;

            // Update local data immediately
            truckData.asset_name_shoval = null;
            truckData.loads = 0;
            truckData.mining_areas_trucks = null;

            Promise.all([
                frappe.model.set_value(truckData.doctype, truckData.name, 'asset_name_shoval', null),
                frappe.model.set_value(truckData.doctype, truckData.name, 'loads', 0),
                frappe.model.set_value(truckData.doctype, truckData.name, 'mining_areas_trucks', null)
            ]).then(() => {
                // Restore preserved values
                truckData.mat_type = currentMatType;
                truckData.geo_mat_layer_truck = currentGeoLayer;
                if (currentMatType) {
                    frappe.model.set_value(truckData.doctype, truckData.name, 'mat_type', currentMatType);
                }
                if (currentGeoLayer) {
                    frappe.model.set_value(truckData.doctype, truckData.name, 'geo_mat_layer_truck', currentGeoLayer);
                }

                // Create new unassigned card
                const newCardHtml = me.createTruckCard(truckData);
                const tempDiv = document.createElement('div');
                tempDiv.innerHTML = newCardHtml.trim();
                const newCardEl = tempDiv.firstChild;

                // Move to unassigned section
                const truckEl = document.querySelector(`.truck-block[data-row-name="${rowName}"]`);
                const unassigned = document.querySelector('#unassigned-trucks');
                
                if (truckEl && newCardEl && unassigned) {
                    // Remove placeholder if it exists
                    const placeholder = unassigned.querySelector('.placeholder-empty');
                    if (placeholder) {
                        placeholder.remove();
                    }
                    
                    // Replace the truck element
                    truckEl.replaceWith(newCardEl);
                    
                    // Move to unassigned container
                    unassigned.appendChild(newCardEl);
                }

                // Re-setup drag and drop for the new element
                me.setupDragAndDrop();
                
                // Refresh the child table and toolbar
                me.frm.refresh_field('truck_loads');
                me.frm.toolbar.refresh();
                
            }).catch((error) => {
                console.error('Error removing truck:', error);
                $(this).removeClass('processing');
            });
        });

        $(document).on(`change.${ns}`, '.dozer-area', function(e) {
            const dozerName = $(this).data('dozer-name');
            const value = this.value;
            me.updateDozerField(dozerName, 'mining_areas_dozer_child', value);
        });

        $(document).on(`change.${ns}`, '.dozer-service', function(e) {
            const dozerName = $(this).data('dozer-name');
            const value = this.value;
            me.updateDozerField(dozerName, 'dozer_service', value);
        });

        $(document).on(`change.${ns}`, '.dozer-geo-layer', function(e) {
            const dozerName = $(this).data('dozer-name');
            const value = this.value;
            me.updateDozerField(dozerName, 'geo_mat_layer_dozer', value);
        });

        $(document).on(`change.${ns}`, '.excavator-area', function() {
            const excavatorName = $(this).data('excavator-name');
            const area = $(this).val();
            
            // Update all trucks in this excavator block
            const container = $(this).closest('.excavator-block').find('.truck-container');
            container.find('.truck-block').each(function() {
                const rowName = $(this).data('row-name');
                me.updateTruckArea(rowName, area);
            });
            
            me.updateExcavatorDefaultArea(excavatorName, area);
        });
    }
    
    async loadUI() {
        // Prevent multiple loads
        if (this.isLoading) {
            console.log('UI already loading, skipping');
            return;
        }
        
        this.isLoading = true;
        
        // Clear existing UI
        this.frm.fields_dict.dnd_html_excavator_ui.$wrapper.empty();

        if (!this.frm.doc.location) {
            console.log('Skipping UI load - missing location');
            this.isLoading = false;
            return;
        }

        console.log('Loading UI');

        try {
            // Get all equipment at this location
            const [allExcavators, allDozers, allTrucks] = await Promise.all([
                this.getAssetsByCategory('Excavator'),
                this.getAssetsByCategory('Dozer'),
                this.getAssetsByCategory(['ADT', 'RIGID'])
            ]);

            // Create container
            const container = $(` 
                <div class="equipment-ui-container">
                    <div class="excavator-ui-section"></div>
                    <div class="dozer-ui-section"></div>
                </div>
            `);
            this.frm.fields_dict.dnd_html_excavator_ui.$wrapper.append(container);

            // Load both UIs
            this.loadExcavatorUI(allExcavators, allTrucks);
            this.loadDozersUI(allDozers);
            
        } catch (error) {
            console.error('Error loading UI:', error);
        } finally {
            this.isLoading = false;
        }
    }

    // Static method to get or create UI instance
    static getInstance(frm) {
        // Check if instance already exists and is valid
        if (frm._hourlyProductionUI && frm._hourlyProductionUI.isInitialized) {
            console.log('Returning existing UI instance');
            return frm._hourlyProductionUI;
        }
        
        // Clean up any existing instance
        if (frm._hourlyProductionUI) {
            frm._hourlyProductionUI.cleanup();
        }
        
        // Create new instance
        console.log('Creating new UI instance');
        return new is_production.ui.HourlyProductionUI(frm);
    }

    loadExcavatorUI(excavators, trucks) {
        const miningAreas = this.getMiningAreas();
        const areaOptions = miningAreas.map(area => 
            `<option value="${area}">${area}</option>`
        ).join('');

        // Create a map of trucks by excavator
        const trucksByExcavator = {};
        const unassignedTrucks = [];
        
        // Initialize with all excavators
        excavators.forEach(excavator => {
            trucksByExcavator[excavator] = [];
        });

        // Group trucks
        (this.frm.doc.truck_loads || []).forEach(truck => {
            if (truck.asset_name_shoval && trucksByExcavator[truck.asset_name_shoval]) {
                trucksByExcavator[truck.asset_name_shoval].push(truck);
            } else {
                unassignedTrucks.push(truck);
            }
        });

        // Generate HTML for ALL excavators
        let assignedHtml = '';
        excavators.forEach(excavator => {
            const trucks = trucksByExcavator[excavator] || [];
            let currentArea = '';
            
            // Try to find an area from assigned trucks
            if (trucks.length > 0) {
                const truckWithArea = trucks.find(t => t.mining_areas_trucks);
                currentArea = truckWithArea ? truckWithArea.mining_areas_trucks : '';
            }

            const hoursTruck = trucks.find(t => (
                parseFloat(t.exc_start_hours || 0) ||
                parseFloat(t.exc_stop_hours || 0) ||
                parseFloat(t.exc_total_hours || 0)
            )) || {};

            const startHours = hoursTruck.exc_start_hours || '';
            const stopHours = hoursTruck.exc_stop_hours || '';
            const totalHours = hoursTruck.exc_total_hours || '';

            const setupTruck = trucks.find(t =>
                t.exc_to_area ||
                t.exc_hauling_distance_meter ||
                t.exc_start_load_time ||
                t.exc_end_load_time
            ) || {};

            const toArea = setupTruck.exc_to_area || '';
            const haulingDistance =
                setupTruck.exc_hauling_distance_meter || '';
            const startLoadTime =
                setupTruck.exc_start_load_time || '';
            const endLoadTime =
                setupTruck.exc_end_load_time || '';

            assignedHtml += `
                <div class="excavator-block" data-excavator-name="${excavator}">
                    <div class="excavator-header">
                        <img src="/assets/is_production/images/excavator (1).png" class="excavator-icon">
                        <h4>${excavator}</h4>
                        <div class="excavator-area-selector">
                            <label>Primary Area:</label>
                            <select class="form-control excavator-area" 
                                    data-excavator-name="${excavator}">
                                <option value="">Select Area</option>
                                ${areaOptions}
                                ${currentArea ? `<option value="${currentArea}" selected>${currentArea}</option>` : ''}
                            </select>
                        </div>

                            <div style="margin-top:6px;">
                                <label style="font-size:11px; margin-bottom:2px;">
                                    To Area:
                                </label>
                                <input type="text"
                                       class="form-control exc-to-area"
                                       data-excavator-name="${excavator}"
                                       value="${toArea}"
                                       autocomplete="off"
                                       placeholder=""
                                       style="height:28px; padding:2px 6px;">
                            </div>

                        <div class="excavator-hours-row" style="display:flex; gap:8px; align-items:end; flex-wrap:wrap; margin-left:10px;">
                            <!-- Hour fields remain in DOM for existing logic,
                                 but are hidden from the user. -->
                            <input type="hidden"
                                   class="exc-hour-input exc-start-hours"
                                   data-excavator-name="${excavator}"
                                   value="${startHours}">

                            <input type="hidden"
                                   class="exc-hour-input exc-stop-hours"
                                   data-excavator-name="${excavator}"
                                   value="${stopHours}">

                            <input type="hidden"
                                   class="exc-total-hours"
                                   data-excavator-name="${excavator}"
                                   value="${totalHours}">

                            <div style="flex:1 1 150px; min-width:150px;">
                                <label style="font-size:11px; margin-bottom:2px;">
                                    Hauling Distance Meter
                                </label>
                                <input type="text"
                                       class="form-control exc-hauling-distance"
                                       data-excavator-name="${excavator}"
                                       data-current-value="${haulingDistance}"
                                       value="${haulingDistance}"
                                       list="hauling-distance-master-options"
                                       autocomplete="off"
                                       placeholder=""
                                       style="height:28px; padding:2px 6px;">
                            </div>

                            <div style="flex:0 0 120px; min-width:120px;">
                                <label style="font-size:11px; margin-bottom:2px;">
                                    Start Load Time
                                </label>
                                <input type="text"
                                       class="form-control exc-start-load-time exc-load-time-input"
                                       data-excavator-name="${excavator}"
                                       value="${startLoadTime}"
                                       style="height:28px; padding:2px 6px;">
                            </div>

                            <div style="flex:0 0 120px; min-width:120px;">
                                <label style="font-size:11px; margin-bottom:2px;">
                                    End Load Time
                                </label>
                                <input type="text"
                                       class="form-control exc-end-load-time exc-load-time-input"
                                       data-excavator-name="${excavator}"
                                       value="${endLoadTime}"
                                       style="height:28px; padding:2px 6px;">
                            </div>
                        </div>
                    </div>
                    <div class="truck-container" id="excavator-${excavator.replace(/\s+/g, '-')}">
                        ${trucks.map(truck => this.createTruckCard(truck)).join('')}
                        ${trucks.length === 0 ? '<div class="placeholder-empty">Drop trucks here</div>' : ''}
                    </div>
                </div>
            `;
        });

        // Generate HTML for unassigned trucks
        const unassignedHtml = `
            <div class="excavator-block">
                <div class="excavator-header">
                    <img src="/assets/is_production/images/mining-truck.png" class="truck-icon">
                    <h4>Unassigned Trucks</h4>
                </div>
                <div class="truck-container" id="unassigned-trucks">
                    ${unassignedTrucks.map(truck => this.createTruckCard(truck)).join('')}
                    ${unassignedTrucks.length === 0 ? '<div class="placeholder-empty">No unassigned trucks</div>' : ''}
                </div>
            </div>
        `;

        // Set HTML
        const fullHtml = `
            <div class="dnd-ui-container">
                <div class="assigned-excavators">
                    ${assignedHtml}
                </div>
                <div class="unassigned-trucks">
                    ${unassignedHtml}
                </div>
            </div>
        `;

        this.frm.fields_dict.dnd_html_excavator_ui.$wrapper.find('.excavator-ui-section').html(fullHtml);
        this.setupDragAndDrop();
        this.setupAreaSelectors();
    }

    loadDozersUI(dozerNames) {
        if (!this.frm.doc.location) {
            console.log('Skipping Dozers UI - no location specified');
            return;
        }

        console.log('Loading Dozers UI');
        this.renderDozersUI(dozerNames);
    }

  renderDozersUI(dozerNames) {
    // Pull dozer service options from DocField meta
    const dozerServiceField = frappe.meta.get_docfield('Dozer Production', 'dozer_service');
    const serviceOptions = dozerServiceField?.options?.split('\n').filter(Boolean) || [];

    // Get mining areas for area dropdown
    const miningAreas = this.getMiningAreas();
    const areaOptions = miningAreas.map(area =>
        `<option value="${area}">${area}</option>`
    ).join('');

    // Get geo layer options
    const geoOptions = this.frm.dozer_geo_options_str ?
        this.frm.dozer_geo_options_str.split('\n').filter(Boolean) : [];
    const geoLayerOptions = geoOptions.map(option =>
        `<option value="${option}">${option}</option>`
    ).join('');

    let dozersHtml = `
        <style>
            /* KOSI_DOZER_STRAIGHT_COLUMNS_V1 */

            .dozer-ui-section .dozer-fields-row {
                display: grid !important;
                grid-template-columns: repeat(6, minmax(0, 1fr)) !important;
                gap: 8px !important;
                align-items: end !important;
                width: 100% !important;
            }

            .dozer-ui-section .dozer-field {
                width: 100% !important;
                min-width: 0 !important;
                margin: 0 !important;
            }

            .dozer-ui-section .dozer-field label {
                display: block !important;
                white-space: nowrap !important;
                margin-bottom: 5px !important;
                line-height: 1.2 !important;
            }

            .dozer-ui-section .dozer-field .form-control {
                width: 100% !important;
                min-width: 0 !important;
                box-sizing: border-box !important;
            }

            @media (max-width: 900px) {
                .dozer-ui-section .dozer-fields-row {
                    grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
                }
            }

            @media (max-width: 520px) {
                .dozer-ui-section .dozer-fields-row {
                    grid-template-columns: 1fr !important;
                }
            }
        </style>

        <div class="dozer-section">
            <h3 class="dozer-section-title">
                Dozers<img src="/assets/is_production/images/dozer.png" class="dozer-icon">
            </h3>
            <div class="dozer-container">
    `;

    dozerNames.forEach(dozerName => {
        const dozerData = this.frm.doc.dozer_production?.find(d => d.asset_name === dozerName) || {};
        const haulingDistance = dozerData.dozer_hauling_distance_meter || '';
        const areaTo = dozerData.area_to || '';

        // Kriel Rehabilitation fixed BCM rates must also be applied
        // when an existing dozer row is first rendered.
        let displayBcmHour = parseFloat(dozerData.bcm_hour) || 0;
        const location = String(this.frm.doc.location || '').trim();

        if (location === 'Kriel Rehabilitation') {
            if (dozerData.dozer_service === 'Production Dozing-50m') {
                displayBcmHour = 75;
                dozerData.bcm_hour = 75;
            } else if (dozerData.dozer_service === 'Production Dozing-100m') {
                displayBcmHour = 150;
                dozerData.bcm_hour = 150;
            } else if (
                dozerData.dozer_service === 'No Dozing' ||
                dozerData.dozer_service === 'Tip Dozing' ||
                dozerData.dozer_service === 'Levelling'
            ) {
                displayBcmHour = 0;
                dozerData.bcm_hour = 0;
            }
        }

        const serviceOptionsHtml = // In renderDozersUI, replace the service options part with:
        `
            <option value="No Dozing" ${dozerData.dozer_service === 'No Dozing' ? 'selected' : ''}>No Dozing</option>
            <option value="Tip Dozing" ${dozerData.dozer_service === 'Tip Dozing' ? 'selected' : ''}>Tip Dozing</option>
            <option value="Production Dozing-50m" ${dozerData.dozer_service === 'Production Dozing-50m' ? 'selected' : ''}>Production Dozing-50m</option>
            <option value="Production Dozing-100m" ${dozerData.dozer_service === 'Production Dozing-100m' ? 'selected' : ''}>Production Dozing-100m</option>
            <option value="Levelling" ${dozerData.dozer_service === 'Levelling' ? 'selected' : ''}>Levelling</option>
        `;

        dozersHtml += `
            <div class="dozer-block" data-dozer-name="${dozerName}">
                <div class="dozer-header">
                    <div class="dozer-name">${dozerName}<img src="/assets/is_production/images/dozer.png" class="dozer-img"></div>
                </div>
                <div class="dozer-fields-row">
                    <div class="dozer-field">
                        <label>Service</label>
                        <select class="form-control dozer-service" data-dozer-name="${dozerName}">
                            ${serviceOptionsHtml}
                        </select>
                    </div>
                    <div class="dozer-field">
                <label>BCM/Hour</label>
                        <input type="number"
                            class="form-control dozer-production"
                            value="${displayBcmHour}"
                            data-dozer-name="${dozerName}"
                            readonly
                            disabled>
                    </div>
                    <div class="dozer-field">
                        <label>Primary Working Area</label>
                        <select class="form-control dozer-area" data-dozer-name="${dozerName}">
                            <option value="">Select Area</option>
                            ${areaOptions}
                            ${dozerData.mining_areas_dozer_child ? `<option value="${dozerData.mining_areas_dozer_child}" selected>${dozerData.mining_areas_dozer_child}</option>` : ''}
                        </select>
                    </div>

                    <div class="dozer-field">
                        <label>Area To</label>
                        <input type="text"
                               class="form-control dozer-area-to"
                               data-dozer-name="${dozerName}"
                               value="${areaTo}"
                               autocomplete="off">
                    </div>

                    <div class="dozer-field">
                        <label>Geo / Mat Layer</label>
                        <select class="form-control dozer-geo-layer" data-dozer-name="${dozerName}">
                            <option value="">Select Geo Layer</option>
                            ${geoLayerOptions}
                            ${dozerData.dozer_geo_mat_layer ? `<option value="${dozerData.dozer_geo_mat_layer}" selected>${dozerData.dozer_geo_mat_layer}</option>` : ''}
                        </select>
                    </div>

                    <div class="dozer-field">
                        <label>
                            Hauling Distance Meter
                            <span style="color:#e03636;">*</span>
                        </label>

                        <input type="text"
                               class="form-control dozer-hauling-distance"
                               data-dozer-name="${dozerName}"
                               data-current-value="${haulingDistance}"
                               value="${haulingDistance}"
                               list="dozer-hauling-distance-master-options"
                               autocomplete="off"
                               placeholder="">
                    </div>
                </div>
            </div>
        `;
    });

    dozersHtml += `
            </div>
        </div>
    `;

    this.frm.fields_dict.dnd_html_excavator_ui.$wrapper.find('.dozer-ui-section').html(dozersHtml);

    const wrapper = this.frm.fields_dict.dnd_html_excavator_ui.$wrapper;

    frappe.db.get_list('Hauling Distance Meter', {
        fields: [
            'name',
            'hauling_distance',
            'from_distance',
            'to_distance'
        ],
        filters: {
            is_active: 1
        },
        order_by: 'to_distance asc, from_distance asc',
        limit: 1000
    }).then(rows => {

        wrapper.find('#dozer-hauling-distance-master-options').remove();

        const datalist = $(
            '<datalist id="dozer-hauling-distance-master-options"></datalist>'
        );

        rows.forEach(row => {
            const value = row.hauling_distance || row.name;

            datalist.append(
                $('<option></option>').attr('value', value)
            );
        });

        wrapper.append(datalist);

    }).catch(error => {
        console.error(
            'Unable to load Dozer Hauling Distance options:',
            error
        );
    });

    $(document).off(
        'change.dozer_hauling_distance input.dozer_hauling_distance',
        '.dozer-hauling-distance'
    );

    $(document).on(
        'change.dozer_hauling_distance input.dozer_hauling_distance',
        '.dozer-hauling-distance',
        (event) => {

            const input = $(event.currentTarget);

            const dozerName = input.data('dozer-name');

            const haulingDistance = String(
                input.val() || ''
            ).trim();

            if (haulingDistance) {
                input.css('border-color', '');
            } else {
                input.css('border-color', '#e03636');
            }

            const row = (this.frm.doc.dozer_production || []).find(
                d => d.asset_name === dozerName
            );

            if (!row) {
                return;
            }

            row.dozer_hauling_distance_meter = haulingDistance;

            frappe.model.set_value(
                row.doctype,
                row.name,
                'dozer_hauling_distance_meter',
                haulingDistance
            );

            this.frm.dirty = true;
            this.frm.doc.__unsaved = 1;

            this.frm.refresh_field('dozer_production');
            this.frm.toolbar.refresh();
        }
    );

    // Area To - free text field
    $(document).off(
        'input.dozer_area_to change.dozer_area_to',
        '.dozer-area-to'
    );

    $(document).on(
        'input.dozer_area_to change.dozer_area_to',
        '.dozer-area-to',
        (event) => {

            const input = $(event.currentTarget);
            const dozerName = input.data('dozer-name');

            const areaTo = String(
                input.val() || ''
            ).trim();

            const row = (this.frm.doc.dozer_production || []).find(
                d => d.asset_name === dozerName
            );

            if (!row) {
                return;
            }

            row.area_to = areaTo;

            frappe.model.set_value(
                row.doctype,
                row.name,
                'area_to',
                areaTo
            );

            this.frm.dirty = true;
            this.frm.doc.__unsaved = 1;
        }
    );

    // Rebind UI event handlers
    this.setupDozerEvents();
}


    getAssetsByCategory(category) {
        return new Promise((resolve) => {
            frappe.call({
                method: 'frappe.client.get_list',
                args: {
                    doctype: 'Asset',
                    fields: ['asset_name'],
                    filters: {
                        location: this.frm.doc.location,
                        asset_category: Array.isArray(category) ? ['in', category] : ['=', category],
                        docstatus: 1
                    },
                    order_by: 'asset_name asc'
                },
                callback: (r) => {
                    resolve(r.message ? r.message.map(e => e.asset_name) : []);
                }
            });
        });
    }

    createTruckCard(truck) {
    const isAssigned = !!truck.asset_name_shoval;
    console.log('Creating truck card for:', truck.asset_name_truck, 'isAssigned:', isAssigned, 'excavator:', truck.asset_name_shoval);
    
    const geoOptions = this.frm.truck_geo_options_str ? 
        this.frm.truck_geo_options_str.split('\n').filter(Boolean) : [];

    let optionsHtml = geoOptions.map(option => {
        const selected = option === truck.geo_mat_layer_truck ? 'selected' : '';
        return `<option value="${option}" ${selected}>${option}</option>`;
    }).join('');

    let areaHtml = '';
   
    return `
        <div class="truck-block" 
             data-truck-name="${truck.asset_name_truck}" 
             data-row-name="${truck.name}"
             draggable="true">
             
            <div class="truck-header">
                <img src="/assets/is_production/images/mining-truck.png" class="truck-icon">
                <div class="truck-name">${truck.asset_name_truck}</div>
                ${isAssigned ? `
                    <button type="button" class="btn-remove-truck" 
                            title="Move to Unassigned" data-row-name="${truck.name}">
                        ✖
                    </button>
                ` : ''}
            </div>

            ${isAssigned ? `
                <div class="truck-fields-row">
                    <div class="truck-field">
                        <label>Loads</label>
                        <input type="number" 
                               class="form-control truck-loads" 
                               value="${truck.loads || 0}" 
                               data-row-name="${truck.name}"
                               min="0" step="0.1">
                    </div>
                    
                    <div class="truck-field">
                        <label>Geo Layer</label>
                        <select class="form-control truck-geo-layer" data-row-name="${truck.name}">
                            <option value="">Select Geo Layer</option>
                            ${optionsHtml}
                        </select>
                    </div>
                    
                    ${areaHtml}
                </div>
            ` : ''}
        </div>
    `;
}
    setupDozerEvents() {
    const me = this;

    $(document)
        .off('change.krielDozerService', '.dozer-service')
        .on('change.krielDozerService', '.dozer-service', function () {
            const name = $(this).data('dozer-name');
            const value = this.value;
            const location = String(me.frm.doc.location || '').trim();

            // Always update the selected service in the child row.
            me.updateDozerField(name, 'dozer_service', value);

            let bcmHour = null;

            // Non-production dozing services must be zero.
            if (
                value === 'No Dozing' ||
                value === 'Tip Dozing' ||
                value === 'Levelling'
            ) {
                bcmHour = 0;
            }

            // Kriel Rehabilitation fixed production dozing rates.
            if (location === 'Kriel Rehabilitation') {
                if (value === 'Production Dozing-50m') {
                    bcmHour = 75;
                } else if (value === 'Production Dozing-100m') {
                    bcmHour = 150;
                }
            }

            // If this service has an automatic BCM value,
            // update both the child row and visible card immediately.
            if (bcmHour !== null) {
                me.updateDozerField(name, 'bcm_hour', bcmHour);

                $(this)
                    .closest('.dozer-block')
                    .find('.dozer-production')
                    .val(bcmHour);
            }
        });

    $(document).on('change', '.dozer-production', function () {
        const name = $(this).data('dozer-name');
        const value = parseInt(this.value) || 0;
        me.updateDozerField(name, 'bcm_hour', value);
    });

    $(document).on('change', '.dozer-area', function () {
        const name = $(this).data('dozer-name');
        const value = this.value;
        me.updateDozerField(name, 'mining_areas_dozer_child', value);
    });

    $(document).on('change', '.dozer-geo-layer', function () {
        const name = $(this).data('dozer-name');
        const value = this.value;
        me.updateDozerField(name, 'dozer_geo_mat_layer', value);

        // Automatically set mat_type if map exists
        if (me.frm.geoMaterialMap && me.frm.geoMaterialMap[value]) {
            me.updateDozerField(name, 'mat_type', me.frm.geoMaterialMap[value]);
        }
    });
}

   

    setupDragAndDrop() {
    console.log('Setting up drag and drop');
    const truckBlocks = document.querySelectorAll('.truck-block');
    const containers = document.querySelectorAll('.truck-container');
    
    // Auto-scroll variables
    let scrollInterval = null;
    const scrollSpeed = 10; // pixels per interval
    const scrollZone = 50; // pixels from edge to trigger scrolling
    
    // Auto-scroll function
    const autoScroll = (e) => {
        const viewportHeight = window.innerHeight;
        const mouseY = e.clientY;
        
        // Clear existing interval
        if (scrollInterval) {
            clearInterval(scrollInterval);
            scrollInterval = null;
        }
        
        // Check if we're in the scroll zones
        if (mouseY < scrollZone) {
            // Scroll up
            scrollInterval = setInterval(() => {
                window.scrollBy(0, -scrollSpeed);
            }, 16); // ~60fps
        } else if (mouseY > viewportHeight - scrollZone) {
            // Scroll down
            scrollInterval = setInterval(() => {
                window.scrollBy(0, scrollSpeed);
            }, 16); // ~60fps
        }
    };
    
    // Stop auto-scroll function
    const stopAutoScroll = () => {
        if (scrollInterval) {
            clearInterval(scrollInterval);
            scrollInterval = null;
        }
    };

    truckBlocks.forEach(truck => {
        truck.addEventListener('dragstart', (e) => {
            truck.classList.add('dragging');
            // Store initial mouse position
            truck.dataset.initialY = e.clientY;
        });

        truck.addEventListener('drag', (e) => {
            // Only auto-scroll if we're actually dragging (mouse moved)
            if (e.clientY !== 0) { // clientY is 0 when drag ends
                autoScroll(e);
            }
        });

        truck.addEventListener('dragend', async (e) => {
    truck.classList.remove('dragging');
    stopAutoScroll(); // Stop scrolling when drag ends
    
    const rowName = truck.getAttribute('data-row-name');
    const rowData = this.frm.doc.truck_loads.find(r => r.name === rowName);
    if (!rowData) return;

    const newContainer = truck.parentElement.closest('.excavator-block');
    const newExcavator = newContainer?.querySelector('h4')?.textContent || null;
    const isNowUnassigned = newExcavator === 'Unassigned Trucks';
    
    // Save current values before changing
    const currentMatType = rowData.mat_type;
    const currentGeoLayer = rowData.geo_mat_layer_truck;

    // Mark document as dirty before making changes
    this.frm.dirty = true;
    this.frm.doc.__unsaved = 1;

    if (isNowUnassigned) {
        // Update the local data first
        rowData.asset_name_shoval = null;
        rowData.loads = 0;
        rowData.mining_areas_trucks = null;
        
        // Then update the server
        await Promise.all([
            frappe.model.set_value(rowData.doctype, rowData.name, 'asset_name_shoval', null),
            frappe.model.set_value(rowData.doctype, rowData.name, 'loads', 0),
            frappe.model.set_value(rowData.doctype, rowData.name, 'mining_areas_trucks', null)
        ]);
        
        // Restore preserved values
        rowData.mat_type = currentMatType;
        rowData.geo_mat_layer_truck = currentGeoLayer;
        if (currentMatType) {
            frappe.model.set_value(rowData.doctype, rowData.name, 'mat_type', currentMatType);
        }
        if (currentGeoLayer) {
            frappe.model.set_value(rowData.doctype, rowData.name, 'geo_mat_layer_truck', currentGeoLayer);
        }
    } else {
        // Get the excavator's primary area from its dropdown
        const excavatorAreaSelect = newContainer.querySelector('.excavator-area');
        const excavatorArea = excavatorAreaSelect ? excavatorAreaSelect.value : null;

        // Update the local data first
        rowData.asset_name_shoval = newExcavator;
        if (excavatorArea) {
            rowData.mining_areas_trucks = excavatorArea;
        }
        
        // Then update the server
        await Promise.all([
            frappe.model.set_value(rowData.doctype, rowData.name, 'asset_name_shoval', newExcavator),
            excavatorArea && frappe.model.set_value(rowData.doctype, rowData.name, 'mining_areas_trucks', excavatorArea)
        ]);
    }

    // Refresh the child table to show changes
    this.frm.refresh_field('truck_loads');
    
    // Update the toolbar to show save button
    this.frm.toolbar.refresh();

    // Now create the new card with the updated data
    const newCardHtml = this.createTruckCard(rowData);
    const tempDiv = document.createElement('div');
    tempDiv.innerHTML = newCardHtml.trim();
    const newCardEl = tempDiv.firstChild;
    truck.replaceWith(newCardEl);

    // Remove placeholder if needed
    document.querySelectorAll('.placeholder-empty').forEach(placeholder => {
        if (placeholder.parentElement.querySelector('.truck-block')) {
            placeholder.remove();
        }
    });

    // Re-setup drag and drop for the new element
    this.setupDragAndDrop();
    this.updateExcavatorAssignments();
});
    });

    containers.forEach(container => {
        container.addEventListener('dragover', e => {
            e.preventDefault();
            
            // Continue auto-scrolling during dragover
            autoScroll(e);
            
            const dragging = document.querySelector('.dragging');
            if (dragging && !container.contains(dragging)) {
                container.appendChild(dragging);
            }
        });
        
        container.addEventListener('dragleave', () => {
            // Don't stop scrolling on dragleave as it fires frequently
            // Only stop on dragend
        });
        
        container.style.minHeight = '40px';
    });
    
    // Add global dragend listener to ensure scrolling stops
    document.addEventListener('dragend', stopAutoScroll);
}

   updateDozerField(dozerName, fieldname, value) {
    const row = this.frm.doc.dozer_production?.find(d => d.asset_name === dozerName);
    if (row) {
        frappe.model.set_value(row.doctype, row.name, fieldname, value);

        if (fieldname === 'dozer_geo_mat_layer' && this.frm.geoMaterialMap && this.frm.geoMaterialMap[value]) {
            frappe.model.set_value(row.doctype, row.name, 'mat_type', this.frm.geoMaterialMap[value]);
        }
        
        // Add this new logic for service type changes
        if (fieldname === 'dozer_service') {
            this.handleDozerServiceChange(row);
        }
    }
    }



    getMiningAreas() {
        return (this.frm.doc.mining_areas_options || []).map(r => r.mining_areas).filter(v => v);
    }

    getDefaultAreaForExcavator(excavator) {
        // Implement custom logic for default areas if needed
        return '';
    }

    setupAreaSelectors() {
        const me = this;
        
        $(document).on('change', '.excavator-area', function() {
            const excavatorName = $(this).data('excavator-name');
            const area = $(this).val();
            
            // Update all trucks in this excavator block
            const container = $(this).closest('.excavator-block').find('.truck-container');
            container.find('.truck-block').each(function() {
                const rowName = $(this).data('row-name');
                me.updateTruckArea(rowName, area);
            });
            
            me.updateExcavatorDefaultArea(excavatorName, area);
        });

        $(document).off('input.excavator_hours change.excavator_hours', '.exc-hour-input');
        $(document).on('input.excavator_hours change.excavator_hours', '.exc-hour-input', function() {
            const block = $(this).closest('.excavator-block');
            const excavatorName = block.data('excavator-name');

            const start = flt(block.find('.exc-start-hours').val() || 0);
            const stop = flt(block.find('.exc-stop-hours').val() || 0);

            let total = 0;
            if (start > 0 && stop > 0 && stop >= start) {
                total = stop - start;
            }

            block.find('.exc-total-hours').val(total ? total.toFixed(0) : '');

            me.updateExcavatorHours(excavatorName, start, stop, total);
        });

        // ----------------------------------------------------
        // Excavator To Area
        // ----------------------------------------------------
        $(document).off(
            'input.excavator_to_area change.excavator_to_area',
            '.exc-to-area'
        );

        $(document).on(
            'input.excavator_to_area change.excavator_to_area',
            '.exc-to-area',
            function() {

                const excavatorName = $(this).data('excavator-name');
                const toArea = String($(this).val() || '').trim();

                me.updateExcavatorToArea(
                    excavatorName,
                    toArea
                );
            }
        );

        // ----------------------------------------------------
        // Previous-hour setup was applied asynchronously.
        // Refresh visible Excavator controls from truck_loads.
        // ----------------------------------------------------
        $(document).off(
            'hourly-production-previous-hour-applied.excavator_ui'
        );

        $(document).on(
            'hourly-production-previous-hour-applied.excavator_ui',
            (event, frm) => {

                if (!frm || frm !== me.frm) {
                    return;
                }

                const wrapper =
                    me.frm.fields_dict.dnd_html_excavator_ui?.$wrapper;

                if (!wrapper) {
                    return;
                }

                const groups = {};

                (me.frm.doc.truck_loads || []).forEach(row => {

                    const excavator = String(
                        row.asset_name_shoval || ''
                    ).trim();

                    if (!excavator) {
                        return;
                    }

                    if (!groups[excavator]) {
                        groups[excavator] = [];
                    }

                    groups[excavator].push(row);
                });

                Object.entries(groups).forEach(
                    ([excavator, rows]) => {

                        const setupRow =
                            rows.find(row =>
                                row.exc_to_area ||
                                row.exc_hauling_distance_meter ||
                                row.exc_start_load_time ||
                                row.exc_end_load_time ||
                                row.mining_areas_trucks
                            ) || rows[0];

                        if (!setupRow) {
                            return;
                        }

                        wrapper
                            .find(
                                `.excavator-area[data-excavator-name="${excavator}"]`
                            )
                            .val(
                                setupRow.mining_areas_trucks || ''
                            );

                        wrapper
                            .find(
                                `.exc-to-area[data-excavator-name="${excavator}"]`
                            )
                            .val(
                                setupRow.exc_to_area || ''
                            );

                        wrapper
                            .find(
                                `.exc-hauling-distance[data-excavator-name="${excavator}"]`
                            )
                            .val(
                                setupRow.exc_hauling_distance_meter || ''
                            )
                            .attr(
                                'data-current-value',
                                setupRow.exc_hauling_distance_meter || ''
                            );

                        wrapper
                            .find(
                                `.exc-start-load-time[data-excavator-name="${excavator}"]`
                            )
                            .val(
                                setupRow.exc_start_load_time || ''
                            );

                        wrapper
                            .find(
                                `.exc-end-load-time[data-excavator-name="${excavator}"]`
                            )
                            .val(
                                setupRow.exc_end_load_time || ''
                            );
                    }
                );

                console.log(
                    'Custom Excavator UI refreshed from previous hour.'
                );
            }
        );

        // ----------------------------------------------------
        // Hauling Distance Meter
        // ----------------------------------------------------
        $(document).off(
            'change.excavator_hauling_distance input.excavator_hauling_distance',
            '.exc-hauling-distance'
        );

        $(document).on(
            'change.excavator_hauling_distance input.excavator_hauling_distance',
            '.exc-hauling-distance',
            function() {
                const excavatorName = $(this).data('excavator-name');
                const haulingDistance = String($(this).val() || '').trim();

                $(this).removeClass('mandatory-error');
                $(this).css('border-color', '');

                me.updateExcavatorHaulingDistance(
                    excavatorName,
                    haulingDistance
                );
            }
        );

        // ----------------------------------------------------
        // Blank-style support for time inputs
        // ----------------------------------------------------
        if (!document.getElementById('exc-load-time-blank-style')) {
            $('head').append(`
                <style id="exc-load-time-blank-style">
                    .exc-load-time-blank::-webkit-datetime-edit {
                        color: transparent;
                    }

                    .exc-load-time-blank::-webkit-datetime-edit-fields-wrapper {
                        color: transparent;
                    }

                    .exc-load-time-blank::-webkit-datetime-edit-hour-field,
                    .exc-load-time-blank::-webkit-datetime-edit-minute-field,
                    .exc-load-time-blank::-webkit-datetime-edit-second-field,
                    .exc-load-time-blank::-webkit-datetime-edit-text {
                        color: transparent;
                    }

                    .exc-load-time-blank:focus::-webkit-datetime-edit,
                    .exc-load-time-blank:focus::-webkit-datetime-edit-fields-wrapper,
                    .exc-load-time-blank:focus::-webkit-datetime-edit-hour-field,
                    .exc-load-time-blank:focus::-webkit-datetime-edit-minute-field,
                    .exc-load-time-blank:focus::-webkit-datetime-edit-second-field,
                    .exc-load-time-blank:focus::-webkit-datetime-edit-text {
                        color: inherit;
                    }

                    .exc-load-time-blank::-webkit-calendar-picker-indicator {
                        opacity: 1;
                        cursor: pointer;
                    }

                    .exc-load-time-input {
                        cursor: pointer;
                    }
                </style>
            `);
        }

        const toggleExcLoadTimeBlankState = (inputElement) => {
            const input = $(inputElement);
            const value = String(input.val() || '').trim();

            if (value) {
                input.removeClass('exc-load-time-blank');
            } else {
                input.addClass('exc-load-time-blank');
            }
        };

        // ----------------------------------------------------
        // Custom Excavator Load Time Slider Picker
        // ----------------------------------------------------

        if (!document.getElementById('exc-slider-time-picker-style')) {
            $('head').append(`
                <style id="exc-slider-time-picker-style">

                    .exc-load-time-input {
                        cursor: pointer;
                        background: var(--control-bg, #fff) !important;
                    }

                    .exc-slider-time-picker {
                        position: fixed;
                        z-index: 99999;
                        width: 210px;
                        background: #fff;
                        border: 1px solid #d8d8d8;
                        border-radius: 10px;
                        box-shadow: 0 4px 14px rgba(0,0,0,0.16);
                        overflow: hidden;
                        padding-top: 7px;
                    }

                    .exc-slider-time-display {
                        font-size: 13px;
                        padding: 2px 12px 5px 12px;
                        color: #444;
                        font-variant-numeric: tabular-nums;
                    }

                    .exc-slider-row {
                        display: flex;
                        align-items: center;
                        height: 22px;
                        padding: 0 12px;
                    }

                    .exc-slider-row input[type="range"] {
                        width: 100%;
                        height: 14px;
                        margin: 0;
                        cursor: pointer;
                    }

                    .exc-slider-now {
                        margin-top: 5px;
                        border-top: 1px solid #e5e5e5;
                        text-align: center;
                        padding: 8px 0;
                        cursor: pointer;
                        font-size: 13px;
                        color: #444;
                        background: #fff;
                    }

                    .exc-slider-now:hover {
                        background: #f5f5f5;
                    }

                    .exc-slider-clear {
                        border-top: 1px solid #e5e5e5;
                        text-align: center;
                        padding: 8px 0;
                        cursor: pointer;
                        font-size: 13px;
                        color: #444;
                        background: #fff;
                    }

                    .exc-slider-clear:hover {
                        background: #f5f5f5;
                    }

                </style>
            `);
        }

        const padExcTime = value =>
            String(value).padStart(2, '0');

        const setExcSliderTime = (
            picker,
            hour,
            minute,
            second
        ) => {
            const time =
                `${padExcTime(hour)}:` +
                `${padExcTime(minute)}:` +
                `${padExcTime(second)}`;

            picker.find('.exc-slider-time-display').text(time);

            return time;
        };

        const closeExcSliderTimePicker = () => {
            $('.exc-slider-time-picker').remove();
        };

        const openExcSliderTimePicker = inputElement => {

            closeExcSliderTimePicker();

            const input = $(inputElement);

            let value = String(input.val() || '').trim();

            let hour = 18;
            let minute = 0;
            let second = 0;

            if (/^\d{2}:\d{2}:\d{2}$/.test(value)) {
                const parts = value.split(':').map(Number);

                hour = parts[0];
                minute = parts[1];
                second = parts[2];
            }

            const picker = $(`
                <div class="exc-slider-time-picker">

                    <div class="exc-slider-time-display">
                        ${padExcTime(hour)}:${padExcTime(minute)}:${padExcTime(second)}
                    </div>

                    <div class="exc-slider-row">
                        <input
                            type="range"
                            class="exc-slider-hour"
                            min="0"
                            max="23"
                            step="1"
                            value="${hour}">
                    </div>

                    <div class="exc-slider-row">
                        <input
                            type="range"
                            class="exc-slider-minute"
                            min="0"
                            max="59"
                            step="1"
                            value="${minute}">
                    </div>

                    <div class="exc-slider-row">
                        <input
                            type="range"
                            class="exc-slider-second"
                            min="0"
                            max="59"
                            step="1"
                            value="${second}">
                    </div>

                    <div class="exc-slider-now">
                        Now
                    </div>

                    <div class="exc-slider-clear">
                        Clear
                    </div>

                </div>
            `);

            $('body').append(picker);

            const rect = inputElement.getBoundingClientRect();

            picker.css({
                top: `${rect.bottom + 3}px`,
                left: `${rect.left}px`
            });

            const updateFromSliders = () => {

                const hour = Number(
                    picker.find('.exc-slider-hour').val()
                );

                const minute = Number(
                    picker.find('.exc-slider-minute').val()
                );

                const second = Number(
                    picker.find('.exc-slider-second').val()
                );

                const time = setExcSliderTime(
                    picker,
                    hour,
                    minute,
                    second
                );

                input.val(time);
                input.removeClass('exc-load-time-blank');

                input.trigger('change');
            };

            picker.on(
                'input',
                'input[type="range"]',
                updateFromSliders
            );

            picker.on(
                'click',
                '.exc-slider-clear',
                function() {

                    input.val('');
                    input.addClass('exc-load-time-blank');

                    input.trigger('change');

                    closeExcSliderTimePicker();
                }
            );

            picker.on(
                'click',
                '.exc-slider-now',
                function() {

                    const now = new Date();

                    const hour = now.getHours();
                    const minute = now.getMinutes();
                    const second = now.getSeconds();

                    picker.find('.exc-slider-hour').val(hour);
                    picker.find('.exc-slider-minute').val(minute);
                    picker.find('.exc-slider-second').val(second);

                    const time = setExcSliderTime(
                        picker,
                        hour,
                        minute,
                        second
                    );

                    input.val(time);
                    input.removeClass('exc-load-time-blank');

                    input.trigger('change');

                    closeExcSliderTimePicker();
                }
            );
        };


        // ----------------------------------------------------
        // Allow manual HH:MM:SS editing like Day Shift Start
        // ----------------------------------------------------
        $(document).off(
            'blur.excavator_load_time_manual change.excavator_load_time_manual',
            '.exc-start-load-time, .exc-end-load-time'
        );

        $(document).on(
            'blur.excavator_load_time_manual change.excavator_load_time_manual',
            '.exc-start-load-time, .exc-end-load-time',
            function() {

                const input = $(this);
                let value = String(input.val() || '').trim();

                // Blank is allowed.
                if (!value) {
                    input.val('');
                    input.addClass('exc-load-time-blank');
                    return;
                }

                // Accept HH:MM and normalize to HH:MM:00.
                if (/^\d{1,2}:\d{2}$/.test(value)) {
                    value = value + ':00';
                }

                // Validate HH:MM:SS.
                const match = value.match(
                    /^(\d{1,2}):(\d{2}):(\d{2})$/
                );

                if (!match) {
                    frappe.msgprint(
                        __('Please enter time as HH:MM:SS')
                    );
                    input.focus();
                    return;
                }

                const hour = Number(match[1]);
                const minute = Number(match[2]);
                const second = Number(match[3]);

                if (
                    hour > 23 ||
                    minute > 59 ||
                    second > 59
                ) {
                    frappe.msgprint(
                        __('Please enter a valid time.')
                    );
                    input.focus();
                    return;
                }

                value =
                    String(hour).padStart(2, '0') + ':' +
                    String(minute).padStart(2, '0') + ':' +
                    String(second).padStart(2, '0');

                input.val(value);
                input.removeClass('exc-load-time-blank');
            }
        );

        $(document).off(
            'click.excavator_slider_time_picker',
            '.exc-start-load-time, .exc-end-load-time'
        );

        $(document).on(
            'click.excavator_slider_time_picker',
            '.exc-start-load-time, .exc-end-load-time',
            function(event) {

                event.stopPropagation();

                openExcSliderTimePicker(this);
            }
        );


        $(document).off(
            'click.excavator_slider_time_picker_close'
        );

        $(document).on(
            'click.excavator_slider_time_picker_close',
            function(event) {

                if (
                    !$(event.target).closest(
                        '.exc-slider-time-picker, .exc-load-time-input'
                    ).length
                ) {
                    closeExcSliderTimePicker();
                }
            }
        );

        // ----------------------------------------------------
        // Excavator Start / End Load Time
        // ----------------------------------------------------
        $(document).off(
            'change.excavator_load_time input.excavator_load_time',
            '.exc-start-load-time, .exc-end-load-time'
        );

        // Open native time selector when user clicks anywhere
        // inside Start Load Time or End Load Time.
        $(document).off(
            'click.excavator_time_picker',
            '.exc-start-load-time, .exc-end-load-time'
        );

        $(document).on(
            'click.excavator_time_picker',
            '.exc-start-load-time, .exc-end-load-time',
            function() {
                try {
                    if (typeof this.showPicker === 'function') {
                        this.showPicker();
                    }
                } catch (error) {
                    // Browser will still allow the normal native picker.
                }
            }
        );

        $(document).on(
            'change.excavator_load_time input.excavator_load_time',
            '.exc-start-load-time, .exc-end-load-time',
            function() {
                const excavatorName = $(this).data('excavator-name');

                toggleExcLoadTimeBlankState(this);

                const block = $(this).closest('.excavator-header');

                const startInput = block.find('.exc-start-load-time');
                const endInput = block.find('.exc-end-load-time');

                toggleExcLoadTimeBlankState(startInput);
                toggleExcLoadTimeBlankState(endInput);

                const startLoadTime = String(
                    startInput.val() || ''
                ).trim();

                const endLoadTime = String(
                    endInput.val() || ''
                ).trim();

                me.updateExcavatorLoadTimes(
                    excavatorName,
                    startLoadTime,
                    endLoadTime
                );
            }
        );

        me.frm.fields_dict.dnd_html_excavator_ui.$wrapper
            .find('.exc-load-time-input')
            .each(function() {
                toggleExcLoadTimeBlankState(this);
            });

        // Populate all Hauling Distance selectors from master.
        frappe.db.get_list('Hauling Distance Meter', {
            fields: [
                'name',
                'hauling_distance',
                'from_distance',
                'to_distance',
                'range_type'
            ],
            filters: {
                is_active: 1
            },
            order_by: 'to_distance asc, from_distance asc',
            limit: 1000
        }).then(rows => {
            const wrapper = me.frm.fields_dict.dnd_html_excavator_ui.$wrapper;

            // One shared datalist for all excavator Hauling Distance fields.
            wrapper.find('#hauling-distance-master-options').remove();

            const datalist = $('<datalist id="hauling-distance-master-options"></datalist>');

            rows.forEach(row => {
                const value = row.hauling_distance || row.name;

                datalist.append(
                    $('<option></option>').attr('value', value)
                );
            });

            wrapper.append(datalist);

            // Restore current saved value when the UI is rendered.
            wrapper.find('.exc-hauling-distance').each(function() {
                const input = $(this);
                const currentValue = String(
                    input.attr('data-current-value') || ''
                );

                input.val(currentValue);
            });
        }).catch(error => {
            console.error(
                'Unable to load Hauling Distance Meter options:',
                error
            );
        });
    }

    updateExcavatorToArea(excavatorName, toArea) {
        if (!excavatorName) return;

        const rows = (this.frm.doc.truck_loads || []).filter(
            row => row.asset_name_shoval === excavatorName
        );

        rows.forEach(row => {

            row.exc_to_area = toArea || '';

            frappe.model.set_value(
                row.doctype,
                row.name,
                'exc_to_area',
                toArea || ''
            );
        });

        if (typeof this.frm.dirty === 'function') {
            this.frm.dirty();
        } else {
            this.frm.doc.__unsaved = 1;
        }

        this.frm.refresh_field('truck_loads');

        if (this.frm.toolbar) {
            this.frm.toolbar.refresh();
        }
    }

    updateExcavatorHaulingDistance(excavatorName, haulingDistance) {
        if (!excavatorName) return;

        const rows = (this.frm.doc.truck_loads || []).filter(
            row => row.asset_name_shoval === excavatorName
        );

        rows.forEach(row => {
            row.exc_hauling_distance_meter = haulingDistance || '';

            frappe.model.set_value(
                row.doctype,
                row.name,
                'exc_hauling_distance_meter',
                haulingDistance || ''
            );
        });

        if (typeof this.frm.dirty === 'function') {
            this.frm.dirty();
        } else {
            this.frm.doc.__unsaved = 1;
        }

        this.frm.refresh_field('truck_loads');

        if (this.frm.toolbar) {
            this.frm.toolbar.refresh();
        }
    }

    updateExcavatorLoadTimes(
        excavatorName,
        startLoadTime,
        endLoadTime
    ) {
        if (!excavatorName) return;

        const startValue =
            String(startLoadTime || '').trim();

        const endValue =
            String(endLoadTime || '').trim();

        const rows = (this.frm.doc.truck_loads || []).filter(
            row => row.asset_name_shoval === excavatorName
        );

        rows.forEach(row => {

            // Keep child document model in sync immediately.
            row.exc_start_load_time = startValue;
            row.exc_end_load_time = endValue;

            frappe.model.set_value(
                row.doctype,
                row.name,
                'exc_start_load_time',
                startValue
            );

            frappe.model.set_value(
                row.doctype,
                row.name,
                'exc_end_load_time',
                endValue
            );
        });

        if (typeof this.frm.dirty === 'function') {
            this.frm.dirty();
        } else {
            this.frm.doc.__unsaved = 1;
        }

        this.frm.refresh_field('truck_loads');

        if (this.frm.toolbar) {
            this.frm.toolbar.refresh();
        }

        console.log(
            'Saved Load Times to Truck Loads:',
            {
                excavator: excavatorName,
                start: startValue,
                end: endValue,
                trucks: rows.map(row => row.asset_name_truck)
            }
        );
    }

    updateExcavatorHours(excavatorName, start, stop, total) {
        if (!excavatorName) return;

        const rows = (this.frm.doc.truck_loads || []).filter(r => r.asset_name_shoval === excavatorName);

        rows.forEach(row => {
            row.exc_start_hours = start || 0;
            row.exc_stop_hours = stop || 0;
            row.exc_total_hours = total || 0;

            frappe.model.set_value(row.doctype, row.name, 'exc_start_hours', start || 0);
            frappe.model.set_value(row.doctype, row.name, 'exc_stop_hours', stop || 0);
            frappe.model.set_value(row.doctype, row.name, 'exc_total_hours', total || 0);
        });

        this.frm.dirty = true;
        this.frm.doc.__unsaved = 1;
        this.frm.refresh_field('truck_loads');
        this.frm.toolbar.refresh();
    }

    updateTruckArea(rowName, area) {
        const row = this.frm.doc.truck_loads.find(r => r.name === rowName);
        if (row) {
            // Update the local data immediately
            row.mining_areas_trucks = area;
            
            // Mark the document as dirty
            this.frm.dirty = true;
            this.frm.doc.__unsaved = 1;
            
            // Use frappe.model.set_value to properly register the change
            frappe.model.set_value(row.doctype, row.name, 'mining_areas_trucks', area);
            
            // Update UI immediately
            const truckBlock = $(`.truck-block[data-row-name="${rowName}"]`);
            if (truckBlock.length) {
                const areaHtml = area ? `<div class="truck-area">Area: ${area}</div>` : '';
                truckBlock.find('.truck-area').remove();
                if (area) {
                    truckBlock.find('.truck-fields').append(areaHtml);
                }
            }
            
            // Refresh the child table field to show changes
            this.frm.refresh_field('truck_loads');
            
            // Update the toolbar to show save button
            this.frm.toolbar.refresh();
        }
    }

    updateExcavatorDefaultArea(excavatorName, area) {
        console.log(`Excavator ${excavatorName} primary area set to ${area}`);
        
        // Mark the document as dirty when excavator area changes
        this.frm.dirty = true;
        this.frm.doc.__unsaved = 1;
        
        // Update the toolbar to show save button
        this.frm.toolbar.refresh();
        
        // Can be extended to store default areas if needed
    }

    updateExcavatorAssignments() {
        console.log('Updating excavator assignments');
        const assignments = {};
        
        document.querySelectorAll('.excavator-block').forEach(block => {
            const excavatorName = block.querySelector('h4').textContent;
            if (excavatorName !== 'Unassigned Trucks') {
                block.querySelectorAll('.truck-block').forEach(truck => {
                    const truckName = truck.getAttribute('data-truck-name');
                    assignments[truckName] = excavatorName;
                });
            }
        });

        this.frm.doc.truck_loads.forEach(row => {
            const newExcavator = assignments[row.asset_name_truck];
            if (newExcavator && newExcavator !== row.asset_name_shoval) {
                frappe.model.set_value(row.doctype, row.name, 'asset_name_shoval', newExcavator);
            } else if (!newExcavator && row.asset_name_shoval) {
                frappe.model.set_value(row.doctype, row.name, 'asset_name_shoval', null);
            }
        });
        
        this.frm.refresh_field('truck_loads');
    }

   updateTruckField(rowName, fieldname, value) {
        const row = this.frm.doc.truck_loads.find(r => r.name === rowName);
        if (row) {
            // Update the local data immediately
            row[fieldname] = value;
            
            // Mark the document as dirty
            this.frm.dirty = true;
            this.frm.doc.__unsaved = 1;
            
            // Use frappe.model.set_value to properly register the change
            frappe.model.set_value(row.doctype, row.name, fieldname, value);
            
            // Handle geo layer material mapping
            if (fieldname === 'geo_mat_layer_truck' && this.frm.geoMaterialMap && this.frm.geoMaterialMap[value]) {
                const matType = this.frm.geoMaterialMap[value];
                row.mat_type = matType;
                frappe.model.set_value(row.doctype, row.name, 'mat_type', matType);
            }
            
            // Calculate BCMS if loads changed
            if (fieldname === 'loads') {
                this.calculateBCMS(row.doctype, row.name);
            }
            
            // Refresh the child table field to show changes
            this.frm.refresh_field('truck_loads');
            
            // Update the toolbar to show save button
            this.frm.toolbar.refresh();
        }
    }


updateDozerField(dozerName, fieldname, value) {
        const row = this.frm.doc.dozer_production?.find(d => d.asset_name === dozerName);
        if (row) {
            // Update the local data immediately
            row[fieldname] = value;
            
            // Mark the document as dirty
            this.frm.dirty = true;
            this.frm.doc.__unsaved = 1;
            
            // Use frappe.model.set_value to properly register the change
            frappe.model.set_value(row.doctype, row.name, fieldname, value);

            // Handle geo layer material mapping
            if (fieldname === 'dozer_geo_mat_layer' && this.frm.geoMaterialMap && this.frm.geoMaterialMap[value]) {
                const matType = this.frm.geoMaterialMap[value];
                row.mat_type = matType;
                frappe.model.set_value(row.doctype, row.name, 'mat_type', matType);
            }
            
            // Handle service type changes
            if (fieldname === 'dozer_service') {
                this.handleDozerServiceChange(row);
            }
            
            // Refresh the child table field to show changes
            this.frm.refresh_field('dozer_production');
            
            // Update the toolbar to show save button
            this.frm.toolbar.refresh();
        }
    }

calculateBCMS(doctype, name) {
    const row = this.frm.doc.truck_loads.find(r => r.name === name);
    if (row) {
        const loads = parseFloat(row.loads) || 0;
        const tf = parseFloat(row.tub_factor) || 0;
        const bcms = (!isNaN(loads) && !isNaN(tf)) ? loads * tf : 0;
        
        // Update local data
        row.bcms = bcms;
        
        // Mark document as dirty
        this.frm.dirty = true;
        this.frm.doc.__unsaved = 1;
        
        // Use frappe.model.set_value to register the change
        frappe.model.set_value(doctype, name, 'bcms', bcms);
        
        // Refresh the field
        this.frm.refresh_field('truck_loads');
        this.frm.toolbar.refresh();
    }
}

    // Add this to your HourlyProductionUI class
    // Inside your class definition (after all other methods), add:
    cleanup() {
    // Remove any DOM elements or event listeners your UI created
    const container = this.frm.fields_dict.dnd_html_excavator_ui.$wrapper[0];
    if (container) {
        container.innerHTML = '';
    }
    
    // Clear any jQuery event handlers
    $(document).off('.hourlyProductionUI');
    
    // Clear any other references
    this.container = null;
    // Add any other cleanup needed for your specific UI
    }

    // Add this new method to the class:
   // Replace the existing handleDozerServiceChange method with this updated version:

// Replace the existing handleDozerServiceChange method with this final version:
async handleDozerServiceChange(row) {

    let bcmValue = 0;
    const location = String(this.frm.doc.location || '').trim();

    // --- Production Dozing rules ---
    // Kriel Rehabilitation uses fixed BCM/hour rates.
    // All other locations retain their existing rates.
    if (row.dozer_service === 'Production Dozing-50m') {
        bcmValue = location === 'Kriel Rehabilitation' ? 75 : 180;
    }
    else if (row.dozer_service === 'Production Dozing-100m') {
        bcmValue = location === 'Kriel Rehabilitation' ? 150 : 200;
    }

    // --- Zero-BCM services ---
    else if (
        row.dozer_service === 'No Dozing' ||
        row.dozer_service === 'Tip Dozing' ||
        row.dozer_service === 'Levelling'
    ) {
        bcmValue = 0;

        // Reset related fields
        row.dozer_geo_mat_layer = '';
        row.mining_areas_dozer_child = '';
        row.mat_type = '';

        frappe.model.set_value(row.doctype, row.name, 'dozer_geo_mat_layer', '');
        frappe.model.set_value(row.doctype, row.name, 'mining_areas_dozer_child', '');
        frappe.model.set_value(row.doctype, row.name, 'mat_type', '');
    }

    // Apply BCM/hour
    row.bcm_hour = bcmValue;
    frappe.model.set_value(row.doctype, row.name, 'bcm_hour', bcmValue);

    // Update UI control
    const block = $(`.dozer-block[data-dozer-name="${row.asset_name}"]`);
    if (block.length) {
        block.find('.dozer-production').val(bcmValue);
    }

    // Mark doc as dirty
    this.frm.dirty = true;
    this.frm.doc.__unsaved = 1;

    this.frm.refresh_field('dozer_production');
    this.frm.toolbar.refresh();
}


    

     calculateBCMS(doctype, name) {
        const row = this.frm.doc.truck_loads.find(r => r.name === name);
        if (row) {
            const loads = parseFloat(row.loads) || 0;
            const tf = parseFloat(row.tub_factor) || 0;
            const bcms = (!isNaN(loads) && !isNaN(tf)) ? loads * tf : 0;
            
            // Update local data
            row.bcms = bcms;
            
            // Mark document as dirty
            this.frm.dirty = true;
            this.frm.doc.__unsaved = 1;
            
            // Use frappe.model.set_value to register the change
            frappe.model.set_value(doctype, name, 'bcms', bcms);
            
            // Refresh the field
            this.frm.refresh_field('truck_loads');
            this.frm.toolbar.refresh();
        }
    }
};

console.log('HourlyProductionUI class registered');

// === FORCE HOUR REPORT MPP MONTHLY STATS START ===
(function () {
  function n(v) {
    const x = flt(v || 0);
    return isFinite(x) ? x : 0;
  }

  function fmt(v, decimals) {
    v = n(v);

    const rounded = Number(v.toFixed(decimals));

    return rounded.toLocaleString(undefined, {
      minimumFractionDigits: 0,
      maximumFractionDigits: decimals
    });
  }

  function getPlanName(frm) {
    return (
      frm.doc.month_prod_planning ||
      frm.doc.monthly_production_planning ||
      frm.doc.monthly_planning ||
      ''
    );
  }

  async function getMPP(frm) {
    const linkedPlan = getPlanName(frm);

    if (linkedPlan) {
      try {
        return await frappe.db.get_doc('Monthly Production Planning', linkedPlan);
      } catch (e) {
        console.warn('Could not fetch linked MPP:', linkedPlan, e);
      }
    }

    const site = frm.doc.location || frm.doc.site || '';
    const date = frm.doc.prod_date || frm.doc.date || frm.doc.shift_date || '';

    if (!site || !date) return null;

    try {
      const docs = await frappe.db.get_list('Monthly Production Planning', {
        filters: [
          ['location', '=', site],
          ['prod_month_start_date', '<=', date],
          ['prod_month_end_date', '>=', date]
        ],
        fields: ['name'],
        limit: 1
      });

      if (!docs || !docs.length) return null;

      return await frappe.db.get_doc('Monthly Production Planning', docs[0].name);
    } catch (e) {
      console.warn('Could not fetch MPP by site/date', e);
      return null;
    }
  }

  function getRows(root) {
    const rows = {};

    $(root).find('table').each(function () {
      const tableText = $(this).text();

      if (!tableText.includes('Monthly Statistics')) return;

      $(this).find('tr').each(function () {
        const cells = $(this).find('td, th');

        if (cells.length < 2) return;

        const label = $(cells[0]).text().trim();

        if (!label) return;

        rows[label.toLowerCase()] = $(cells[1]);
      });
    });

    return rows;
  }

  function setRow(rows, label, value, decimals) {
    const cell = rows[String(label).toLowerCase()];

    if (!cell || !cell.length) return;

    cell.text(fmt(value, decimals));
  }

  async function forceMonthlyStatsFromMPP(frm) {
    try {
      if (!frm || frm.doctype !== 'Hourly Production') return;

      const mpp = await getMPP(frm);

      if (!mpp) {
        console.warn('DISPLAY MPP STATS: No Monthly Production Planning found.');
        return;
      }

      const root =
        frm.fields_dict.hp_report && frm.fields_dict.hp_report.$wrapper
          ? frm.fields_dict.hp_report.$wrapper
          : frm.$wrapper;

      const rows = getRows(root);

      if (!rows['monthly target bcm']) {
        console.warn('DISPLAY MPP STATS: Monthly Statistics table not found yet.');
        return;
      }

      const monthlyTarget = n(mpp.monthly_target_bcm);
      const dailyTarget = n(mpp.target_bcm_day);
      const targetHourlyRate = n(mpp.target_bcm_hour);
      const productionToDate = n(mpp.month_actual_bcm);
      const remainingBcm = Math.max(monthlyTarget - productionToDate, 0);
      const currentRate = n(mpp.mtd_bcm_hour);
      const remainingHours = n(mpp.month_remaining_prod_hours);
      const requiredRate = remainingHours > 0 ? remainingBcm / remainingHours : 0;
      const stripRatio = n(mpp.strip_ratio);
      const forecastedBcm = n(mpp.month_forecated_bcm);

      setRow(rows, 'Monthly Target BCM', monthlyTarget, 0);
      setRow(rows, 'Daily Target', dailyTarget, 0);
      setRow(rows, 'Target Hourly Rate', targetHourlyRate, 0);
      setRow(rows, 'Production to Date', productionToDate, 0);
      setRow(rows, 'Remaining BCM', remainingBcm, 0);
      setRow(rows, 'Current Rate', currentRate, 3);
      setRow(rows, 'Required Rate', requiredRate, 3);
      setRow(rows, 'Strip Ratio', stripRatio, 3);
      setRow(rows, 'Forecasted BCM', forecastedBcm, 1);

      console.log('DISPLAYED HOUR REPORT MONTHLY STATS FROM MPP OK', {
        mpp: mpp.name,
        monthlyTarget,
        dailyTarget,
        targetHourlyRate,
        productionToDate,
        remainingBcm,
        currentRate,
        requiredRate,
        stripRatio,
        forecastedBcm
      });
    } catch (e) {
      console.error('forceMonthlyStatsFromMPP failed', e);
    }
  }

  function runForce(frm) {
    setTimeout(() => forceMonthlyStatsFromMPP(frm), 100);
    setTimeout(() => forceMonthlyStatsFromMPP(frm), 500);
    setTimeout(() => forceMonthlyStatsFromMPP(frm), 1200);
    setTimeout(() => forceMonthlyStatsFromMPP(frm), 2500);
    setTimeout(() => forceMonthlyStatsFromMPP(frm), 4000);
  }

  function installPatch() {
    if (!window.is_production || !is_production.ui || !is_production.ui.HourlyProductionUI) {
      setTimeout(installPatch, 300);
      return;
    }

    const proto = is_production.ui.HourlyProductionUI.prototype;

    if (proto.__display_mpp_monthly_stats_installed) return;

    proto.__display_mpp_monthly_stats_installed = true;

    const originalLoadUI = proto.loadUI;

    proto.loadUI = function () {
      const result = originalLoadUI.apply(this, arguments);
      runForce(this.frm);
      return result;
    };

    if (proto.renderReport) {
      const originalRenderReport = proto.renderReport;

      proto.renderReport = function () {
        const result = originalRenderReport.apply(this, arguments);
        runForce(this.frm);
        return result;
      };
    }

    console.log('Installed display-only HourlyProductionUI MPP Monthly Statistics patch.');
  }

  installPatch();

  $(document).on('shown.bs.tab click', function () {
    if (window.cur_frm && window.cur_frm.doctype === 'Hourly Production') {
      runForce(window.cur_frm);
    }
  });

  const observer = new MutationObserver(function () {
    if (window.cur_frm && window.cur_frm.doctype === 'Hourly Production') {
      runForce(window.cur_frm);
    }
  });

  $(function () {
    observer.observe(document.body, {
      childList: true,
      subtree: true
    });
  });
})();
// === FORCE HOUR REPORT MPP MONTHLY STATS END ===
