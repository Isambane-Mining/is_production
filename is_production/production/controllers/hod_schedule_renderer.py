"""Capture the installed HOD dashboard offline, without credentials or an HTTP login."""
import base64
import json
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlsplit

ORIGIN = 'https://hod-render.invalid'


def asset_path(bench, url_path):
    bench = Path(bench).resolve()
    decoded = unquote(url_path)
    if not decoded.startswith('/assets/') or '..' in Path(decoded).parts:
        raise ValueError('Only installed public assets may be loaded.')
    path = (bench/'sites'/decoded.lstrip('/')).resolve()
    allowed = [bench/'apps', (bench/'sites/assets').resolve()]
    if not any(path.is_relative_to(root) for root in allowed):
        raise ValueError('Asset path escapes the public application directories.')
    if not path.is_file():
        raise FileNotFoundError(f'Missing installed asset: {url_path}')
    return path


def capture(rows, bench, app, *, no_sandbox=False):
    if not rows:
        raise ValueError('No site dashboards were supplied; refusing an empty presentation.')
    from playwright.sync_api import sync_playwright

    bench, app = Path(bench).resolve(), Path(app).resolve()
    css = asset_path(bench, '/assets/is_production/css/dashboards.css')
    if '.hod-performance-dashboard' not in css.read_text():
        raise ValueError('Installed dashboards.css does not contain the HOD dashboard styles.')
    script = app/'production/page/hod_presentation/hod_presentation.js'
    styles = []
    manifest_path = bench/'sites/assets/assets.json'
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        for key, value in manifest.items():
            if key.endswith('desk.bundle.css') and value not in styles:
                asset_path(bench, value)
                styles.append(value)
    styles.append('/assets/is_production/css/dashboards.css')
    failures, slides = [], []

    with sync_playwright() as engine:
        browser = engine.chromium.launch(headless=True, args=['--no-sandbox'] if no_sandbox else [])
        try:
            context = browser.new_context(viewport={'width':1600,'height':1000},
                device_scale_factor=1.5, locale='en-ZA', timezone_id='Africa/Johannesburg',
                color_scheme='light', service_workers='block')

            def route_request(route):
                url = urlsplit(route.request.url)
                if url.scheme == 'data':
                    route.continue_()
                    return
                if url.scheme != 'https' or url.netloc != 'hod-render.invalid':
                    failures.append('External resource blocked: '+url.netloc)
                    route.abort()
                    return
                if url.path == '/':
                    route.fulfill(status=200, content_type='text/html', body='<!doctype html><html><head></head><body></body></html>')
                    return
                if url.path == '/favicon.ico':
                    route.fulfill(status=204)
                    return
                try:
                    path = asset_path(bench, url.path)
                    route.fulfill(status=200, body=path.read_bytes(),
                        content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                except (ValueError, FileNotFoundError) as exc:
                    failures.append(str(exc))
                    route.abort()

            context.route('**/*', route_request)
            page = context.new_page()
            page.set_default_timeout(60000)
            page.goto(ORIGIN+'/', wait_until='load')
            for href in styles:
                page.add_style_tag(url=ORIGIN+href)
            page.add_style_tag(content='html,body{margin:0;padding:0;width:1600px;background:white;}body{font-family:Arial,sans-serif;}')
            page.evaluate("""() => {
                window.__ = value => value;
                window.frappe = {pages:{'hod-presentation':{}},datetime:{get_today:()=> '2000-01-01'}};
            }""")
            page.add_script_tag(path=str(script))
            for row in rows:
                page.evaluate("""row => {
                    document.body.innerHTML = hodDashboard(row);
                    const board = document.querySelector('.hod-performance-dashboard');
                    board.classList.add('hod-export-dashboard');
                    board.style.width = '1600px';
                }""", row)
                page.evaluate("""async () => {
                    await document.fonts.ready;
                    await Promise.all(Array.from(document.images).map(img => img.decode()));
                }""")
                if failures:
                    raise ValueError('Dashboard assets failed: '+'; '.join(dict.fromkeys(failures)))
                board = page.locator('.hod-performance-dashboard')
                geometry = board.evaluate("""el => ({width:el.getBoundingClientRect().width,
                    height:el.getBoundingClientRect().height,scrollWidth:el.scrollWidth})""")
                if geometry['width'] < 1599 or geometry['height'] < 100 or geometry['height'] > 4000 or geometry['scrollWidth'] > 1602:
                    raise ValueError('Dashboard has invalid dimensions or horizontal overflow: '+str(geometry))
                image = board.screenshot(type='jpeg',quality=96,animations='disabled',timeout=60000)
                slides.append({'title':row['site']+' · Production Performance', 'heading_style':'dashboard',
                    'image_data':'data:image/jpeg;base64,'+base64.b64encode(image).decode('ascii')})
        finally:
            browser.close()
    return slides
