/* Frappe theme for the Mining Simulation page (loaded in <head>, before the simulator).

   1. data-theme on <html> follows Frappe: the desk's theme when the page is framed in the desk
      (and live as the user switches), otherwise the user's desk theme setting; "Automatic"
      follows the operating system. It is always set, so frappe_theme.css applies.
   2. Frappe's theme variables (light and dark) and its fonts are read from the desk stylesheet
      and added to this page, so frappe_theme.css can map the simulator's tokens onto them.
   The page stays hidden until the variables are in (at most a few seconds), so it does not
   flash in the simulator's own colours first. */
(() => {
  const CFG = window.MINING_SIM || {};
  const root = document.documentElement;
  const os_dark = window.matchMedia('(prefers-color-scheme: dark)');
  root.classList.add('frappe-theme-loading');

  function parent_root() {
    try {
      if (window.parent !== window && window.parent.document) return window.parent.document.documentElement;
    } catch (e) { /* not framed by the desk */ }
    return null;
  }

  function wanted_theme() {
    const p = parent_root(), t = p && p.getAttribute('data-theme');
    if (t === 'light' || t === 'dark') return t;
    if (CFG.theme === 'Dark') return 'dark';
    if (CFG.theme === 'Light') return 'light';
    return os_dark.matches ? 'dark' : 'light'; // Automatic
  }

  // Redraw what the simulator paints itself; its 3D view repaints on the data-theme change.
  function repaint() {
    try { if (typeof Timeline !== 'undefined') Timeline.draw(); } catch (e) { /* not started yet */ }
    try { if (typeof UI !== 'undefined' && typeof App !== 'undefined' && App.P) UI.renderTab(); } catch (e) { /* not started yet */ }
    try { if (typeof Section !== 'undefined') Section.schedule(); } catch (e) { /* not started yet */ }
  }

  function apply_theme(force) {
    const t = wanted_theme();
    if (force || root.getAttribute('data-theme') !== t) {
      root.setAttribute('data-theme', t); // also when unchanged with force: the 3D view re-reads its colours
      requestAnimationFrame(repaint);
    }
  }

  apply_theme();
  os_dark.addEventListener && os_dark.addEventListener('change', () => apply_theme());
  const p = parent_root();
  if (p) {
    const watch = new window.parent.MutationObserver(() => apply_theme());
    watch.observe(p, { attributes: true, attributeFilter: ['data-theme'] });
    window.addEventListener('pagehide', () => watch.disconnect()); // the frame reloads with each project opened
  }

  // ---- Frappe's theme variables and fonts, taken from the desk stylesheet
  const THEME_SELECTOR = /^(:root|html)?(\[data-theme="?(light|dark)"?\])?$/;
  function collect(rules, out) {
    for (const r of rules) {
      if (r instanceof CSSFontFaceRule) out.push(r.cssText);
      else if (r instanceof CSSStyleRule) {
        // e.g. "[data-theme=dark], .dark": keep the theme selectors of a rule of variables only
        const selectors = r.selectorText.split(',').map(s => s.trim()).filter(s => s && THEME_SELECTOR.test(s));
        const props = Array.from(r.style);
        if (selectors.length && props.length && props.every(n => n.startsWith('--'))) out.push(`${selectors.join(', ')} { ${r.style.cssText} }`);
      } else if (r instanceof CSSMediaRule) {
        const inner = [];
        collect(r.cssRules, inner);
        if (inner.length) out.push(`@media ${r.conditionText} {${inner.join('\n')}}`);
      }
    }
  }

  let done = false;
  function ready() {
    if (done) return;
    done = true;
    root.classList.remove('frappe-theme-loading');
    apply_theme(true);
  }

  if (!CFG.deskCss) { ready(); return; }
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.media = 'not all'; // read only: the desk's own rules do not apply here
  link.href = CFG.deskCss;
  link.onload = () => {
    try {
      const out = [];
      collect(link.sheet.cssRules, out);
      const style = document.createElement('style');
      style.id = 'frappe-theme-vars';
      style.textContent = out.join('\n');
      document.head.prepend(style);
    } catch (e) { console.warn('Frappe theme variables could not be read', e); }
    link.remove();
    ready();
  };
  link.onerror = () => { link.remove(); ready(); };
  document.head.appendChild(link);
  setTimeout(ready, 4000); // never leave the page hidden
})();
