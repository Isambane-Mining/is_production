frappe.pages["geo-seam-3d-viewer"].on_page_load = function(wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Geo Seam 3D Viewer",
		single_column: true
	});

	let surfaces = [];
	let scene = null;
	let camera = null;
	let renderer = null;
	let controls = null;
	let raycaster = null;
	let mouse = null;
	let animationId = null;
	let surfaceObjects = [];
	let surfaceGroup = null;
	let loadedThree = false;
	let THREE = null;
	let OrbitControls = null;
	let STLExporter = null;

	const state = {
		showPoints: true,
		showWireframe: true,
		showSolidSurface: true,
		colourBy: "Batch",
		verticalExaggeration: 25,
		pointSize: 5,
		xCentre: 0,
		yCentre: 0,
		zBase: 0,
		modelRadius: 1000,
		sceneScale: 1
	};

	const batchColours = [
		0x2f80ed, 0xeb5757, 0x27ae60, 0xf2994a, 0x9b51e0,
		0x56ccf2, 0xf2c94c, 0x219653, 0xbb6bd9, 0x6fcf97
	];

	$(page.body).html(`
		<style>
			.geo-3d-shell {
				height: calc(100vh - 86px);
				display: grid;
				grid-template-columns: 340px 1fr;
				gap: 10px;
				padding: 8px;
				background: #f6f7f9;
			}

			.geo-3d-side-panel {
				background: #fff;
				border: 1px solid #e0e0e0;
				border-radius: 10px;
				padding: 12px;
				overflow-y: auto;
				box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
			}

			.geo-3d-title {
				font-size: 15px;
				font-weight: 700;
				margin-bottom: 10px;
			}

			.geo-3d-section {
				border-top: 1px solid #eee;
				padding-top: 10px;
				margin-top: 10px;
			}

			.geo-3d-filter-slot {
				margin-bottom: 9px;
			}

			.geo-3d-button-row {
				display: grid;
				grid-template-columns: 1fr 1fr;
				gap: 6px;
				margin-top: 10px;
			}

			.geo-3d-button-row .btn {
				width: 100%;
			}

			.geo-3d-help {
				font-size: 11px;
				color: #777;
				line-height: 1.35;
				margin-top: 5px;
			}

			.geo-3d-main-panel {
				position: relative;
				min-width: 0;
				background: #fff;
				border: 1px solid #e0e0e0;
				border-radius: 10px;
				overflow: hidden;
				box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
			}

			#geo_3d_viewer {
				position: absolute;
				inset: 0;
				background: linear-gradient(#fdfdfd, #eef2f5);
			}

			.geo-3d-info-box {
				position: absolute;
				top: 12px;
				left: 12px;
				background: rgba(255, 255, 255, 0.94);
				border: 1px solid #ddd;
				border-radius: 8px;
				padding: 9px 11px;
				font-size: 12px;
				line-height: 1.45;
				pointer-events: none;
				box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
				min-width: 230px;
				max-width: 360px;
			}

			.geo-3d-legend {
				position: absolute;
				right: 12px;
				bottom: 12px;
				background: rgba(255, 255, 255, 0.96);
				border: 1px solid #ddd;
				border-radius: 9px;
				padding: 10px 12px;
				font-size: 12px;
				pointer-events: none;
				min-width: 260px;
				box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
				max-height: 280px;
				overflow: hidden;
			}

			.geo-3d-legend-title {
				font-weight: 700;
				margin-bottom: 6px;
			}

			.geo-3d-legend-row {
				display: flex;
				align-items: center;
				gap: 7px;
				margin: 4px 0;
				white-space: nowrap;
				overflow: hidden;
				text-overflow: ellipsis;
			}

			.geo-3d-swatch {
				width: 12px;
				height: 12px;
				border-radius: 50%;
				border: 1px solid rgba(0,0,0,0.25);
				flex: 0 0 auto;
			}

			.geo-3d-loading {
				position: absolute;
				inset: 0;
				display: none;
				align-items: center;
				justify-content: center;
				background: rgba(255, 255, 255, 0.72);
				z-index: 10;
				font-weight: 600;
				color: #333;
			}
		</style>

		<div class="geo-3d-shell">
			<div class="geo-3d-side-panel">
				<div class="geo-3d-title">Geo Seam 3D Viewer</div>

				<div class="geo-3d-filter-slot" id="geo_project_filter"></div>
				<div class="geo-3d-filter-slot" id="geo_model_output_filter"></div>
				<div class="geo-3d-filter-slot" id="version_filter"></div>
				<div class="geo-3d-filter-slot" id="model_batches_filter"></div>

				<div class="geo-3d-section">
					<div class="geo-3d-filter-slot" id="vertical_exaggeration_filter"></div>
					<div class="geo-3d-filter-slot" id="point_size_filter"></div>
					<div class="geo-3d-filter-slot" id="colour_by_filter"></div>
				</div>

				<div class="geo-3d-section">
					<div class="geo-3d-filter-slot" id="show_solid_surface_filter"></div>
					<div class="geo-3d-filter-slot" id="show_wireframe_filter"></div>
					<div class="geo-3d-filter-slot" id="show_points_filter"></div>
					<div class="geo-3d-help">
						Use <b>Solid Surface</b> to see seam sheets, <b>Wireframe</b> to inspect the grid,
						and <b>Points</b> to confirm imported X/Y/Z data even when a batch does not form a perfect grid.
					</div>
				</div>

				<div class="geo-3d-button-row">
					<button class="btn btn-primary btn-sm" id="load_3d_view">Load View</button>
					<button class="btn btn-default btn-sm" id="reset_3d_camera">Reset Camera</button>
					<button class="btn btn-default btn-sm" id="clear_3d_view">Clear View</button>
					<button class="btn btn-default btn-sm" id="refresh_3d_view">Refresh Render</button>
					<button class="btn btn-default btn-sm" id="export_3d_stl">Export STL</button>
				</div>

				<div class="geo-3d-section">
					<div class="geo-3d-help">
						Controls: left mouse = rotate, wheel = zoom, right mouse = pan.
						3D mapping is X = imported X, vertical = imported Z elevation, depth axis = imported Y.
						STL export includes only visible solid surface meshes, not point clouds or wireframe lines.
					</div>
				</div>
			</div>

			<div class="geo-3d-main-panel">
				<div id="geo_3d_viewer"></div>
				<div class="geo-3d-loading" id="geo_3d_loading">Loading 3D viewer...</div>
				<div class="geo-3d-info-box" id="geo_3d_info_box">No data loaded.</div>
				<div class="geo-3d-legend" id="geo_3d_legend" style="display:none;"></div>
			</div>
		</div>
	`);

	const filters = {};

	function make_control(parent, df) {
		return frappe.ui.form.make_control({
			parent: $(parent),
			df,
			render_input: true
		});
	}

	function method_path(method) {
		return "is_production.geo_planning.page.geo_seam_3d_viewer.geo_seam_3d_viewer." + method;
	}

	function get_project() {
		return filters.geo_project ? filters.geo_project.get_value() : "";
	}

	filters.geo_project = make_control("#geo_project_filter", {
		fieldtype: "Link",
		options: "Geo Project",
		label: "Project",
		fieldname: "geo_project",
		change: function() {
			if (filters.geo_model_output) filters.geo_model_output.set_value("");
			if (filters.model_batches) filters.model_batches.set_value([]);
			setup_project_filtered_queries();
		}
	});

	filters.geo_model_output = make_control("#geo_model_output_filter", {
		fieldtype: "Link",
		options: "Geo Model Output",
		label: "Model Output",
		fieldname: "geo_model_output"
	});

	filters.version_tag = make_control("#version_filter", {
		fieldtype: "Data",
		label: "Version",
		fieldname: "version_tag"
	});

	filters.model_batches = make_control("#model_batches_filter", {
		fieldtype: "MultiSelectList",
		label: "Model Batches",
		fieldname: "model_batches",
		reqd: 1,
		get_data: function(txt) {
			return frappe.call({
				method: method_path("get_model_batch_options"),
				args: {
					txt: txt || "",
					geo_project: get_project()
				}
			}).then(r => {
				return (r.message || []).map(row => ({
					value: row.value,
					description: row.description || ""
				}));
			});
		}
	});

	filters.vertical_exaggeration = make_control("#vertical_exaggeration_filter", {
		fieldtype: "Float",
		label: "Vertical Exaggeration",
		fieldname: "vertical_exaggeration",
		default: 25,
		description: "Example: 1 = true scale, 25/50 = exaggerate seam dip so flat seams are visible.",
		change: function() {
			refresh_state_from_filters();
			render_surfaces();
		}
	});

	filters.point_size = make_control("#point_size_filter", {
		fieldtype: "Float",
		label: "Point Size",
		fieldname: "point_size",
		default: 5,
		change: function() {
			refresh_state_from_filters();
			render_surfaces();
		}
	});

	filters.colour_by = make_control("#colour_by_filter", {
		fieldtype: "Select",
		label: "Colour By",
		fieldname: "colour_by",
		options: "Batch\nElevation",
		default: "Batch",
		change: function() {
			refresh_state_from_filters();
			render_surfaces();
		}
	});

	filters.show_solid_surface = make_control("#show_solid_surface_filter", {
		fieldtype: "Check",
		label: "Show Solid Surface",
		fieldname: "show_solid_surface",
		default: 1,
		change: function() {
			refresh_state_from_filters();
			render_surfaces();
		}
	});

	filters.show_wireframe = make_control("#show_wireframe_filter", {
		fieldtype: "Check",
		label: "Show Wireframe",
		fieldname: "show_wireframe",
		default: 1,
		change: function() {
			refresh_state_from_filters();
			render_surfaces();
		}
	});

	filters.show_points = make_control("#show_points_filter", {
		fieldtype: "Check",
		label: "Show Points",
		fieldname: "show_points",
		default: 1,
		change: function() {
			refresh_state_from_filters();
			render_surfaces();
		}
	});

	setTimeout(function() {
		if (!filters.vertical_exaggeration.get_value()) filters.vertical_exaggeration.set_value(50);
		if (!filters.point_size.get_value()) filters.point_size.set_value(7);
		filters.show_wireframe.set_value(1);
		filters.show_points.set_value(1);
		refresh_state_from_filters();
	}, 150);

	setup_project_filtered_queries();

	function setup_project_filtered_queries() {
		filters.geo_model_output.df.get_query = function() {
			const project = get_project();
			if (!project) return {};
			return {
				filters: {
					geo_project: project
				}
			};
		};
	}

	$("#load_3d_view").on("click", function() {
		load_view();
	});

	$("#reset_3d_camera").on("click", function() {
		reset_camera();
	});

	$("#clear_3d_view").on("click", function() {
		surfaces = [];
		clear_scene_objects();
		update_info_box();
		update_legend();
	});

	$("#refresh_3d_view").on("click", function() {
		refresh_state_from_filters();
		render_surfaces();
	});

	$("#export_3d_stl").on("click", function() {
		export_visible_stl();
	});

	window.addEventListener("resize", function() {
		resize_renderer();
	});

	setTimeout(function() {
		init_three_viewer();
	}, 80);

	function refresh_state_from_filters() {
		state.verticalExaggeration = Math.max(0.0001, Number(filters.vertical_exaggeration.get_value() || 1));
		state.pointSize = Math.max(0.1, Number(filters.point_size.get_value() || 2.5));
		state.colourBy = filters.colour_by.get_value() || "Batch";
		state.showPoints = !!Number(filters.show_points.get_value() || 0);
		state.showWireframe = !!Number(filters.show_wireframe.get_value() || 0);
		state.showSolidSurface = !!Number(filters.show_solid_surface.get_value() || 0);
	}

	function get_selected_batches() {
		const raw = filters.model_batches.get_value();

		if (!raw) return [];

		if (Array.isArray(raw)) {
			return raw.map(v => {
				if (typeof v === "string") return v;
				return v.value || v.name || "";
			}).filter(Boolean);
		}

		if (typeof raw === "string") {
			try {
				const parsed = JSON.parse(raw);
				if (Array.isArray(parsed)) {
					return parsed.map(v => typeof v === "string" ? v : (v.value || v.name || "")).filter(Boolean);
				}
			} catch (e) {
				// fall through to comma split
			}

			return raw.split(",").map(v => v.trim()).filter(Boolean);
		}

		return [];
	}

	function show_loading(message) {
		$("#geo_3d_loading").text(message || "Loading...").css("display", "flex");
	}

	function hide_loading() {
		$("#geo_3d_loading").hide();
	}

	function ensure_three_loaded() {
		// three.js is not on every desk page: load it for this page
		return new Promise((resolve) => frappe.require("production_dependencies.bundle.js", resolve)).then(
			use_loaded_three
		);
	}

	function use_loaded_three() {
		THREE = is_production && is_production.THREE;
		OrbitControls = is_production && is_production.OrbitControls;
		STLExporter = is_production && is_production.STLExporter;

		if (!THREE || !OrbitControls || !STLExporter) {
			return Promise.reject(
				new Error("Three.js, OrbitControls, or STLExporter is not available from production_dependencies.bundle.js")
			);
		}

		loadedThree = true;
		return Promise.resolve();
	}

	function init_three_viewer() {
		ensure_three_loaded()
			.then(() => {
				const container = document.getElementById("geo_3d_viewer");

				scene = new THREE.Scene();
				scene.background = new THREE.Color(0xf7f9fb);

				camera = new THREE.PerspectiveCamera(45, 1, 0.1, 10000000);

				renderer = new THREE.WebGLRenderer({
					antialias: true,
					alpha: false
				});

				renderer.setPixelRatio(window.devicePixelRatio || 1);
				container.appendChild(renderer.domElement);

				controls = new OrbitControls(camera, renderer.domElement);
				controls.enableDamping = true;
				controls.dampingFactor = 0.08;
				controls.screenSpacePanning = false;
				controls.target.set(0, 0, 0);

				raycaster = new THREE.Raycaster();
				mouse = new THREE.Vector2();

				const ambient = new THREE.AmbientLight(0xffffff, 0.72);
				scene.add(ambient);

				const dir1 = new THREE.DirectionalLight(0xffffff, 0.55);
				dir1.position.set(800, 1200, 700);
				scene.add(dir1);

				const dir2 = new THREE.DirectionalLight(0xffffff, 0.25);
				dir2.position.set(-800, 600, -900);
				scene.add(dir2);

				surfaceGroup = new THREE.Group();
				surfaceGroup.name = "surface_group";
				scene.add(surfaceGroup);

				add_axes();
				resize_renderer();
				reset_camera();
				animate();
				hide_loading();
				update_info_box();
			})
			.catch((err) => {
				console.error("Three.js load error:", err);
				hide_loading();
				$("#geo_3d_info_box").html(
					"<b>3D library could not load.</b><br>" +
					"Check production_dependencies.bundle.js. It must expose is_production.THREE, is_production.OrbitControls, and is_production.STLExporter."
				);
			});
	}

	function add_axes() {
		if (!scene || !THREE) return;

		const axes = new THREE.AxesHelper(350);
		axes.name = "base_axes";
		scene.add(axes);

		const grid = new THREE.GridHelper(1400, 28, 0x999999, 0xdddddd);
		grid.name = "base_grid";
		scene.add(grid);

		const markerGeo = new THREE.BoxGeometry(30, 30, 30);
		const markerMat = new THREE.MeshBasicMaterial({ color: 0xff0000 });
		const marker = new THREE.Mesh(markerGeo, markerMat);
		marker.name = "origin_marker";
		marker.position.set(0, 15, 0);
		scene.add(marker);
	}

	function animate() {
		animationId = requestAnimationFrame(animate);

		if (controls) {
			controls.update();
		}

		if (renderer && scene && camera) {
			renderer.render(scene, camera);
		}
	}

	function resize_renderer() {
		if (!renderer || !camera) return;

		const container = document.getElementById("geo_3d_viewer");
		const rect = container.getBoundingClientRect();

		const width = Math.max(300, Math.floor(rect.width));
		const height = Math.max(300, Math.floor(rect.height));

		renderer.setSize(width, height, true);
		renderer.setPixelRatio(window.devicePixelRatio || 1);

		camera.aspect = width / height;
		camera.updateProjectionMatrix();

		if (renderer && scene && camera) {
			renderer.render(scene, camera);
		}
	}

	function load_view() {
		const batches = get_selected_batches();

		if (!batches.length) {
			frappe.msgprint("Please select at least one Model Batch.");
			return;
		}

		refresh_state_from_filters();

		show_loading("Loading selected batches...");

		frappe.call({
			method: method_path("get_multi_batch_surfaces"),
			args: {
				geo_project: filters.geo_project.get_value(),
				geo_model_output: filters.geo_model_output.get_value(),
				version_tag: filters.version_tag.get_value(),
				model_batches: JSON.stringify(batches)
			},
			callback: function(r) {
				hide_loading();
				surfaces = r.message || [];

				if (!surfaces.length) {
					frappe.msgprint("No points were found for the selected batches.");
				}

				render_surfaces();
				reset_camera();
				update_info_box();
				update_legend();
			},
			error: function(err) {
				hide_loading();
				console.error(err);
				frappe.msgprint("Could not load 3D surface data. Please check the server error log.");
			}
		});
	}

	function get_all_points() {
		const all = [];

		for (const surface of surfaces) {
			for (const p of surface.points || []) {
				all.push(p);
			}
		}

		return all;
	}

	function calculate_global_transform() {
		const all = get_all_points();

		if (!all.length) {
			state.xCentre = 0;
			state.yCentre = 0;
			state.zBase = 0;
			state.modelRadius = 900;
			state.sceneScale = 1;
			return;
		}

		const xs = all.map(p => p.x);
		const ys = all.map(p => p.y);
		const zs = all.map(p => p.z);

		const minX = Math.min(...xs);
		const maxX = Math.max(...xs);
		const minY = Math.min(...ys);
		const maxY = Math.max(...ys);
		const minZ = Math.min(...zs);
		const maxZ = Math.max(...zs);

		state.xCentre = (minX + maxX) / 2;
		state.yCentre = (minY + maxY) / 2;
		state.zBase = minZ;

		const dx = maxX - minX || 1;
		const dy = maxY - minY || 1;
		const dz = maxZ - minZ || 1;

		const planExtent = Math.max(dx, dy, 1);
		state.sceneScale = 1200 / planExtent;

		const scaledDz = dz * state.sceneScale * state.verticalExaggeration;
		state.modelRadius = Math.max(900, 700 + scaledDz);
	}

	function to_three_point(p) {
		return new THREE.Vector3(
			(p.x - state.xCentre) * state.sceneScale,
			(p.z - state.zBase) * state.sceneScale * state.verticalExaggeration,
			-(p.y - state.yCentre) * state.sceneScale
		);
	}

	function clear_scene_objects() {
		if (!surfaceGroup) return;

		for (const obj of surfaceObjects) {
			surfaceGroup.remove(obj);

			if (obj.geometry) obj.geometry.dispose();

			if (obj.material) {
				if (Array.isArray(obj.material)) {
					obj.material.forEach(m => m.dispose && m.dispose());
				} else {
					obj.material.dispose && obj.material.dispose();
				}
			}
		}

		surfaceObjects = [];
		surfaceGroup.position.set(0, 0, 0);
	}

	function render_surfaces() {
		if (!loadedThree || !scene || !surfaceGroup) {
			return;
		}

		refresh_state_from_filters();
		calculate_global_transform();
		clear_scene_objects();

		for (let i = 0; i < surfaces.length; i++) {
			const surface = surfaces[i];
			const colour = get_surface_colour(i, surface);
			const points = (surface.points || []).filter(p =>
				isFinite(p.x) && isFinite(p.y) && isFinite(p.z)
			);

			if (!points.length) continue;

			const meshData = build_surface_geometry(points);

			if (state.showSolidSurface && meshData.geometry && meshData.triangleCount > 0) {
				const mat = new THREE.MeshLambertMaterial({
					color: colour,
					side: THREE.DoubleSide,
					transparent: true,
					opacity: 0.72,
					depthWrite: false
				});

				const mesh = new THREE.Mesh(meshData.geometry, mat);
				mesh.name = `surface_${surface.batch}`;
				mesh.userData = { surface };
				surfaceGroup.add(mesh);
				surfaceObjects.push(mesh);
			}

			if (state.showWireframe && meshData.geometry && meshData.triangleCount > 0) {
				const wireGeo = new THREE.WireframeGeometry(meshData.geometry);
				const wireMat = new THREE.LineBasicMaterial({
					color: 0x000000,
					transparent: true,
					opacity: 0.85
				});
				const wire = new THREE.LineSegments(wireGeo, wireMat);
				wire.name = `wire_${surface.batch}`;
				surfaceGroup.add(wire);
				surfaceObjects.push(wire);
			}

			if (state.showPoints || !meshData.triangleCount) {
				const pointGeo = new THREE.BufferGeometry();
				const positions = [];

				for (const p of points) {
					const v = to_three_point(p);
					positions.push(v.x, v.y, v.z);
				}

				pointGeo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));

				const pointMat = new THREE.PointsMaterial({
					color: colour,
					size: Math.max(4, state.pointSize),
					sizeAttenuation: false
				});

				const pointCloud = new THREE.Points(pointGeo, pointMat);
				pointCloud.name = `points_${surface.batch}`;
				surfaceGroup.add(pointCloud);
				surfaceObjects.push(pointCloud);
			}
		}

		center_surface_group();
		update_info_box();
		update_legend();
	}

	function center_surface_group() {
		if (!surfaceGroup || !surfaceObjects.length) return;

		surfaceGroup.updateMatrixWorld(true);

		const box = new THREE.Box3().setFromObject(surfaceGroup);

		if (box.isEmpty()) return;

		const center = new THREE.Vector3();
		const size = new THREE.Vector3();

		box.getCenter(center);
		box.getSize(size);

		surfaceGroup.position.x -= center.x;
		surfaceGroup.position.z -= center.z;
		surfaceGroup.position.y -= box.min.y;

		surfaceGroup.updateMatrixWorld(true);

		const maxSize = Math.max(size.x, size.y, size.z, 1);
		state.modelRadius = Math.max(900, maxSize * 1.35);
	}

	function get_surface_colour(index, surface) {
		if (state.colourBy === "Elevation") {
			return batchColours[index % batchColours.length];
		}

		return batchColours[index % batchColours.length];
	}

	function median_gap(values, fallback) {
		const clean = [...new Set(values.filter(v => isFinite(v)).map(v => Number(v.toFixed(6))))].sort((a, b) => a - b);
		const gaps = [];

		for (let i = 1; i < clean.length; i++) {
			const gap = clean[i] - clean[i - 1];

			if (gap > 0.000001) {
				gaps.push(gap);
			}
		}

		if (!gaps.length) return fallback;

		gaps.sort((a, b) => a - b);
		return gaps[Math.floor(gaps.length / 2)];
	}

	function coord_key(x, y) {
		return `${Number(x).toFixed(6)}|${Number(y).toFixed(6)}`;
	}

	function build_surface_geometry(points) {
		const uniqueX = [...new Set(points.map(p => Number(p.x.toFixed(6))))].sort((a, b) => a - b);
		const uniqueY = [...new Set(points.map(p => Number(p.y.toFixed(6))))].sort((a, b) => a - b);

		const pointMap = new Map();

		for (const p of points) {
			pointMap.set(coord_key(p.x, p.y), p);
		}

		const vertices = [];
		const indices = [];
		const vertexIndex = new Map();

		function add_vertex(p) {
			const key = coord_key(p.x, p.y);

			if (vertexIndex.has(key)) {
				return vertexIndex.get(key);
			}

			const v = to_three_point(p);
			const idx = vertices.length / 3;
			vertices.push(v.x, v.y, v.z);
			vertexIndex.set(key, idx);
			return idx;
		}

		for (let ix = 0; ix < uniqueX.length - 1; ix++) {
			for (let iy = 0; iy < uniqueY.length - 1; iy++) {
				const x0 = uniqueX[ix];
				const x1 = uniqueX[ix + 1];
				const y0 = uniqueY[iy];
				const y1 = uniqueY[iy + 1];

				const p00 = pointMap.get(coord_key(x0, y0));
				const p10 = pointMap.get(coord_key(x1, y0));
				const p01 = pointMap.get(coord_key(x0, y1));
				const p11 = pointMap.get(coord_key(x1, y1));

				if (p00 && p10 && p01 && p11) {
					const i00 = add_vertex(p00);
					const i10 = add_vertex(p10);
					const i01 = add_vertex(p01);
					const i11 = add_vertex(p11);

					indices.push(i00, i10, i11);
					indices.push(i00, i11, i01);
				}
			}
		}

		if (!indices.length) {
			return {
				geometry: null,
				triangleCount: 0
			};
		}

		const geometry = new THREE.BufferGeometry();
		geometry.setAttribute("position", new THREE.Float32BufferAttribute(vertices, 3));
		geometry.setIndex(indices);
		geometry.computeVertexNormals();
		geometry.computeBoundingSphere();

		return {
			geometry,
			triangleCount: indices.length / 3
		};
	}

	function reset_camera() {
		if (!camera || !controls) return;

		resize_renderer();

		const r = Math.max(state.modelRadius, 900);
		const target = new THREE.Vector3(0, 0, 0);
		const position = new THREE.Vector3(
			r * 0.9,
			r * 0.75,
			r * 1.15
		);

		controls.target.copy(target);
		camera.position.copy(position);
		camera.lookAt(target);

		camera.near = Math.max(0.1, r / 10000);
		camera.far = Math.max(100000, r * 30);
		camera.updateProjectionMatrix();

		controls.update();

		if (renderer && scene && camera) {
			renderer.render(scene, camera);
		}
	}

	function export_visible_stl() {
		if (!surfaceGroup || !surfaceObjects.length || !STLExporter) {
			frappe.msgprint("No 3D model is available to export.");
			return;
		}

		const exportGroup = new THREE.Group();

		for (const obj of surfaceObjects) {
			if (!obj.visible) continue;
			if (!obj.isMesh) continue;

			const clone = obj.clone(true);

			if (obj.geometry) {
				clone.geometry = obj.geometry.clone();
			}

			if (obj.material) {
				clone.material = Array.isArray(obj.material)
					? obj.material.map(m => m.clone())
					: obj.material.clone();
			}

			clone.position.copy(obj.position);
			clone.rotation.copy(obj.rotation);
			clone.scale.copy(obj.scale);
			clone.matrix.copy(obj.matrix);
			clone.matrixWorld.copy(obj.matrixWorld);

			exportGroup.add(clone);
		}

		if (!exportGroup.children.length) {
			frappe.msgprint(
				"Only solid surface meshes can be exported to STL. Enable 'Show Solid Surface' and load a batch with valid mesh geometry."
			);
			return;
		}

		exportGroup.position.copy(surfaceGroup.position);
		exportGroup.rotation.copy(surfaceGroup.rotation);
		exportGroup.scale.copy(surfaceGroup.scale);
		exportGroup.updateMatrixWorld(true);

		const exporter = new STLExporter();
		const stlString = exporter.parse(exportGroup, { binary: false });

		const blob = new Blob([stlString], {
			type: "model/stl"
		});

		const timestamp = frappe.datetime.now_datetime()
			.replace(/[^0-9]/g, "")
			.slice(0, 14);

		const filename = `geo_seam_model_${timestamp}.stl`;
		const url = URL.createObjectURL(blob);

		const link = document.createElement("a");
		link.href = url;
		link.download = filename;
		document.body.appendChild(link);
		link.click();
		document.body.removeChild(link);

		URL.revokeObjectURL(url);
	}

	function update_info_box() {
		const totalPoints = get_all_points().length;
		const all = get_all_points();

		if (!surfaces.length || !totalPoints) {
			$("#geo_3d_info_box").html("No data loaded.");
			return;
		}

		const xs = all.map(p => p.x);
		const ys = all.map(p => p.y);
		const zs = all.map(p => p.z);

		const info = [];
		info.push(`<b>Surfaces:</b> ${surfaces.length}`);
		info.push(`<b>Total Points:</b> ${totalPoints.toLocaleString()}`);
		info.push(`<b>X:</b> ${Math.min(...xs).toFixed(2)} - ${Math.max(...xs).toFixed(2)}`);
		info.push(`<b>Y:</b> ${Math.min(...ys).toFixed(2)} - ${Math.max(...ys).toFixed(2)}`);
		info.push(`<b>Elevation Z:</b> ${Math.min(...zs).toFixed(2)} - ${Math.max(...zs).toFixed(2)}`);
		info.push(`<b>Vertical Exag.:</b> ${state.verticalExaggeration}`);
		info.push(`<b>Solid:</b> ${state.showSolidSurface ? "On" : "Off"}`);
		info.push(`<b>Wireframe:</b> ${state.showWireframe ? "On" : "Off"}`);
		info.push(`<b>Points:</b> ${state.showPoints ? "On" : "Off"}`);

		const failedMeshes = surfaces.filter(s => {
			const data = build_surface_geometry((s.points || []).filter(p => isFinite(p.x) && isFinite(p.y) && isFinite(p.z)));
			return !data.triangleCount;
		}).length;

		if (failedMeshes) {
			info.push(`<b>Point-only batches:</b> ${failedMeshes}`);
		}

		$("#geo_3d_info_box").html(info.join("<br>"));
	}

	function update_legend() {
		const el = $("#geo_3d_legend");

		if (!surfaces.length) {
			el.hide();
			return;
		}

		let html = `<div class="geo-3d-legend-title">Selected Batches</div>`;

		surfaces.forEach((surface, i) => {
			const colour = "#" + batchColours[i % batchColours.length].toString(16).padStart(6, "0");
			const label = frappe.utils.escape_html(surface.label || surface.batch || `Batch ${i + 1}`);
			const count = (surface.points || []).length.toLocaleString();

			html += `
				<div class="geo-3d-legend-row">
					<span class="geo-3d-swatch" style="background:${colour};"></span>
					<span title="${label}">${label} (${count})</span>
				</div>
			`;
		});

		el.html(html).show();
	}
};