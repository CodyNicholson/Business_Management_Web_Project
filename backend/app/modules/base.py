"""Core data: company, users, groups, contacts, attachments, activities."""
from datetime import date

from ..orm import F, UserError, app, define_groups, define_sequence, menu, model
from ..security import end_all_sessions, hash_password
from ..views import btn, col, flt, form, kanban, list_view, search, section, smart, tab

define_groups(
    ("base.user", "Employee (everyone)"), ("base.admin", "Administrator"),
    ("crm.user", "CRM: User"), ("crm.manager", "CRM: Manager"),
    ("sales.user", "Sales: User"), ("sales.manager", "Sales: Manager"),
    ("account.user", "Accounting: Billing"), ("account.manager", "Accounting: Adviser"),
    ("purchase.user", "Purchase: User"), ("purchase.manager", "Purchase: Manager"),
    ("stock.user", "Inventory: User"), ("stock.manager", "Inventory: Manager"),
    ("mrp.user", "Manufacturing: User"), ("mrp.manager", "Manufacturing: Manager"),
    ("project.user", "Project: User"), ("project.manager", "Project: Manager"),
    ("hr.user", "HR: Officer"), ("hr.manager", "HR: Manager"),
    ("helpdesk.user", "Helpdesk: Agent"), ("helpdesk.manager", "Helpdesk: Manager"),
    ("pos.user", "Point of Sale: Cashier"), ("pos.manager", "Point of Sale: Manager"),
    ("marketing.user", "Marketing: User"),
    ("events.user", "Events: User"),
    ("fleet.user", "Fleet: Manager"),
    ("maintenance.user", "Maintenance: User"),
    ("subscription.user", "Subscriptions: User"),
)

define_sequence("sale.order", "S")
define_sequence("purchase.order", "P")
define_sequence("stock.in", "WH/IN/")
define_sequence("stock.out", "WH/OUT/")
define_sequence("stock.int", "WH/INT/")
define_sequence("mrp.production", "MO/")
define_sequence("helpdesk.ticket", "TCK")
define_sequence("pos.order", "POS/")
define_sequence("subscription", "SUB")
define_sequence("account.payment", "PAY/")
define_sequence("hr.payslip", "SLIP/")

ADMIN = {"base.admin": "rwcd"}
EVERYONE_READ = {"base.user": "r"}

company = model("res.company", "Company", [
    F.char("name", "Company Name", required=True),
    F.char("email", "Email"), F.char("phone", "Phone"), F.char("website", "Website"),
    F.char("street", "Street"), F.char("city", "City"), F.char("zip", "ZIP"), F.char("country", "Country"),
    F.char("tax_id", "Tax ID"), F.char("currency_symbol", "Currency Symbol", default="$"),
    F.char("currency_code", "Currency Code", default="USD"),
    F.date("lock_date", "Lock Date", help="Journal entries on or before this date cannot be posted or changed."),
], access={**ADMIN, **EVERYONE_READ}, views={
    "list": list_view("name", "email", "phone", "currency_code", "lock_date"),
    "form": form(sections=[section(col("email", "phone", "website", "tax_id"),
                                   col("street", "city", "zip", "country"), title="General"),
                           section(col("currency_symbol", "currency_code"), col("lock_date"), title="Accounting")]),
})

group = model("res.group", "Group", [
    F.char("key", "Key", required=True, readonly=True), F.char("name", "Name", required=True, readonly=True),
], access={**ADMIN, **EVERYONE_READ}, order="name ASC", constraints=[("key_uniq", "UNIQUE (key)")], views={
    "list": list_view("name", "key"), "form": form(sections=[section(col("key"))]),
})

