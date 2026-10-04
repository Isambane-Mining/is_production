"""Create the Asset Categories is_production reports on, against this site's own accounts.

These used to be a fixture, but the fixture carried Isambane/Excavo account
names ("Capital Equipments - ISA" ...), so installing is_production on any site
with a different chart of accounts failed. is_production only relies on the
category names; the accounts are company accounting data.

Runs after install and after every migrate. Existing categories are never
touched, so sites that already have them (prod) keep their own accounts.
"""

import frappe

ASSET_CATEGORIES = ("Dozer", "ADT", "Excavator")


def ensure_asset_categories():
	if not frappe.db.table_exists("Asset Category"):
		return

	missing = [name for name in ASSET_CATEGORIES if not frappe.db.exists("Asset Category", name)]
	if not missing:
		return

	account_row = _get_default_company_account_row()
	if not account_row:
		# No company / chart of accounts yet (e.g. before the setup wizard).
		# The next migrate tries again.
		frappe.logger("is_production").info(
			"Asset Categories %s not created: no default company with a Fixed Asset account yet.",
			", ".join(missing),
		)
		return

	for name in missing:
		frappe.get_doc(
			{
				"doctype": "Asset Category",
				"asset_category_name": name,
				"accounts": [account_row],
			}
		).insert(ignore_permissions=True)


def _get_default_company_account_row() -> dict | None:
	company = frappe.defaults.get_global_default("company") or frappe.db.get_value(
		"Company", {}, "name", order_by="creation asc"
	)
	if not company:
		return None

	filters = {"company": company, "account_type": "Fixed Asset", "is_group": 0, "disabled": 0}
	# Prefer the standard chart's "Capital Equipment" ledger, else the first Fixed Asset ledger.
	fixed_asset_account = frappe.db.get_value(
		"Account", {**filters, "account_name": ["like", "Capital Equipment%"]}, "name", order_by="lft asc"
	) or frappe.db.get_value("Account", filters, "name", order_by="lft asc")
	if not fixed_asset_account:
		return None

	defaults = frappe.db.get_value(
		"Company",
		company,
		["accumulated_depreciation_account", "depreciation_expense_account"],
		as_dict=True,
	)

	return {
		"company_name": company,
		"fixed_asset_account": fixed_asset_account,
		"accumulated_depreciation_account": defaults.accumulated_depreciation_account,
		"depreciation_expense_account": defaults.depreciation_expense_account,
	}
