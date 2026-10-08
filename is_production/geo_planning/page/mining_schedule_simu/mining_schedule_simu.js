frappe.pages["mining-schedule-simu"].on_page_load = function(wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: "Mining Schedule Simulator",
        single_column: true
    });

    const method_base = "is_production.geo_planning.page.mining_schedule_simu.mining_schedule_simu";

    let THREE = null, OrbitControls = null, scene = null, camera = null, renderer = null, controls = null;
    let blockGroup = null, blockMeshes = [], payload = null, rows = [], currentStep = -1, timer = null;
    let stackItems = [];
    const model = { cx: 0, cy: 0, scale: 1, radius: 1200 };
    const colours = { waiting: 0xd1d5db, partial: 0xf97316, current: 0xfacc15, complete: 0x22c55e, edge: 0x111827 };

    $(page.body).html(`
        <style>
            .mss-shell{height:calc(100vh - 86px);display:grid;grid-template-columns:390px 1fr;gap:10px;padding:8px;background:#f6f7f9}
            .mss-side{background:white;border:1px solid #e5e7eb;border-radius:12px;padding:12px;overflow-y:auto;box-shadow:0 1px 5px rgba(0,0,0,.05)}
            .mss-main{position:relative;background:white;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;box-shadow:0 1px 5px rgba(0,0,0,.05)}
            #mss_viewer{position:absolute;inset:0;background:linear-gradient(#f8fafc,#e5edf5)}
            .mss-title{font-size:15px;font-weight:700;margin-bottom:10px}.mss-section{border-top:1px solid #edf0f2;margin-top:12px;padding-top:12px}
            .mss-control{margin-bottom:9px}.mss-grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px}.mss-actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}
            .mss-actions .btn{width:100%}.mss-help{color:#6b7280;font-size:11px;line-height:1.35;margin-top:6px}
            .mss-box{position:absolute;background:rgba(255,255,255,.94);border:1px solid #d1d5db;border-radius:10px;padding:10px 12px;font-size:12px;line-height:1.45;box-shadow:0 2px 10px rgba(0,0,0,.08);pointer-events:none;z-index:5}
            #mss_info{top:12px;left:12px;min-width:300px;max-width:470px}#mss_legend{right:12px;bottom:12px;min-width:280px}
            .mss-loading{position:absolute;inset:0;display:none;align-items:center;justify-content:center;background:rgba(255,255,255,.72);z-index:20;font-weight:700}
            .mss-swatch{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:6px;border:1px solid rgba(0,0,0,.25)}
            .mss-progress{height:8px;background:#e5e7eb;border-radius:99px;overflow:hidden;margin-top:8px}.mss-progress-bar{height:100%;width:0%;background:#22c55e}
        </style>
        <div class="mss-shell">
            <div class="mss-side">
                <div class="mss-title">Mining Schedule Simulator</div>
                <div id="scenario_name_control" class="mss-control"></div>
                <div id="existing_scenario_control" class="mss-control"></div>
                <div class="mss-section">
                    <div id="geo_project_control" class="mss-control"></div>
                    <div id="geo_pit_layout_control" class="mss-control"></div>
                    <div id="material_stack_control" class="mss-control"></div>
                    <button class="btn btn-default btn-sm" id="load_stack_items_btn" style="width:100%;margin-bottom:8px">Load Stack Items</button>
                    <div id="schedule_scope_control" class="mss-control"></div>
                    <div id="stack_item_control" class="mss-control"></div>
                    <div class="mss-grid2"><div id="material_seam_control" class="mss-control"></div><div id="value_type_control" class="mss-control"></div></div>
                    <div class="mss-help">Select a Material Stack, load items, then choose the stack item. Do not type the seam manually.</div>
                </div>
                <div class="mss-section">
                    <div class="mss-grid2"><div id="start_date_control" class="mss-control"></div><div id="end_date_control" class="mss-control"></div></div>
                    <div class="mss-grid2"><div id="shift_hours_weekday_control" class="mss-control"></div><div id="shifts_per_weekday_control" class="mss-control"></div></div>
                    <div class="mss-grid2"><div id="shift_hours_saturday_control" class="mss-control"></div><div id="shifts_per_saturday_control" class="mss-control"></div></div>
                    <div class="mss-grid2"><div id="shift_hours_sunday_control" class="mss-control"></div><div id="shifts_per_sunday_control" class="mss-control"></div></div>
                </div>
                <div class="mss-section">
                    <div class="mss-grid2"><div id="number_of_excavators_control" class="mss-control"></div><div id="capacity_per_excavator_hour_control" class="mss-control"></div></div>
                    <div class="mss-grid2"><div id="utilisation_percent_control" class="mss-control"></div><div id="availability_percent_control" class="mss-control"></div></div>
                    <div id="schedule_method_control" class="mss-control"></div>
                    <div class="checkbox"><label><input type="checkbox" id="overwrite_schedule_toggle"> Overwrite existing scenario</label></div>
                    <div class="mss-help">Blank utilisation/availability = 100%. Blocks can split across periods by capacity.</div>
                </div>
                <div class="mss-actions">
                    <button class="btn btn-primary" id="generate_schedule_btn">Generate Schedule</button><button class="btn btn-default" id="load_schedule_btn">Load Animation</button>
                    <button class="btn btn-success" id="play_btn">Play</button><button class="btn btn-warning" id="pause_btn">Pause</button>
                    <button class="btn btn-default" id="step_btn">Step</button><button class="btn btn-default" id="reset_btn">Reset</button>
                </div>
                <div class="mss-section"><div id="animation_speed_control" class="mss-control"></div><div class="mss-help">Pilot animation: one row can be a whole block or part of a block.</div></div>
            </div>
            <div class="mss-main">
                <div id="mss_viewer"></div><div class="mss-loading" id="mss_loading">Loading...</div><div class="mss-box" id="mss_info">No schedule loaded.</div>
                <div class="mss-box" id="mss_legend"><b>Legend</b><br>
                    <span class="mss-swatch" style="background:#d1d5db"></span>Not reached<br>
                    <span class="mss-swatch" style="background:#facc15"></span>Current scheduled portion<br>
                    <span class="mss-swatch" style="background:#f97316"></span>Partially complete<br>
                    <span class="mss-swatch" style="background:#22c55e"></span>Block complete
                    <div class="mss-progress"><div class="mss-progress-bar" id="mss_progress_bar"></div></div>
                </div>
            </div>
        </div>`);

    const c = {};
    function make(parent, df){ c[df.fieldname] = frappe.ui.form.make_control({parent: $(parent), df, render_input: true}); return c[df.fieldname]; }
    function val(f){ return c[f] ? c[f].get_value() : ""; }

    make("#scenario_name_control", {fieldtype:"Data", label:"Scenario Name", fieldname:"scenario_name"});
    make("#existing_scenario_control", {fieldtype:"Autocomplete", label:"Existing Scenario", fieldname:"existing_scenario", get_query: txt => frappe.call({method:`${method_base}.get_recent_schedule_scenarios`, args:{txt:txt||""}}).then(r => (r.message||[]).map(x => x.value))});
    make("#geo_project_control", {fieldtype:"Link", label:"Geo Project", fieldname:"geo_project", options:"Geo Project"});
    make("#geo_pit_layout_control", {fieldtype:"Link", label:"Geo Pit Layout", fieldname:"geo_pit_layout", options:"Geo Pit Layout"});
    make("#material_stack_control", {fieldtype:"Link", label:"Material Stack", fieldname:"material_stack", options:"Geo Pit Layout Material Stack"});
    make("#schedule_scope_control", {fieldtype:"Select", label:"Schedule Scope", fieldname:"schedule_scope", options:"\nSingle Material\nWhole Stack"});
    make("#stack_item_control", {fieldtype:"Select", label:"Stack Item", fieldname:"stack_item", options:"\n"});
    make("#material_seam_control", {fieldtype:"Data", label:"Material / Seam", fieldname:"material_seam", read_only:1});
    make("#value_type_control", {fieldtype:"Select", label:"Value Type", fieldname:"value_type", options:"\nThickness\nDepth\nElevation\nQuality\nDensity\nOther", read_only:1});
    make("#start_date_control", {fieldtype:"Date", label:"Start Date", fieldname:"start_date"});
    make("#end_date_control", {fieldtype:"Date", label:"End Date", fieldname:"end_date"});
    make("#shift_hours_weekday_control", {fieldtype:"Float", label:"Weekday Shift Hours", fieldname:"shift_hours_weekday"});
    make("#shifts_per_weekday_control", {fieldtype:"Float", label:"Weekday Shifts", fieldname:"shifts_per_weekday"});
    make("#shift_hours_saturday_control", {fieldtype:"Float", label:"Saturday Shift Hours", fieldname:"shift_hours_saturday"});
    make("#shifts_per_saturday_control", {fieldtype:"Float", label:"Saturday Shifts", fieldname:"shifts_per_saturday"});
    make("#shift_hours_sunday_control", {fieldtype:"Float", label:"Sunday Shift Hours", fieldname:"shift_hours_sunday"});
    make("#shifts_per_sunday_control", {fieldtype:"Float", label:"Sunday Shifts", fieldname:"shifts_per_sunday"});
    make("#number_of_excavators_control", {fieldtype:"Int", label:"Excavators", fieldname:"number_of_excavators"});
    make("#capacity_per_excavator_hour_control", {fieldtype:"Float", label:"Capacity / Excavator / Hour", fieldname:"capacity_per_excavator_hour"});
    make("#utilisation_percent_control", {fieldtype:"Percent", label:"Utilisation %", fieldname:"utilisation_percent"});
    make("#availability_percent_control", {fieldtype:"Percent", label:"Availability %", fieldname:"availability_percent"});
    make("#schedule_method_control", {fieldtype:"Select", label:"Schedule Method", fieldname:"schedule_method", options:"\nCut Block\nRow Column\nManual Sequence\nNearest Neighbour\nOptimised"});
    make("#animation_speed_control", {fieldtype:"Float", label:"Animation Speed ms / row", fieldname:"animation_speed"});

    $("#load_stack_items_btn").on("click", loadStackItems);
    $("#generate_schedule_btn").on("click", generateSchedule);
    $("#load_schedule_btn").on("click", () => loadAnimation());
    $("#play_btn").on("click", play);
    $("#pause_btn").on("click", pause);
    $("#step_btn").on("click", step);
    $("#reset_btn").on("click", reset);
    c.stack_item.$input.on("change", applyStackItem);

    // three.js is not on every desk page: load it for this page
    frappe.require("production_dependencies.bundle.js", initThree);

    function loading(msg){ $("#mss_loading").text(msg || "Loading...").css("display","flex"); }
    function done(){ $("#mss_loading").hide(); }
    function num(v){ if(v === undefined || v === null || v === "") return null; const n=Number(v); return Number.isFinite(n)?n:null; }

    function loadStackItems(){
        if(!val("material_stack")) return frappe.msgprint("Please select a Material Stack first.");
        frappe.call({method:`${method_base}.get_stack_material_items`, args:{material_stack:val("material_stack")}, callback:r=>{
            stackItems = r.message || [];
            const opts = [""].concat(stackItems.map((it,i)=>`${i+1}. ${it.material_seam} | ${it.value_type || ""}`));
            c.stack_item.df.options = opts.join("\n"); c.stack_item.refresh();
            frappe.show_alert({message:`${stackItems.length} stack item(s) loaded.`, indicator:"green"});
        }});
    }

    function applyStackItem(){
        const selected = val("stack_item"); if(!selected) return;
        const i = Number(String(selected).split(".")[0])-1; const it = stackItems[i]; if(!it) return;
        c.material_seam.set_value(it.material_seam || ""); c.value_type.set_value(it.value_type || "");
    }

    function generateSchedule(){
        if(val("schedule_scope")==="Whole Stack") return frappe.msgprint("Whole Stack scheduling is the next build step. Use Single Material for now.");
        if(val("schedule_scope")!=="Single Material") return frappe.msgprint("Please select Schedule Scope = Single Material.");
        const req = ["scenario_name","geo_project","geo_pit_layout","material_stack","stack_item","material_seam","value_type","start_date","end_date","shift_hours_weekday","shift_hours_saturday","shift_hours_sunday","shifts_per_weekday","shifts_per_saturday","shifts_per_sunday","number_of_excavators","capacity_per_excavator_hour","schedule_method"];
        for(const f of req){ if(!val(f)) return frappe.msgprint(`Please enter ${c[f].df.label}.`); }
        loading("Generating schedule...");
        frappe.call({method:`${method_base}.generate_schedule`, args:{
            scenario_name:val("scenario_name"), geo_project:val("geo_project"), geo_pit_layout:val("geo_pit_layout"), material_stack:val("material_stack"),
            material_seam:val("material_seam"), value_type:val("value_type"), start_date:val("start_date"), end_date:val("end_date"),
            shift_hours_weekday:num(val("shift_hours_weekday")), shift_hours_saturday:num(val("shift_hours_saturday")), shift_hours_sunday:num(val("shift_hours_sunday")),
            shifts_per_weekday:num(val("shifts_per_weekday")), shifts_per_saturday:num(val("shifts_per_saturday")), shifts_per_sunday:num(val("shifts_per_sunday")),
            number_of_excavators:num(val("number_of_excavators")), capacity_per_excavator_hour:num(val("capacity_per_excavator_hour")),
            utilisation_percent:num(val("utilisation_percent")), availability_percent:num(val("availability_percent")), schedule_method:val("schedule_method"),
            overwrite_existing:$("#overwrite_schedule_toggle").is(":checked")?1:0
        }, callback:r=>{
            done(); const m = r.message || {}; c.existing_scenario.set_value(m.schedule_scenario);
            frappe.msgprint(`Scenario: <b>${frappe.utils.escape_html(m.scenario_name||"")}</b><br>Periods: <b>${m.periods_created||0}</b><br>Schedule Rows: <b>${m.schedule_rows_created||0}</b><br>Blocks Completed: <b>${m.blocks_completed||0}</b><br>Volume Scheduled: <b>${Number(m.total_volume||0).toLocaleString(undefined,{maximumFractionDigits:2})}</b>`);
            loadAnimation(m.schedule_scenario);
        }, error:done});
    }

    function loadAnimation(optional){
        const scenario = optional || val("existing_scenario"); if(!scenario) return frappe.msgprint("Please select or generate a Mine Schedule Scenario.");
        loading("Loading animation payload...");
        frappe.call({method:`${method_base}.get_animation_payload`, args:{schedule_scenario:scenario}, callback:r=>{
            done(); payload = r.message || {}; rows = payload.blocks || []; currentStep = -1; buildScene(); updateState(); updateInfo();
        }, error:done});
    }

    function ensureThree(){
        THREE = is_production && is_production.THREE; OrbitControls = is_production && is_production.OrbitControls;
        if(!THREE || !OrbitControls){ frappe.msgprint("Three.js or OrbitControls is not loaded. Check production_dependencies.bundle.js."); return false; }
        return true;
    }

    function initThree(){
        if(!ensureThree()) return;
        const el = document.getElementById("mss_viewer");
        scene = new THREE.Scene(); scene.background = new THREE.Color(0xf8fafc);
        camera = new THREE.PerspectiveCamera(45,1,.1,10000000);
        renderer = new THREE.WebGLRenderer({antialias:true}); renderer.setPixelRatio(window.devicePixelRatio||1); el.appendChild(renderer.domElement);
        controls = new OrbitControls(camera, renderer.domElement); controls.enableDamping = true; controls.dampingFactor=.08;
        scene.add(new THREE.AmbientLight(0xffffff,.82)); const d = new THREE.DirectionalLight(0xffffff,.42); d.position.set(700,1200,800); scene.add(d);
        scene.add(new THREE.GridHelper(1600,32,0x94a3b8,0xe2e8f0)); blockGroup = new THREE.Group(); scene.add(blockGroup);
        window.addEventListener("resize", resize); resize(); resetCamera(); renderLoop();
    }
    function renderLoop(){ requestAnimationFrame(renderLoop); if(controls) controls.update(); if(renderer&&scene&&camera) renderer.render(scene,camera); }
    function resize(){ if(!renderer||!camera) return; const r=document.getElementById("mss_viewer").getBoundingClientRect(); renderer.setSize(Math.max(300,r.width|0),Math.max(300,r.height|0),true); camera.aspect=Math.max(300,r.width)/Math.max(300,r.height); camera.updateProjectionMatrix(); }
    function resetCamera(){ if(!camera||!controls) return; const r=model.radius||1200; camera.position.set(r*.85,r*1,r*1.25); controls.target.set(0,0,0); camera.near=.1; camera.far=r*50; camera.updateProjectionMatrix(); controls.update(); }

    function coords(g){ return g && g.coordinates && g.coordinates.length ? (g.coordinates[0] || []) : []; }
    function calcTransform(){
        const pts=[]; rows.forEach(row=>coords(row.polygon_geojson).forEach(p=>pts.push(p)));
        if(!pts.length){ model.cx=0; model.cy=0; model.scale=1; return; }
        const xs=pts.map(p=>+p[0]), ys=pts.map(p=>+p[1]); const minx=Math.min(...xs), maxx=Math.max(...xs), miny=Math.min(...ys), maxy=Math.max(...ys);
        model.cx=(minx+maxx)/2; model.cy=(miny+maxy)/2; model.scale=1400/Math.max(maxx-minx||1,maxy-miny||1,1);
    }
    function toXY(p){ return {x:(+p[0]-model.cx)*model.scale, y:-(+p[1]-model.cy)*model.scale}; }

    function buildScene(){
        clearBlocks(); calcTransform();
        const byBlock={}; rows.forEach(row=>{ if(!byBlock[row.mining_block]) byBlock[row.mining_block]=row; });
        Object.keys(byBlock).forEach(k=>{
            const row=byBlock[k], cs=coords(row.polygon_geojson); if(cs.length<3) return;
            const shape=new THREE.Shape(); let p=toXY(cs[0]); shape.moveTo(p.x,p.y); for(let i=1;i<cs.length;i++){ p=toXY(cs[i]); shape.lineTo(p.x,p.y); }
            const geo=new THREE.ShapeGeometry(shape); geo.rotateX(-Math.PI/2);
            const mat=new THREE.MeshLambertMaterial({color:colours.waiting,side:THREE.DoubleSide,transparent:true,opacity:.86});
            const mesh=new THREE.Mesh(geo,mat); mesh.position.y=2; mesh.userData={mining_block:row.mining_block,mining_block_code:row.mining_block_code}; blockGroup.add(mesh); blockMeshes.push(mesh);
            const eg=new THREE.EdgesGeometry(geo), em=new THREE.LineBasicMaterial({color:colours.edge,transparent:true,opacity:.25}); const edge=new THREE.LineSegments(eg,em); edge.position.y=3; edge.userData={isEdge:true}; blockGroup.add(edge); blockMeshes.push(edge);
        });
        resetCamera();
    }
    function clearBlocks(){ blockMeshes.forEach(o=>{ blockGroup.remove(o); if(o.geometry) o.geometry.dispose(); if(o.material && o.material.dispose) o.material.dispose(); }); blockMeshes=[]; }
    function colour(mesh, col, op){ if(!mesh.material) return; mesh.material.color.setHex(col); mesh.material.opacity=op; mesh.material.needsUpdate=true; }

    function updateState(){
        blockMeshes.forEach(o=>{ if(o.userData&&o.userData.isEdge) return; colour(o,colours.waiting,.82); o.position.y=2; });
        for(let i=0;i<=currentStep && i<rows.length;i++){
            const row=rows[i]; const mesh=blockMeshes.find(o=>o.userData&&!o.userData.isEdge&&o.userData.mining_block===row.mining_block); if(!mesh) continue;
            if(row.is_block_complete){ colour(mesh,colours.complete,.92); mesh.position.y=10; } else { colour(mesh,colours.partial,.9); mesh.position.y=8; }
        }
        if(currentStep>=0 && rows[currentStep]){
            const row=rows[currentStep]; const mesh=blockMeshes.find(o=>o.userData&&!o.userData.isEdge&&o.userData.mining_block===row.mining_block);
            if(mesh){ colour(mesh,colours.current,1); mesh.position.y=18; }
        }
        $("#mss_progress_bar").css("width", `${rows.length ? Math.max(0,Math.min(100,((currentStep+1)/rows.length)*100)) : 0}%`);
        updateInfo();
    }
    function updateInfo(){
        if(!payload||!rows.length){ $("#mss_info").html("No schedule loaded."); return; }
        const row=currentStep>=0?rows[currentStep]:null;
        let html=`<b>${frappe.utils.escape_html(payload.scenario_name||"")}</b><br>Material: <b>${frappe.utils.escape_html(payload.material_seam||"")}</b><br>Schedule Rows: <b>${rows.length.toLocaleString()}</b><br>Total Scheduled Volume: <b>${Number(payload.total_volume||0).toLocaleString(undefined,{maximumFractionDigits:0})}</b><br>Total Scheduled Tonnes: <b>${Number(payload.total_tonnes||0).toLocaleString(undefined,{maximumFractionDigits:0})}</b>`;
        if(row){ html+=`<hr style="margin:6px 0;">Period: <b>${row.period_no}</b> ${frappe.utils.escape_html(row.period_name||"")}<br>Block: <b>${frappe.utils.escape_html(row.mining_block_code||"")}</b><br>Scheduled Volume: <b>${Number(row.scheduled_volume||row.planned_volume||0).toLocaleString(undefined,{maximumFractionDigits:2})}</b><br>Required Hours: <b>${Number(row.required_hours||0).toLocaleString(undefined,{maximumFractionDigits:2})}</b><br>Block Progress: <b>${Math.round(Number(row.end_fraction||0)*100)}%</b><br>Block Complete: <b>${row.is_block_complete?"Yes":"No"}</b>`; }
        else html+=`<br><br>Press <b>Play</b> or <b>Step</b> to start.`;
        $("#mss_info").html(html);
    }
    function play(){ if(!rows.length) return frappe.msgprint("Load a schedule first."); pause(); const speed=Math.max(50,Number(val("animation_speed")||450)); timer=setInterval(()=>{ if(currentStep>=rows.length-1){ pause(); return; } currentStep++; updateState(); }, speed); }
    function pause(){ if(timer){ clearInterval(timer); timer=null; } }
    function step(){ if(!rows.length) return frappe.msgprint("Load a schedule first."); currentStep=Math.min(rows.length-1,currentStep+1); updateState(); }
    function reset(){ pause(); currentStep=-1; updateState(); }
};