users = model("res.users", "User", [
    F.char("name", "Name", required=True), F.char("email", "Email", required=True),
    F.bool("active", "Active", default=True),
    F.m2m("group_ids", "Access Rights", "res.group", "res_users_groups"),
    F.sql("has_login", "Can Sign In", "bool", "SELECT password_hash IS NOT NULL FROM res_users x WHERE x.id = {t}.id"),
], access={**ADMIN, **EVERYONE_READ}, order="name ASC", search_fields=["name", "email"],
    constraints=[("email_uniq", "UNIQUE (email)")], views={
    "list": list_view("name", "email", "has_login", "active"),
    "form": form(buttons=[btn("set_password", "Set Password", primary=True),
                          btn("archive", "Archive", when=[["active", "=", True]]),
                          btn("unarchive", "Restore", when=[["active", "=", False]])],
                 sections=[section(col("email", "active", "has_login"),
                                   tabs=[tab("Access Rights", {"field": "group_ids", "widget": "checkboxes"})])]),
    "search": search(fields=["name", "email"], filters=[flt("archived", "Archived", [["active", "=", False]])]),
})


@users.hook("prepare_create")
@users.hook("prepare_write")
def _normalize_email(env, *args):
    vals = args[-1]
    if vals.get("email"):
        vals["email"] = vals["email"].strip().lower()
        if "@" not in vals["email"]:
            raise UserError("Enter a valid email address.")
    return vals


@users.action("set_password", "Set Password", groups=["base.admin"],
              params=[{"name": "password", "label": "New Password", "type": "password", "required": True}])
def set_password(env, ids, params):
    hashed = hash_password(str(params.get("password") or ""))
    env.cur.execute("UPDATE res_users SET password_hash = %s WHERE id = ANY(%s)", (hashed, list(ids)))
    for user_id in ids:
        end_all_sessions(env.cur, user_id)
    return {"type": "notify", "message": "Password updated. Existing sessions were signed out."}


@users.action("archive", "Archive", groups=["base.admin"])
def archive_user(env, ids, params):
    if env.user.id in ids:
        raise UserError("You cannot archive your own account.")
    env.sudo()["res.users"].write(ids, {"active": False})
    for user_id in ids:
        end_all_sessions(env.cur, user_id)


@users.action("unarchive", "Restore", groups=["base.admin"])
def unarchive_user(env, ids, params):
    env.sudo()["res.users"].write(ids, {"active": True})


tag = model("res.partner.tag", "Contact Tag", [F.char("name", "Name", required=True)],
            access={"base.user": "rwc", "base.admin": "rwcd"}, order="name ASC",
            views={"list": list_view("name"), "form": form()})

