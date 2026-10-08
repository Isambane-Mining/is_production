/* Frappe storage for the Mining Simulation page.

   Loaded after the simulator's own script (www/mining_simulation.html) and before it starts.
   The simulator keeps projects through one Store object (list, load, save, remove and the
   binary grid / satellite blobs). This provides that Store on top of Mining Simulation
   Project documents and points the simulator's start-up at it; the simulator's code is
   not changed. */
(() => {
  const CFG = window.MINING_SIM || {};
  const API = '/api/method/is_production.geo_planning.services.mining_simulation_service.';

  async function call(method, args) {
    const r = await fetch(API + method, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-Frappe-CSRF-Token': CFG.csrfToken || '' },
      body: JSON.stringify(args || {})
    });
    let data = null;
    try { data = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) {
      let msg = r.status === 403 ? 'You do not have permission for this.' : `The server answered ${r.status}.`;
      try {
        const m = JSON.parse(JSON.parse(data._server_messages)[0]);
        if (m.message) msg = m.message.replace(/<[^>]+>/g, '');
      } catch (e) { if (data && data.exception) msg = String(data.exception).split(':').slice(1).join(':').trim() || msg; }
      throw new Error(msg);
    }
    return data ? data.message : null;
  }

  function FrappeStore() {
    const canWrite = !!CFG.canWrite;
    return {
      kind: 'frappe', canWrite,
      label: canWrite ? 'Saved in Mining Simulation Projects' : 'View only',
      async list() { return (await call('list_projects')) || []; },
      async load(id) { return call('load_project', { name: id }); },
      async save(P) { P.modified = new Date().toISOString(); await call('save_project', { project: JSON.stringify(P) }); },
      async remove(id) { await call('delete_project', { name: id }); },
      async putBlob(id, key, u8) { await call('put_blob', { name: id, key, data: U.u8ToB64(await U.gzip(u8)) }); },
      async getBlob(id, key) { const b64 = await call('get_blob', { name: id, key }); return b64 ? U.gunzip(U.b64ToU8(b64)) : null; },
      async delBlob(id, key) { await call('delete_blob', { name: id, key }); }
    };
  }

  // The simulator uses browser storage (Store.IDB) when it is not a Claude artifact: use Frappe instead.
  Store.IDB = async () => FrappeStore();

  // Open the project named in the URL (?project=...): the simulator opens its last project first.
  if (CFG.project) { try { localStorage.setItem('rollover.lastProject', CFG.project); } catch (e) { /* storage blocked */ } }

  // With no projects yet, the simulator shows its example colliery; only keep it if someone asks for it.
  let starting = true;
  const boot = App.boot.bind(App), openDemo = App.openDemo.bind(App);
  App.boot = async function () { try { return await boot(); } finally { starting = false; } };
  App.openDemo = function (persist) { return openDemo(persist && !starting); };
})();
