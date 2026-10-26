"""Keep existing workspace navigation pointed at the dedicated HOD Desk Page."""
import frappe


def sync_navigation():
    # Update only the existing HOD links, including site-customised workspaces.
    for link in frappe.get_all('Workspace Link', filters={
        'link_type': 'Report', 'link_to': 'HOD Presentation',
    }, fields=['name', 'parent']):
        frappe.db.set_value('Workspace Link', link.name, {
            'link_type': 'Page', 'link_to': 'hod-presentation',
            'is_query_report': 0, 'report_ref_doctype': None,
        }, update_modified=False)
        frappe.clear_document_cache('Workspace', link.parent)

    # Current Desk navigation uses Sidebar Item; Workspace Sidebar is an archive.
    if frappe.db.exists('DocType', 'Sidebar Item'):
        for item in frappe.get_all('Sidebar Item', filters={
            'link_type': 'Report', 'link_to': 'HOD Presentation',
        }, fields=['name', 'parent', 'parenttype', 'filters', 'route_options']):
            values = {'link_type': 'Page', 'link_to': 'hod-presentation'}
            options = _page_route_options(item)
            if options:
                values['route_options'] = frappe.as_json(options)
            frappe.db.set_value('Sidebar Item', item.name, values, update_modified=False)
            frappe.clear_document_cache(item.parenttype, item.parent)


def _page_route_options(item):
    """Page links read route_options; report links read their saved filters."""
    filters = frappe.parse_json(item.get('filters')) or {}
    if isinstance(filters, list):
        filters = {row[1]: [row[2], row[3]] for row in filters}
    options = {}
    for field, value in filters.items():
        if isinstance(value, list) and len(value) == 2 and value[0] == '=':
            value = value[1]
        options[field] = value
    # Explicit route options take precedence over legacy report filters.
    options.update(frappe.parse_json(item.get('route_options')) or {})
    return options