partner = model("res.partner", "Contact", [
    F.char("name", "Name", required=True, tracked=True),
    F.sel("kind", "Type", [("company", "Company"), ("person", "Individual")], default="company"),
    F.m2o("parent_id", "Company", "res.partner", domain=[["kind", "=", "company"]], ondelete="SET NULL"),
    F.char("job_title", "Job Position"),
    F.char("email", "Email", tracked=True), F.char("phone", "Phone", tracked=True), F.char("website", "Website"),
    F.char("street", "Street"), F.char("city", "City"), F.char("state_name", "State"), F.char("zip", "ZIP"),
    F.char("country", "Country"), F.char("tax_id", "Tax ID"), F.text("notes", "Notes"),
    F.bool("is_customer", "Is a Customer", default=True), F.bool("is_vendor", "Is a Vendor", default=False),
    F.m2m("tag_ids", "Tags", "res.partner.tag", "res_partner_tags"),
    F.m2o("salesperson_id", "Salesperson", "res.users", ondelete="SET NULL"),
    F.o2m("child_ids", "Contacts", "res.partner", "parent_id", columns=["name", "job_title", "email", "phone"]),
    F.bool("active", "Active", default=True),
    F.sql("total_invoiced", "Invoiced", "money",
          "SELECT COALESCE(SUM(amount_total * CASE WHEN m.move_type = 'out_refund' THEN -1 ELSE 1 END), 0) "
          "FROM account_move m WHERE m.partner_id = {t}.id AND m.state = 'posted' "
          "AND m.move_type IN ('out_invoice', 'out_refund')", groups=["account.user", "account.manager", "sales.user", "sales.manager"]),
], access={"base.user": "r", "crm.user": "rwc", "sales.user": "rwc", "purchase.user": "rwc", "account.user": "rwc",
           "helpdesk.user": "rwc", "hr.user": "rwc", "sales.manager": "rwcd", "account.manager": "rwcd", "base.admin": "rwcd"},
    mail=True, order="name ASC", search_fields=["name", "email", "phone"], views={
    "kanban": kanban(title="name", subtitle="job_title", fields=["email", "phone", "city"]),
    "list": list_view("name", "phone", "email", "city", "country", "salesperson_id", "tag_ids"),
    "form": form(smart_buttons=[
        smart("Sales", "shopping-cart", "sale.order", [["partner_id", "=", "$id"]]),
        smart("Invoices", "file-text", "account.move", [["partner_id", "=", "$id"], ["move_type", "in", ["out_invoice", "out_refund"]]]),
        smart("Purchases", "truck", "purchase.order", [["partner_id", "=", "$id"]]),
        smart("Tickets", "life-buoy", "helpdesk.ticket", [["partner_id", "=", "$id"]]),
        smart("Opportunities", "target", "crm.lead", [["partner_id", "=", "$id"]]),
    ], chatter=True, sections=[
        section(col({"field": "kind", "widget": "radio"}, "parent_id", "job_title", "street", "city", "state_name", "zip", "country", "tax_id"),
                col("phone", "email", "website", "tag_ids", "salesperson_id", "is_customer", "is_vendor", "total_invoiced"),
                tabs=[tab("Contacts & Addresses", "child_ids"), tab("Internal Notes", "notes")]),
    ]),
    "search": search(fields=["name", "email", "phone", "city"], filters=[
        flt("customers", "Customers", [["is_customer", "=", True]]),
        flt("vendors", "Vendors", [["is_vendor", "=", True]]),
        flt("companies", "Companies", [["kind", "=", "company"]]),
        flt("people", "Individuals", [["kind", "=", "person"]]),
        flt("archived", "Archived", [["active", "=", False]]),
    ], group_by=["kind", "country", "salesperson_id"]),
})

model("ir.attachment", "Attachment", [
    F.char("name", "File Name", required=True, readonly=True), F.char("mimetype", "Type", readonly=True),
    F.int("size", "Size", readonly=True), F.char("storage_key", "Storage Key", readonly=True, groups=["base.admin"]),
    F.char("res_model", "Model", readonly=True), F.int("res_id", "Record", readonly=True),
], access=EVERYONE_READ, views={"list": list_view("name", "mimetype", "size", "create_date")},
    rule=lambda env: [["create_uid", "=", env.user.id]])


def _activity_rule(env):
    return [["user_id", "=", env.user.id]]


activity = model("mail.activity", "Activity", [
    F.char("name", "Summary", required=True),
    F.sel("kind", "Type", [("todo", "To-Do"), ("call", "Call"), ("email", "Email"), ("meeting", "Meeting")], default="todo"),
    F.date("date_deadline", "Due Date", required=True, default=lambda env: date.today()),
    F.m2o("user_id", "Assigned to", "res.users", required=True, default=lambda env: env.user.id or None, ondelete="CASCADE"),
    F.char("res_model", "Document Model", readonly=True), F.int("res_id", "Document", readonly=True),
    F.text("note", "Note"),
    F.bool("done", "Done", default=False),
], access={"base.user": "rwcd"}, order="date_deadline ASC", rule=_activity_rule, views={
    "list": list_view("name", "kind", "date_deadline", "user_id", "res_model"),
    "form": form(sections=[section(col("kind", "date_deadline", "user_id"), col("note"))]),
    "search": search(fields=["name"], filters=[flt("open", "Open", [["done", "=", False]])]),
})

app("contacts", "Contacts", "contact", "#2f7f93", [
    menu("all", "Contacts", "res.partner", ("kanban", "list", "form")),
    menu("config", "Configuration", children=[menu("tags", "Contact Tags", "res.partner.tag")]),
], sequence=20)

app("settings", "Settings", "settings", "#55606e", [
    menu("users", "Users", "res.users"),
    menu("company", "Company", "res.company"),
    menu("groups", "Groups", "res.group"),
], groups=["base.admin"], sequence=999)
