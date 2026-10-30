"""Recurring contracts. The scheduler creates a draft invoice each time one falls due."""
import calendar as cal
from datetime import date, timedelta

from ..orm import F, UserError, app, menu, model
from ..views import btn, col, flt, form, graph, kanban, list_view, search, section, smart, tab
from .account import _id, line_amounts, m2o_value

SUB = {"subscription.user": "rwcd", "base.admin": "rwcd", "account.manager": "r"}
PERIODS = {"month": 1, "quarter": 3, "year": 12}
sub = model("subscription.subscription", "Subscription", [
    F.char("name", "Reference", readonly=True, sequence="subscription"), F.m2o("partner_id", "Customer", "res.partner", required=True, tracked=True),
    F.sel("period", "Recurrence", [("month", "Monthly"), ("quarter", "Quarterly"), ("year", "Yearly")], default="month", required=True, tracked=True),
    F.date("date_start", "Start Date", required=True, default=lambda env: date.today()), F.date("next_invoice_date", "Next Invoice", default=lambda env: date.today(), tracked=True), F.date("date_end", "End Date"),
    F.m2o("user_id", "Salesperson", "res.users", default=lambda env: env.user.id or None, ondelete="SET NULL"),
    F.sel("state", "Status", [("draft", "Quotation"), ("open", "In Progress"), ("closed", "Closed")], readonly=True, default="draft", tracked=True),
    F.o2m("line_ids", "Products", "subscription.line", "subscription_id", columns=["product_id", "name", "quantity", "price_unit", "tax_id", "price_subtotal"]),
    F.money("recurring_total", "Recurring Total", readonly=True, default=0),
    F.sql("mrr", "Monthly Recurring Revenue", "money", "SELECT ROUND({t}.recurring_total / CASE {t}.period WHEN 'quarter' THEN 3 WHEN 'year' THEN 12 ELSE 1 END, 2)"),
], access=SUB, mail=True, locked_when=[["state", "=", "closed"]], views={
    "kanban": kanban(group_by="state", title="partner_id", subtitle="name", amount="recurring_total", date="next_invoice_date", user="user_id"),
    "list": list_view("name", "partner_id", "period", "next_invoice_date", {"field": "recurring_total", "sum": True}, {"field": "mrr", "sum": True}, {"field": "state", "widget": "badge"}),
    "form": form(status="state", chatter=True, buttons=[
        btn("start", "Start", primary=True, when=[["state", "=", "draft"]]), btn("invoice", "Generate Invoice", when=[["state", "=", "open"]]), btn("close", "Close", when=[["state", "=", "open"]]),
    ], smart_buttons=[smart("Invoices", "file-text", "account.move", [["subscription_id", "=", "$id"]])], sections=[section(col("partner_id", "period", "user_id"), col("date_start", "next_invoice_date", "date_end", "mrr"), tabs=[tab("Products", {"field": "line_ids", "totals": ["recurring_total"]})])]),
    "graph": graph("partner_id", ["mrr"]),
    "search": search(fields=["name", "partner_id.name"], filters=[flt("open", "In Progress", [["state", "=", "open"]]), flt("due", "Due", [["state", "=", "open"], ["next_invoice_date", "<=", "$today"]])], group_by=["partner_id", "period", "state"]),
})
line = model("subscription.line", "Subscription Line", [
    F.m2o("subscription_id", "Subscription", "subscription.subscription", required=True, ondelete="CASCADE"), F.m2o("product_id", "Product", "product.product", required=True, onchange=True), F.char("name", "Description", required=True),
    F.float("quantity", "Quantity", default=1, onchange=True), F.money("price_unit", "Unit Price", default=0, onchange=True), F.m2o("tax_id", "Tax", "account.tax", domain=[["scope", "=", "sale"]], onchange=True), F.money("price_subtotal", "Subtotal", readonly=True, default=0),
], access={"subscription.user": "rwcd", "base.admin": "rwcd", "account.manager": "r"}, parent="subscription_id", order="id ASC")


