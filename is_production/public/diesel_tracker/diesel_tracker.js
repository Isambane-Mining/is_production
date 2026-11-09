/* The Diesel L/BCM Tracker (templates/pages/portal_diesel_tracker.html).

   Its sites, equipment, entries and settings live on the bench, in the Diesel Tracker doctypes, through
   is_production.production.diesel_tracker. Entries are typed here, or brought in every hour from the production
   records (the submitted Daily Diesel Sheets and Hourly Production). The page asks for one period (and site) at a
   time: the server sends each machine's sums over it ("summary") and the latest entries. Every change answers
   with the data afresh, and the page looks again every two minutes while it is shown. What the user may change
   (the data's "can") decides which forms and buttons are shown.

   L/BCM = diesel litres ÷ (waste bcm + coal tons ÷ coal density), per machine and over the period. A haul
   truck's loads from the production records are its excavator's bcm: they are the truck's own, but the site's
   totals count them once (the entries' "counted").

   Burn rate = diesel litres ÷ engine hours (Pre-Use Hours), per ADT, excavator and dozer, against the OEM burn rate
   of its model ("burn": worked out on the server, as the Monthly Diesel Reconciliation does). */
(function () {
  "use strict";
  const CFG = window.DIESEL_TRACKER || {};
  const API = "/api/method/is_production.production.diesel_tracker.";
  const DEFAULTS = { density: 1.4, green: 0.8, amber: 1.2, max: 2 };
  const LIST = 40, EQ_LIST = 50, BARS = 30; // the entries and machines listed, the bars drawn, before "show all"
  const FLEETS = ["adt", "excavator", "dozer"]; // the burn rate graphs
  const S = { sites: [], equipment: [], summary: [], burn: [], entries: [], entryCount: 0, months: [], view: {}, settings: { ...DEFAULTS }, sync: {}, can: {}, ready: false, failed: false, loadedAt: 0 };
  const F = { site: "all", month: "", from: "", to: "", init: false, showAll: false, showAllEq: false };
  const $ = (id) => document.getElementById(id);
  const nf0 = new Intl.NumberFormat("en-ZA", { maximumFractionDigits: 0 });
  const nf2 = new Intl.NumberFormat("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fmt0 = (v) => nf0.format(v || 0);
  const fmt2 = (v) => (v == null || !isFinite(v) ? "–" : nf2.format(v));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const todayISO = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 10); };
  const monthLabel = (ym) => { const [y, m] = ym.split("-").map(Number); return new Date(y, m - 1, 1).toLocaleDateString("en-ZA", { month: "long", year: "numeric" }); };
  const monthRange = (ym) => { const [y, m] = ym.split("-").map(Number); const last = new Date(y, m, 0).getDate(); return [ym + "-01", ym + "-" + String(last).padStart(2, "0")]; };
  const dateLabel = (iso) => { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d).toLocaleDateString("en-ZA", { day: "numeric", month: "short", year: "numeric" }); };
  const timeLabel = (t) => new Date(t).toLocaleTimeString("en-ZA", { hour: "2-digit", minute: "2-digit" });
  // (the server's "2026-11-09 10:14:03.123456", in the bench's time)
  const stampLabel = (s) => { const [d, t] = String(s).split(" "); return dateLabel(d) + (t ? " " + t.slice(0, 5) : ""); };

  /* ---------- the bench ---------- */
  // the message of a failed call: Frappe's own (a validation), else what the status says
  function serverMsg(status, data) {
    const type = data && data.exc_type;
    if (type === "SessionExpired" || type === "CSRFTokenError") return "Your session has ended. Reload the page to sign in again.";
    let msg = status === 403 ? "You don't have permission to change this data. Ask a Production Manager." : `Couldn't save: the server answered ${status}. Try again.`;
    try {
      const m = JSON.parse(JSON.parse(data._server_messages)[0]);
      if (m.message) msg = String(m.message).replace(/<[^>]+>/g, "");
    } catch (e) { /* no message from the server */ }
    return msg;
  }
  async function call(method, args) {
    let r;
    try {
      r = await fetch(API + method, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json", "X-Frappe-CSRF-Token": CFG.csrfToken || "" },
        body: JSON.stringify(args || {}),
      });
    } catch (e) { throw new Error("Couldn't reach the server. Check your connection and try again."); }
    let data = null;
    try { data = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) throw new Error(serverMsg(r.status, data));
    return data ? data.message : null;
  }
  // the period and site the page shows (the latest month with entries, the first time)
  const view = () => (F.init ? { from: F.from, to: F.to, site: F.site, limit: F.showAll ? 2000 : LIST } : { latest: 1, site: F.site, limit: LIST });
  // (each answer is applied unless a later request has already been: a slow refresh never undoes a save)
  let seq = 0, applied = 0, writing = 0, pending = false;
  function apply(d, mine) {
    if (!d || mine < applied) return;
    applied = mine;
    S.sites = d.sites || []; S.equipment = d.equipment || []; S.summary = d.summary || []; S.burn = d.burn || []; S.entries = d.entries || [];
    S.entryCount = d.entryCount || 0; S.months = d.months || []; S.view = d.view || {};
    S.settings = { ...DEFAULTS, ...(d.settings || {}) }; S.sync = d.sync || {}; S.can = d.can || {};
    if (!F.init) {
      F.init = true; F.from = S.view.from || ""; F.to = S.view.to || "";
      const ym = F.from.slice(0, 7);
      F.month = !F.from && !F.to ? "all" : ym && monthRange(ym).join() === [F.from, F.to].join() ? ym : "custom";
    }
    S.ready = true; S.failed = false; S.loadedAt = Date.now();
    render();
  }
  async function write(method, args) {
    const mine = ++seq; writing++;
    try { const r = await call(method, { ...args, view: view() }); apply(r && r.data, mine); return r || {}; }
    finally { writing--; if (!writing && pending) { pending = false; refresh(); } }
  }
  async function refresh() {
    if (writing) { pending = true; return; }
    const mine = ++seq;
    try { apply(await call("get_data", { view: view() }), mine); }
    catch (e) { S.failed = true; S.failMsg = e.message; render(); }
  }
  let refreshT = 0;
  const refreshSoon = () => { clearTimeout(refreshT); refreshT = setTimeout(refresh, 120); };
  const backend = {
    add: (col, obj) => write({ sites: "add_site", equipment: "add_equipment", entries: "add_entry" }[col], obj).then((r) => r.id),
    remove: (col, id) => write("delete", { kind: col, id }),
    saveSettings: (o) => write("save_settings", o),
  };
  const errMsg = (e) => (e && e.message) || "Couldn't save. Check your connection and try again.";

  let toastT;
  function toast(msg, err, ms) {
    const t = $("toast"); t.textContent = msg; t.className = "show" + (err ? " err" : "");
    clearTimeout(toastT); toastT = setTimeout(() => { t.className = err ? "err" : ""; }, ms || (err ? 5000 : 2400));
  }

  // what the user may do: entry, setup, settings, sync, and loading or removing the example data
  function allowed(what) {
    const c = S.can;
    if (what === "exampleData") return !!(c.setup && c.entry && c.deleteSetup);
    return !!c[what];
  }

  /* ---------- calculations ---------- */
  const bcmOf = (e) => (+e.waste || 0) + (+e.coal || 0) / (S.settings.density || 1.4);
  const status = (v) => (v == null || !isFinite(v) ? "none" : v <= S.settings.green ? "good" : v <= S.settings.amber ? "avg" : "bad");
  const STATUS_TEXT = { good: "Low use", avg: "Average", bad: "High use", none: "No volume" };
  const siteName = (id) => (S.sites.find((s) => s.id === id) || {}).name || "Removed site";
  const equip = (id) => S.equipment.find((q) => q.id === id) || { name: "Removed equipment", type: "" };

  /* ---------- rendering ---------- */
  let raf = 0;
  function render() { cancelAnimationFrame(raf); raf = requestAnimationFrame(draw); }

  function fillSelect(sel, opts, value) {
    const html = opts.map((o) => `<option value="${esc(o[0])}">${esc(o[1])}</option>`).join("");
    if (sel.innerHTML !== html) sel.innerHTML = html;
    if (opts.some((o) => o[0] === value)) sel.value = value;
    else if (opts.length) sel.value = opts[0][0];
  }

  function drawFilters() {
    const siteOpts = [["all", "All sites"], ...S.sites.slice().sort((a, b) => a.name.localeCompare(b.name)).map((s) => [s.id, s.name])];
    if (F.site !== "all" && S.ready && !S.sites.some((s) => s.id === F.site)) { F.site = "all"; refreshSoon(); }
    fillSelect($("fSite"), siteOpts, F.site);
    const months = new Set(S.months);
    months.add(todayISO().slice(0, 7));
    if (F.month && F.month.length === 7) months.add(F.month);
    const mOpts = [...months].sort().reverse().map((m) => [m, monthLabel(m)]);
    mOpts.push(["all", "All dates"]);
    if (F.month === "custom") mOpts.unshift(["custom", "Custom range"]);
    fillSelect($("fMonth"), mOpts, F.month);
    if (document.activeElement !== $("fFrom")) $("fFrom").value = F.from;
    if (document.activeElement !== $("fTo")) $("fTo").value = F.to;
  }

  function periodText() {
    if (F.month && F.month.length === 7) return monthLabel(F.month);
    if (!F.from && !F.to) return "All dates";
    return (F.from ? dateLabel(F.from) : "Start") + " to " + (F.to ? dateLabel(F.to) : "today");
  }

  /* gauge */
  const CX = 150, CY = 150, R = 112;
  const ang = (v) => Math.PI * (1 - Math.min(Math.max(v / S.settings.max, 0), 1));
  const pt = (v, r) => [CX + r * Math.cos(ang(v)), CY - r * Math.sin(ang(v))];
  function arc(v0, v1, r) {
    const [x0, y0] = pt(v0, r), [x1, y1] = pt(v1, r);
    return `M${x0.toFixed(2)} ${y0.toFixed(2)} A${r} ${r} 0 0 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
  }
  function drawGauge(v) {
    const st = S.settings, max = st.max;
    const g = Math.min(st.green, max), a = Math.min(Math.max(st.amber, st.green), max);
    const bands = [[0, g, "var(--good)"], [g, a, "var(--avg)"], [a, max, "var(--bad)"]].filter((b) => b[1] > b[0]);
    $("gaugeBands").innerHTML =
      `<path d="${arc(0, max, R)}" fill="none" stroke="var(--surface-2)" stroke-width="30"/>` +
      bands.map((b) => `<path d="${arc(b[0], b[1], R)}" fill="none" stroke="${b[2]}" stroke-width="24"/>`).join("");
    let ticks = "";
    const steps = 8;
    for (let i = 0; i <= steps; i++) {
      const tv = (max / steps) * i;
      const [x0, y0] = pt(tv, R - 16), [x1, y1] = pt(tv, R - 24), [lx, ly] = pt(tv, R - 37);
      ticks += `<line x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}" stroke="var(--muted)" stroke-width="1.5"/>`;
      if (i % 2 === 0) ticks += `<text x="${lx}" y="${ly + 4}" text-anchor="middle" font-size="11" fill="var(--muted)" font-family="Barlow, sans-serif">${tv.toFixed(max >= 4 ? 1 : 2).replace(/\.?0+$/, "") || "0"}</text>`;
    }
    $("gaugeTicks").innerHTML = ticks;
    $("needleShape").setAttribute("fill", "var(--ink)");
    $("hubOuter").setAttribute("fill", "var(--ink)");
    $("hubInner").setAttribute("fill", "var(--surface)");
    const frac = v == null ? 0 : Math.min(Math.max(v / max, 0), 1);
    $("needle").style.transform = `rotate(${frac * 180}deg)`;
    const s = status(v);
    $("gaugeValue").innerHTML = (v == null ? "–" : (v > max ? "&gt;" : "") + esc(fmt2(v))) + "<small>L/bcm</small>";
    const pill = $("gaugePill");
    pill.className = "pill " + s;
    pill.textContent = v == null ? "No data" : STATUS_TEXT[s];
    $("gaugeLegend").innerHTML =
      `<span><i style="background:var(--good)"></i>Low ≤ ${fmt2(st.green)}</span>` +
      `<span><i style="background:var(--avg)"></i>Average ≤ ${fmt2(st.amber)}</span>` +
      `<span><i style="background:var(--bad)"></i>High &gt; ${fmt2(st.amber)}</span>`;
  }

  /* chart */
  let chart = null;
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const thresholdPlugin = {
    id: "thresholds",
    afterDatasetsDraw(c) {
      const y = c.scales.y, area = c.chartArea, ctx = c.ctx;
      [[S.settings.green, cssVar("--good")], [S.settings.amber, cssVar("--bad")]].forEach(([v, col]) => {
        if (v > y.max) return;
        const py = y.getPixelForValue(v);
        ctx.save(); ctx.strokeStyle = col; ctx.setLineDash([5, 4]); ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(area.left, py); ctx.lineTo(area.right, py); ctx.stroke();
        ctx.fillStyle = col; ctx.font = "600 11px Barlow, sans-serif"; ctx.textAlign = "right";
        ctx.fillText(nf2.format(v), area.right - 2, py - 4); ctx.restore();
      });
    },
  };
  function drawChart(rows) {
    const all = rows.filter((r) => r.lbcm != null).sort((a, b) => b.lbcm - a.lbcm), data = all.slice(0, BARS);
    $("chartEmpty").hidden = data.length > 0;
    $("chartNote").textContent = !data.length ? "" : (all.length > BARS ? `The ${BARS} highest of ${all.length} machines. ` : "") + "Dashed lines mark the green and amber limits";
    if (!window.Chart) { if (data.length) { $("chartEmpty").hidden = false; $("chartEmpty").textContent = "The chart library didn't load. The table below has the same figures."; } return; }
    const colFor = { good: cssVar("--good"), avg: cssVar("--avg"), bad: cssVar("--bad") };
    const muted = cssVar("--muted"), line = cssVar("--line"), ink = cssVar("--ink");
    const labels = data.map((r) => (F.site === "all" && S.sites.length > 1 ? r.name + " · " + siteName(r.siteId) : r.name));
    const yMax = Math.max(S.settings.amber * 1.15, ...data.map((r) => r.lbcm * 1.1), 0.5);
    const cfg = {
      type: "bar",
      data: { labels, datasets: [{ data: data.map((r) => +r.lbcm.toFixed(3)), backgroundColor: data.map((r) => colFor[status(r.lbcm)]), borderRadius: 4, maxBarThickness: 56 }] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: { duration: 350 },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items) => labels[items[0].dataIndex],
              label: (ctx) => nf2.format(data[ctx.dataIndex].lbcm) + " L/bcm (" + STATUS_TEXT[status(data[ctx.dataIndex].lbcm)] + ")",
              afterLabel: (ctx) => { const r = data[ctx.dataIndex]; return fmt0(r.diesel) + " L diesel over " + fmt0(r.bcm) + " bcm" + (r.hauled ? " hauled" : ""); },
            },
          },
        },
        scales: {
          y: { beginAtZero: true, max: +yMax.toFixed(2), title: { display: true, text: "Litres per bcm", color: muted, font: { family: "Barlow" } }, ticks: { color: muted, font: { family: "Barlow" } }, grid: { color: line } },
          x: { ticks: { color: ink, font: { family: "Barlow", weight: "600" }, autoSkip: false, maxRotation: 50 }, grid: { display: false } },
        },
      },
      plugins: [thresholdPlugin],
    };
    if (chart) { chart.data = cfg.data; chart.options = cfg.options; chart.update(); }
    else chart = new Chart($("eqChart"), cfg);
  }

  /* burn rate: a graph per fleet, a bar per machine (its L/h, coloured against its OEM rate), the OEM rate a tick
     over it. Machines with few engine hours in the period (under a tenth of the busiest's, at most 24) give
     unsteady rates: they are left out of the graph (not of the fleet's figure) and counted under it. */
  const burnCharts = {};
  const burnStatus = (m) => (!m.oem ? "nooem" : m.lph <= m.oem ? "good" : m.lph <= m.oem * 1.1 ? "avg" : "bad");
  const fmt1 = (v) => (v == null || !isFinite(v) ? "–" : new Intl.NumberFormat("en-ZA", { maximumFractionDigits: 1 }).format(v));
  function drawBurnFleet(key) {
    const f = S.burn.find((x) => x.key === key) || { title: key, machines: [], lph: null, oem: null, oemShare: 0, noHours: 0 };
    const what = { adt: "ADT", excavator: "excavator", dozer: "dozer" }[key];
    const withH = f.machines.filter((m) => m.lph != null), busiest = Math.max(0, ...withH.map((m) => m.hours));
    const minH = Math.min(24, busiest / 10), steady = withH.filter((m) => m.hours >= minH).sort((a, b) => b.lph - a.lph), data = steady.slice(0, BARS);
    $("burnFleet_" + key).innerHTML = f.lph == null ? "No engine hours"
      : `<strong>${fmt2(f.lph)} L/h</strong> · ` + (f.oem ? `OEM ${fmt2(f.oem)}${f.oemShare < 0.995 ? ` (for ${Math.round(f.oemShare * 100)}% of the hours)` : ""}` : "no OEM rate");
    const few = withH.length - steady.length;
    $("burnHint_" + key).textContent = [
      steady.length > BARS ? `The ${BARS} highest of ${steady.length}.` : "",
      few ? `${few} with under ${fmt1(minH)} engine hours left out.` : "",
      f.noHours ? `${f.noHours} with diesel but no engine hours.` : "",
    ].filter(Boolean).join(" ");
    const empty = $("burnEmpty_" + key);
    empty.hidden = data.length > 0;
    empty.textContent = f.machines.length ? `No ${what} has engine hours on the Pre-Use Hours sheets in this period.` : `No ${what} worked in this period.`;
    if (!window.Chart) { if (data.length) { empty.hidden = false; empty.textContent = "The chart library didn't load."; } return; }
    const col = { good: cssVar("--good"), avg: cssVar("--avg"), bad: cssVar("--bad"), nooem: cssVar("--accent") };
    const muted = cssVar("--muted"), line = cssVar("--line"), ink = cssVar("--ink");
    const labels = data.map((m) => equip(m.equipmentId).name + (F.site === "all" && S.sites.length > 1 ? " · " + siteName(m.siteId) : ""));
    const yMax = Math.max(10, ...data.map((m) => Math.max(m.lph, m.oem || 0) * 1.12));
    const tick = Math.max(4, Math.min(12, 150 / Math.max(1, data.length)));
    const cfg = {
      type: "bar",
      data: { labels, datasets: [
        { type: "line", label: "OEM", data: data.map((m) => m.oem || null), showLine: false, pointStyle: "line", pointRadius: tick, pointHoverRadius: tick, pointBorderWidth: 3, pointHoverBorderWidth: 3, borderColor: ink, backgroundColor: ink, order: 0 },
        { type: "bar", label: "Burn rate", data: data.map((m) => m.lph), backgroundColor: data.map((m) => col[burnStatus(m)]), borderRadius: 3, maxBarThickness: 40, order: 1 },
      ] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: { duration: 350 },
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            filter: (item) => item.dataset.type === "bar",
            callbacks: {
              title: (items) => labels[items[0].dataIndex],
              label: (ctx) => { const m = data[ctx.dataIndex]; return `${fmt2(m.lph)} L/h` + (m.oem ? ` · OEM ${fmt2(m.oem)} (${m.lph > m.oem ? "+" : ""}${fmt1(((m.lph - m.oem) / m.oem) * 100)}%)` : " · no OEM rate"); },
              afterLabel: (ctx) => { const m = data[ctx.dataIndex]; return `${fmt0(m.litres)} L over ${fmt1(m.hours)} engine hours${m.model ? " · " + m.model : ""}`; },
            },
          },
        },
        scales: {
          y: { beginAtZero: true, max: Math.ceil(yMax), title: { display: true, text: "Litres per engine hour", color: muted, font: { family: "Barlow" } }, ticks: { color: muted, font: { family: "Barlow" } }, grid: { color: line } },
          x: { ticks: { color: ink, font: { family: "Barlow", weight: "600" }, autoSkip: false, maxRotation: 60 }, grid: { display: false } },
        },
      },
    };
    if (burnCharts[key]) { burnCharts[key].data = cfg.data; burnCharts[key].options = cfg.options; burnCharts[key].update(); }
    else burnCharts[key] = new Chart($("burnChart_" + key), cfg);
  }
  function drawBurn() {
    $("burnPeriod").textContent = (F.site === "all" ? "All sites" : siteName(F.site)) + " · " + periodText();
    FLEETS.forEach((key) => {
      try { drawBurnFleet(key); }
      catch (e) {
        console.error(e);
        const empty = $("burnEmpty_" + key); empty.hidden = false; empty.textContent = "The graph couldn't be drawn.";
      }
    });
  }

  // the forms and buttons the user may use; the rest is hidden (the settings shown, but not editable)
  function drawAccess() {
    document.querySelectorAll("[data-need]").forEach((el) => { el.hidden = !S.ready || !allowed(el.dataset.need); });
    document.querySelector(".forms").classList.toggle("one", !allowed("entry"));
    $("setupNote").hidden = !S.ready || allowed("setup");
    ["stDensity", "stGreen", "stAmber", "stMax"].forEach((id) => { $(id).disabled = !allowed("settings"); });
    $("emptyText").innerHTML = allowed("setup")
      ? "Add a site and its equipment under <strong>Setup</strong>, then log diesel litres, coal tons and waste volumes. Or load a few months of example data to see how the gauge and chart work."
      : "No sites have been set up yet. A Production Manager adds the sites and equipment under <strong>Setup</strong>.";
  }

  // when the production records were last brought in
  function drawSync() {
    const at = S.sync && S.sync.at;
    $("syncBar").hidden = !S.ready || (!at && !allowed("sync"));
    $("syncNote").textContent = at
      ? `Diesel and volumes come in every hour from the Daily Diesel Sheets and Hourly Production. Last brought in ${stampLabel(at)}${S.sync.note ? " (" + S.sync.note + ")" : ""}.`
      : "Diesel and volumes come in every hour from the Daily Diesel Sheets and Hourly Production. They have not been brought in yet.";
  }

  function draw() {
    drawFilters();
    drawAccess();
    drawSync();
    const st = S.settings;
    const hasEx = S.sites.some((d) => d.example) || S.equipment.some((d) => d.example);
    $("exampleBanner").hidden = !hasEx;
    $("emptyState").hidden = !S.ready || S.sites.length > 0;
    $("modeNote").textContent = !S.ready
      ? (S.failed ? `The saved data can't be reached: ${S.failMsg} The page tries again in two minutes.` : "Loading saved data…")
      : S.failed ? `Couldn't refresh (${S.failMsg}): showing the figures from ${timeLabel(S.loadedAt)}.`
      : "Saved on the bench. Everyone with access sees the same figures.";
    $("densityNote").textContent = nf2.format(st.density);

    // the period's totals: all the diesel; each bcm once (haul trucks' loads are their excavators')
    let tD = 0, tC = 0, tW = 0;
    S.summary.forEach((r) => { tD += r.diesel; tC += r.coalCounted; tW += r.wasteCounted; });
    const coalBcm = tC / st.density, tB = tW + coalBcm;
    const overall = tB > 0 ? tD / tB : null;
    $("kDiesel").textContent = fmt0(tD);
    $("kWaste").textContent = fmt0(tW);
    $("kCoal").textContent = fmt0(tC);
    $("kCoalUnit").textContent = "tons (" + fmt0(coalBcm) + " bcm)";
    $("kBcm").textContent = fmt0(tB);
    $("gaugeScope").textContent = (F.site === "all" ? "All sites" : siteName(F.site)) + " · " + periodText();
    $("periodLabel").textContent = periodText();
    drawGauge(overall);

    // each machine over the period, on its own volumes
    const rows = S.summary.map((r) => {
      const q = equip(r.equipmentId), bcm = r.waste + r.coal / st.density;
      return { ...r, id: r.equipmentId, name: q.name, type: q.type, bcm, lbcm: bcm > 0 ? r.diesel / bcm : null,
        hauled: r.coal + r.waste > 1e-9 && r.coalCounted + r.wasteCounted < r.coal + r.waste - 1e-6, auto: r.nAuto > 0 };
    }).sort((a, b) => (b.lbcm == null ? -1 : b.lbcm) - (a.lbcm == null ? -1 : a.lbcm) || b.diesel - a.diesel);
    // (a chart that fails leaves the tables and forms below drawn)
    try { drawChart(rows); }
    catch (e) {
      console.error(e);
      $("chartEmpty").hidden = false; $("chartEmpty").textContent = "The chart couldn't be drawn. The table below has the same figures.";
    }
    drawBurn();
    const shownEq = F.showAllEq ? rows : rows.slice(0, EQ_LIST);
    $("eqMoreWrap").hidden = rows.length <= EQ_LIST || F.showAllEq;
    $("btnEqMore").textContent = `Show all ${fmt0(rows.length)} machines`;
    $("eqTable").innerHTML = shownEq.length ? shownEq.map((r) => {
      const s = status(r.lbcm);
      return `<tr><td><strong>${esc(r.name)}</strong></td><td>${esc(r.type)}</td><td>${esc(siteName(r.siteId))}</td><td class="r">${fmt0(r.diesel)}</td><td class="r">${fmt0(r.coal)}</td><td class="r">${fmt0(r.waste)}</td><td class="r">${fmt0(r.bcm)}${r.hauled ? '<span class="hauled" title="Its loads are the excavators\' bcm: the site counts them once">hauled</span>' : ""}</td><td class="r"><strong>${fmt2(r.lbcm)}</strong></td><td><span class="pill ${s}">${STATUS_TEXT[s]}</span></td></tr>`;
    }).join("") : `<tr><td colspan="9" class="muted">No entries for ${esc(periodText().toLowerCase() === "all dates" ? "any date" : periodText())}${F.site === "all" ? "" : " at " + esc(siteName(F.site))}.</td></tr>`;

    // the latest entries of the period (all of them, up to 2 000, after "Show all entries")
    const del = allowed("deleteEntry"), n = S.entryCount;
    $("entryCount").textContent = fmt0(n) + (n === 1 ? " entry" : " entries") + (S.entries.length < n ? ` · the latest ${fmt0(S.entries.length)} shown` : "");
    $("moreWrap").hidden = n <= LIST || F.showAll;
    $("entryTable").innerHTML = S.entries.length ? S.entries.map((e) => {
      const b = bcmOf(e), l = b > 0 ? (+e.diesel || 0) / b : null, s = status(l);
      const tag = e.auto ? '<span class="tag" title="Brought in from the Daily Diesel Sheets and Hourly Production">records</span>' : "";
      return `<tr><td>${esc(dateLabel(e.date))}</td><td>${esc(siteName(e.siteId))}</td><td>${esc(equip(e.equipmentId).name)}${tag}</td><td class="r">${fmt0(e.diesel)}</td><td class="r">${fmt0(e.coal)}</td><td class="r">${fmt0(e.waste)}</td><td class="r" style="color:var(--${s === "none" ? "muted" : s})"><strong>${fmt2(l)}</strong></td><td class="r">${del && !e.auto ? `<button type="button" class="btn-del" data-del="entries" data-id="${esc(e.id)}">Delete</button>` : ""}</td></tr>`;
    }).join("") : `<tr><td colspan="8" class="muted">Nothing logged in this period yet.</td></tr>`;

    drawForms();
    drawSetup();
  }

  let newSite = null; // (the site just added: the equipment form turns to it)
  function drawForms() {
    const siteOpts = S.sites.slice().sort((a, b) => a.name.localeCompare(b.name)).map((s) => [s.id, s.name]);
    const none = [["", "Add a site first"]];
    const eSite = $("eSite");
    const wantSite = eSite.value || (F.site !== "all" ? F.site : "");
    fillSelect(eSite, siteOpts.length ? siteOpts : none, wantSite);
    fillSelect($("qSite"), siteOpts.length ? siteOpts : none, newSite || $("qSite").value || wantSite);
    newSite = null;
    const eqOpts = S.equipment.filter((q) => q.siteId === eSite.value).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true })).map((q) => [q.id, q.name + " (" + q.type + ")"]);
    fillSelect($("eEquip"), eqOpts.length ? eqOpts : [["", siteOpts.length ? "Add equipment to this site" : "Add a site first"]], $("eEquip").value);
    if (!$("eDate").value) $("eDate").value = todayISO();
    const ss = S.settings;
    [["stDensity", ss.density], ["stGreen", ss.green], ["stAmber", ss.amber], ["stMax", ss.max]].forEach(([id, v]) => {
      const el = $(id); if (document.activeElement !== el && !el.dataset.dirty) el.value = v;
    });
    preview();
  }

  // sites and their machines; what the production records bring in cannot be deleted here (it would come back)
  function drawSetup() {
    const sites = S.sites.slice().sort((a, b) => a.name.localeCompare(b.name));
    const del = allowed("deleteSetup");
    const chip = (q) => `<span class="chip">${esc(q.name)} <em>${esc(q.type)}</em>${del && !q.nAuto ? `<button type="button" class="btn-del" data-del="equipment" data-id="${esc(q.id)}" aria-label="Delete ${esc(q.name)}">✕</button>` : ""}</span>`;
    const openSites = new Set(Array.from(document.querySelectorAll("#siteList details[open]")).map((d) => d.dataset.site));
    $("siteList").innerHTML = sites.length ? sites.map((s) => {
      const eq = S.equipment.filter((q) => q.siteId === s.id).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
      const first = eq.slice(0, 12), rest = eq.slice(12);
      const linked = s.location ? `<span class="tag" title="Its entries come in from the production records of ${esc(s.location)}">records</span>` : "";
      return `<div class="site-block"><div class="site-line"><strong>${esc(s.name)}${linked}</strong>${del && !s.nAuto ? `<button type="button" class="btn-del" data-del="sites" data-id="${esc(s.id)}">Delete site</button>` : `<span class="muted small">${fmt0(eq.length)} machines</span>`}</div>
        <div class="chips">${first.length ? first.map(chip).join("") : '<span class="muted small">No equipment yet</span>'}</div>
        ${rest.length ? `<details class="more" data-site="${esc(s.id)}"${openSites.has(s.id) ? " open" : ""}><summary>${fmt0(rest.length)} more</summary><div class="chips">${rest.map(chip).join("")}</div></details>` : ""}</div>`;
    }).join("") : `<p class="muted small" style="margin:0">${allowed("setup") ? "No sites yet. Add one on the right." : "No sites yet."}</p>`;
  }

  function preview() {
    const d = parseFloat($("eDiesel").value), c = parseFloat($("eCoal").value) || 0, w = parseFloat($("eWaste").value) || 0;
    const b = w + c / S.settings.density;
    const el = $("ePreview");
    if (!(d > 0)) { el.textContent = ""; return; }
    if (!(b > 0)) { el.textContent = "No volume entered, so this entry adds diesel only."; return; }
    const l = d / b; el.innerHTML = `This entry: <strong style="color:var(--${status(l)})">${fmt2(l)} L/bcm</strong> over ${fmt0(b)} bcm`;
  }

  /* ---------- events ---------- */
  const periodChanged = () => { F.showAll = false; F.showAllEq = false; render(); refreshSoon(); };
  $("fSite").addEventListener("change", (e) => { F.site = e.target.value; periodChanged(); });
  $("fMonth").addEventListener("change", (e) => {
    const v = e.target.value; F.month = v;
    if (v === "all") { F.from = ""; F.to = ""; }
    else if (v.length === 7) [F.from, F.to] = monthRange(v);
    periodChanged();
  });
  ["fFrom", "fTo"].forEach((id) => $(id).addEventListener("change", () => {
    F.from = $("fFrom").value; F.to = $("fTo").value;
    if (F.from && F.to && F.from > F.to) { [F.from, F.to] = [F.to, F.from]; }
    const match = F.from && F.from.slice(0, 7) === (F.to || "").slice(0, 7) && monthRange(F.from.slice(0, 7)).join() === [F.from, F.to].join();
    F.month = match ? F.from.slice(0, 7) : (!F.from && !F.to ? "all" : "custom");
    $("fFrom").blur(); periodChanged();
  }));
  $("btnMore").addEventListener("click", () => { F.showAll = true; refresh(); });
  $("btnEqMore").addEventListener("click", () => { F.showAllEq = true; render(); });
  $("eSite").addEventListener("change", () => { $("eEquip").value = ""; render(); });
  ["eDiesel", "eCoal", "eWaste"].forEach((id) => $(id).addEventListener("input", preview));
  ["stDensity", "stGreen", "stAmber", "stMax"].forEach((id) => $(id).addEventListener("input", (e) => { e.target.dataset.dirty = "1"; }));

  $("entryForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const siteId = $("eSite").value, equipmentId = $("eEquip").value, date = $("eDate").value;
    const diesel = parseFloat($("eDiesel").value), coal = parseFloat($("eCoal").value) || 0, waste = parseFloat($("eWaste").value) || 0;
    if (!siteId) return toast("Add a site under Setup first.", true);
    if (!equipmentId) return toast("Pick a machine. Add equipment to this site under Setup if the list is empty.", true);
    if (!date) return toast("Enter the date for this entry.", true);
    if (!(diesel >= 0) || coal < 0 || waste < 0) return toast("Diesel, coal and waste must be zero or more.", true);
    $("eSubmit").disabled = true;
    try {
      await backend.add("entries", { date, siteId, equipmentId, diesel, coal, waste });
      toast("Entry saved for " + equip(equipmentId).name);
      $("eDiesel").value = ""; $("eCoal").value = ""; $("eWaste").value = ""; preview(); $("eDiesel").focus();
    } catch (e) { toast(errMsg(e), true); }
    $("eSubmit").disabled = false;
  });

  $("siteForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const name = $("sName").value.trim(); if (!name) return;
    if (S.sites.some((s) => s.name.toLowerCase() === name.toLowerCase())) return toast("A site with that name already exists.", true);
    try { newSite = await backend.add("sites", { name }); $("sName").value = ""; toast("Site added: " + name); render(); }
    catch (e) { toast(errMsg(e), true); }
  });

  $("equipForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const siteId = $("qSite").value, name = $("qName").value.trim(), type = $("qType").value;
    if (!siteId) return toast("Add a site first.", true);
    if (!name) return;
    if (S.equipment.some((q) => q.siteId === siteId && q.name.toLowerCase() === name.toLowerCase())) return toast("That site already has equipment called " + name + ".", true);
    try { await backend.add("equipment", { siteId, name, type }); $("qName").value = ""; toast(name + " added to " + siteName(siteId)); }
    catch (e) { toast(errMsg(e), true); }
  });

  $("settingsForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const o = { density: parseFloat($("stDensity").value), green: parseFloat($("stGreen").value), amber: parseFloat($("stAmber").value), max: parseFloat($("stMax").value) };
    if (!(o.density > 0)) return toast("Coal density must be more than 0.", true);
    if (!(o.green >= 0) || !(o.amber > o.green)) return toast("The amber limit must be higher than the green limit.", true);
    if (!(o.max > o.amber)) return toast("The gauge maximum must be higher than the amber limit.", true);
    try { await backend.saveSettings(o); ["stDensity", "stGreen", "stAmber", "stMax"].forEach((id) => delete $(id).dataset.dirty); toast("Settings saved"); render(); }
    catch (e) { toast(errMsg(e), true); }
  });

  // bring the period shown in from the production records now (every day of them for "All dates")
  $("btnSync").addEventListener("click", async () => {
    const btn = $("btnSync"); btn.disabled = true; btn.textContent = "Bringing them in…";
    try {
      const r = await write("sync_now", { from_date: F.from, to_date: F.to });
      const x = r.sync || {};
      toast(`From the production records, ${x.from ? dateLabel(x.from) : "–"} to ${x.to ? dateLabel(x.to) : "–"}: ${fmt0(x.added)} added, ${fmt0(x.updated)} updated, ${fmt0(x.removed)} removed.`, false, 6000);
    } catch (e) { toast(errMsg(e), true); }
    btn.disabled = false; btn.textContent = "Update now";
  });

  /* two-click delete (a site or machine takes its entries with it, on the server) */
  document.addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-del]"); if (!b) return;
    if (!b.classList.contains("armed")) {
      document.querySelectorAll(".btn-del.armed").forEach((x) => { x.classList.remove("armed"); x.textContent = x.dataset.label; });
      b.dataset.label = b.textContent; b.classList.add("armed");
      const col = b.dataset.del, id = b.dataset.id;
      let extra = "";
      if (col === "sites") { const n = S.equipment.filter((q) => q.siteId === id).length, m = (S.sites.find((s) => s.id === id) || {}).n || 0; extra = n || m ? ` + ${n} machines, ${m} entries` : ""; }
      if (col === "equipment") { const m = (S.equipment.find((q) => q.id === id) || {}).n || 0; extra = m ? ` + ${m} entries` : ""; }
      b.textContent = "Confirm" + extra;
      setTimeout(() => { if (b.isConnected && b.classList.contains("armed")) { b.classList.remove("armed"); b.textContent = b.dataset.label; } }, 4000);
      return;
    }
    const col = b.dataset.del, id = b.dataset.id;
    b.disabled = true;
    try {
      await backend.remove(col, id);
      toast(col === "entries" ? "Entry deleted" : col === "equipment" ? "Equipment deleted" : "Site deleted");
    } catch (e) { toast(errMsg(e), true); b.disabled = false; refresh(); }
  });

  /* example data: made and removed on the server */
  $("btnLoadEx").addEventListener("click", async () => {
    const btn = $("btnLoadEx"); btn.disabled = true; btn.textContent = "Loading example data…";
    try {
      F.init = false; // (the period: the latest month, the example's)
      await write("load_example");
      toast("Example data loaded");
    } catch (e) { toast(errMsg(e), true); }
    btn.disabled = false; btn.textContent = "Load example data"; render();
  });
  $("btnClearEx").addEventListener("click", async () => {
    const btn = $("btnClearEx");
    if (btn.dataset.armed !== "1") { btn.dataset.armed = "1"; btn.textContent = "Click again to remove all example data"; setTimeout(() => { btn.dataset.armed = ""; btn.textContent = "Remove example data"; }, 4000); return; }
    btn.disabled = true; btn.textContent = "Removing…";
    try {
      F.init = false;
      const r = await write("clear_example");
      const kept = (r && r.kept) || [];
      toast(kept.length ? `Example data removed. Kept ${kept.join(", ")}: your own entries are logged against ${kept.length === 1 ? "it" : "them"}.` : "Example data removed", false);
    } catch (e) { toast(errMsg(e), true); }
    btn.disabled = false; btn.dataset.armed = ""; btn.textContent = "Remove example data"; render();
  });

  /* theme changes: recolour the chart */
  try { matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render); } catch (e) { /* old browser */ }
  new MutationObserver(render).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

  /* ---------- start: load, then look again every two minutes while the page is shown ---------- */
  render();
  refresh();
  setInterval(() => { if (document.visibilityState === "visible") refresh(); }, 120000);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && Date.now() - S.loadedAt > 15000) refresh(); });
})();