@line.set_onchange
def _onchange(env, values, changed, parent=None):
    out = {}
    if changed == "product_id" and values.get("product_id"):
        p = env.sudo()["product.product"].get(_id(values["product_id"]))
        out = {"name": p["name"], "price_unit": float(p["list_price"]), "tax_id": m2o_value(env, "account.tax", p["sale_tax_id"])}
    merged = {**values, **out}
    subtotal, _ = line_amounts(env, merged.get("quantity") or 0, merged.get("price_unit") or 0, _id(merged.get("tax_id")))
    out["price_subtotal"] = float(subtotal)
    return out


@line.hook("recompute")
def _recompute_line(env, ids):
    for rec in env["subscription.line"].read(ids, ["id", "quantity", "price_unit", "tax_id"]):
        subtotal, _ = line_amounts(env, rec["quantity"], rec["price_unit"], rec["tax_id"])
        env.cur.execute("UPDATE subscription_line SET price_subtotal = %s WHERE id = %s", (subtotal, rec["id"]))


@sub.hook("recompute")
def _recompute(env, ids):
    for sub_id in ids:
        env.cur.execute("UPDATE subscription_subscription SET recurring_total = (SELECT COALESCE(SUM(price_subtotal), 0) FROM subscription_line WHERE subscription_id = %s) WHERE id = %s", (sub_id, sub_id))


def add_months(d: date, months: int) -> date:
    idx = d.month - 1 + months
    year, month = d.year + idx // 12, idx % 12 + 1
    return date(year, month, min(d.day, cal.monthrange(year, month)[1]))


@sub.action("start", "Start", groups=["subscription.user"])
def action_start(env, ids, params):
    env = env.sudo()
    for rec_id in ids:
        env.cur.execute("SELECT COUNT(*) FROM subscription_line WHERE subscription_id = %s", (rec_id,))
        if not env.cur.fetchone()[0]:
            raise UserError("Add at least one product first.")
    env["subscription.subscription"].write(ids, {"state": "open"})


def generate_invoice(env, sub_id: int) -> int:
    env = env.sudo()
    s = env["subscription.subscription"].get(sub_id)
    if s["state"] != "open":
        raise UserError("Only running subscriptions can be invoiced.")
    move_id = env["account.move"].create({"move_type": "out_invoice", "partner_id": s["partner_id"], "ref": s["name"], "date": date.today(), "invoice_date_due": date.today() + timedelta(days=30), "subscription_id": sub_id})
    env.cur.execute("SELECT l.product_id, l.name, l.quantity, l.price_unit, l.tax_id, p.income_account_id FROM subscription_line l JOIN product_product p ON p.id = l.product_id WHERE l.subscription_id = %s ORDER BY l.id", (sub_id,))
    for product_id, name, qty, price, tax_id, account_id in env.cur.fetchall():
        env["account.invoice.line"].create({"move_id": move_id, "product_id": product_id, "name": name, "quantity": qty, "price_unit": price, "tax_id": tax_id, "account_id": account_id})
    current = s["next_invoice_date"] or date.today()
    env["subscription.subscription"].write([sub_id], {"next_invoice_date": add_months(current, PERIODS[s["period"]])})
    return move_id


@sub.action("invoice", "Generate Invoice", groups=["subscription.user"])
def action_invoice(env, ids, params):
    return {"type": "open", "model": "account.move", "id": generate_invoice(env, ids[0])}


@sub.action("close", "Close", groups=["subscription.user"])
def action_close(env, ids, params):
    env.sudo()["subscription.subscription"].write(ids, {"state": "closed", "date_end": date.today()})


def invoice_due(env) -> int:
    env = env.sudo()
    env.cur.execute(
        """SELECT id FROM subscription_subscription WHERE state = 'open' AND next_invoice_date <= CURRENT_DATE
           AND (date_end IS NULL OR next_invoice_date <= date_end) FOR UPDATE SKIP LOCKED""")
    due = [r[0] for r in env.cur.fetchall()]
    for sub_id in due:
        generate_invoice(env, sub_id)
    return len(due)


app("subscriptions", "Subscriptions", "repeat", "#2d7d9a", [
    menu("all", "Subscriptions", "subscription.subscription", ("list", "kanban", "form", "graph")),
    menu("due", "To Renew", "subscription.subscription", ("list", "form"), [["state", "=", "open"], ["next_invoice_date", "<=", "$today"]]),
], groups=["subscription.user"], sequence=55)
