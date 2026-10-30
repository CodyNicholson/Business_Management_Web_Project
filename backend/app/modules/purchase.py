"""Requests for quotation and purchase orders: receipts and vendor bills flow from the order."""
from datetime import date, timedelta

from ..orm import F, UserError, app, dec, menu, model
from ..views import btn, col, flt, form, graph, list_view, pivot, search, section, smart, tab
from .account import _id, line_amounts, m2o_value
from .stock import location_by_usage, main_stock

PURCHASE = {"purchase.user": "rwc", "purchase.manager": "rwcd", "base.admin": "rwcd", "account.user": "r", "account.manager": "r", "stock.user": "r"}
order = model("purchase.order", "Purchase Order", [
    F.char("name", "Order Reference", readonly=True, sequence="purchase.order"),
    F.m2o("partner_id", "Vendor", "res.partner", required=True, tracked=True, domain=[["is_vendor", "=", True]]),
    F.sel("state", "Status", [("draft", "RFQ"), ("sent", "RFQ Sent"), ("purchase", "Purchase Order"), ("cancel", "Cancelled")], readonly=True, default="draft", tracked=True),
    F.date("date_order", "Order Date", required=True, default=lambda env: date.today()),
    F.date("date_planned", "Expected Arrival", default=lambda env: date.today() + timedelta(days=7)),
    F.m2o("user_id", "Buyer", "res.users", default=lambda env: env.user.id or None, ondelete="SET NULL"),
    F.char("partner_ref", "Vendor Reference"), F.char("origin", "Source Document"), F.text("notes", "Notes"),
    F.o2m("order_line", "Products", "purchase.order.line", "order_id", columns=["product_id", "name", "product_qty", "qty_received", "qty_billed", "price_unit", "tax_id", "price_subtotal"]),
    F.money("amount_untaxed", "Untaxed Amount", readonly=True, default=0),
    F.money("amount_tax", "Taxes", readonly=True, default=0),
    F.money("amount_total", "Total", readonly=True, default=0),
    F.sel("bill_status", "Billing Status", [("no", "Nothing to Bill"), ("to_bill", "Waiting Bills"), ("billed", "Fully Billed")], readonly=True, default="no"),
], access=PURCHASE, mail=True, order="date_order DESC, id DESC", locked_when=[["state", "in", ["purchase", "cancel"]]], search_fields=["name", "partner_ref"], views={
    "list": list_view("name", "partner_id", "date_order", "date_planned", {"field": "amount_total", "sum": True}, {"field": "bill_status", "widget": "badge"}, {"field": "state", "widget": "badge"}),
    "form": form(status="state", printable="purchase", chatter=True, buttons=[
        btn("mark_sent", "Mark as Sent", when=[["state", "=", "draft"]]),
        btn("confirm", "Confirm Order", primary=True, when=[["state", "in", ["draft", "sent"]]]),
        btn("create_bill", "Create Bill", primary=True, when=[["state", "=", "purchase"], ["bill_status", "=", "to_bill"]]),
        btn("cancel", "Cancel", when=[["state", "!=", "cancel"]]),
    ], smart_buttons=[smart("Receipt", "truck", "stock.picking", [["purchase_id", "=", "$id"]]), smart("Bills", "file-text", "account.move", [["purchase_id", "=", "$id"]])],
        sections=[section(col("partner_id", "partner_ref", "origin"), col("date_order", "date_planned", "user_id", "bill_status"), tabs=[
            tab("Products", {"field": "order_line", "totals": ["amount_untaxed", "amount_tax", "amount_total"]}), tab("Notes", "notes")])]),
    "graph": graph("date_order:month", ["amount_total"]),
    "pivot": pivot("partner_id", "date_order:quarter", ["amount_total"]),
    "search": search(fields=["name", "partner_ref", "partner_id.name"], filters=[
        flt("rfq", "RFQs", [["state", "in", ["draft", "sent"]]]), flt("orders", "Purchase Orders", [["state", "=", "purchase"]]),
        flt("to_bill", "Waiting Bills", [["bill_status", "=", "to_bill"]]),
    ], group_by=["partner_id", "state", "date_order:month"]),
})

line = model("purchase.order.line", "Purchase Line", [
    F.m2o("order_id", "Order", "purchase.order", required=True, ondelete="CASCADE"),
    F.m2o("product_id", "Product", "product.product", required=True, domain=[["purchase_ok", "=", True]], onchange=True),
    F.char("name", "Description", required=True),
    F.float("product_qty", "Quantity", default=1, onchange=True),
    F.float("qty_received", "Received", readonly=True, default=0),
    F.float("qty_billed", "Billed", readonly=True, default=0),
    F.money("price_unit", "Unit Price", default=0, onchange=True),
    F.m2o("tax_id", "Taxes", "account.tax", domain=[["scope", "=", "purchase"]], onchange=True),
    F.money("price_subtotal", "Subtotal", readonly=True, default=0),
    F.money("price_tax", "Tax Amount", readonly=True, default=0),
], access={"purchase.user": "rwcd", "purchase.manager": "rwcd", "base.admin": "rwcd", "account.user": "r", "account.manager": "r", "stock.user": "r"}, parent="order_id", order="id ASC",
    constraints=[("qty_positive", "CHECK (product_qty >= 0)")])


@line.set_onchange
def _onchange(env, values, changed, parent=None):
    out = {}
    if changed == "product_id" and values.get("product_id"):
        p = env.sudo()["product.product"].get(_id(values["product_id"]))
        out = {"name": p["name"], "price_unit": float(p["standard_price"]), "tax_id": m2o_value(env, "account.tax", p["purchase_tax_id"])}
    merged = {**values, **out}
    subtotal, _ = line_amounts(env, merged.get("product_qty") or 0, merged.get("price_unit") or 0, _id(merged.get("tax_id")))
    out["price_subtotal"] = float(subtotal)
    return out


@line.hook("recompute")
def _recompute_line(env, ids):
    for rec in env["purchase.order.line"].read(ids, ["id", "product_qty", "price_unit", "tax_id"]):
        subtotal, tax_amount = line_amounts(env, rec["product_qty"], rec["price_unit"], rec["tax_id"])
        env.cur.execute("UPDATE purchase_order_line SET price_subtotal = %s, price_tax = %s WHERE id = %s", (subtotal, tax_amount, rec["id"]))


def _update_bill_status(env, order_id):
    state = env["purchase.order"].get(order_id, ["state"])["state"]
    status = "no"
    if state == "purchase":
        env.cur.execute(
            """SELECT l.product_qty, l.qty_received, l.qty_billed, p.product_type FROM purchase_order_line l
               JOIN product_product p ON p.id = l.product_id WHERE l.order_id = %s""", (order_id,))
        rows = env.cur.fetchall()
        # Vendor bills follow received quantities; services count as received once ordered.
        pending = any((q if t == "service" else r) > b for q, r, b, t in rows)
        status = "to_bill" if pending else ("billed" if rows and all(b >= q for q, _, b, _ in rows) else "no")
    env.cur.execute("UPDATE purchase_order SET bill_status = %s WHERE id = %s", (status, order_id))


@order.hook("recompute")
def _recompute(env, ids):
    for order_id in ids:
        env.cur.execute("SELECT COALESCE(SUM(price_subtotal), 0), COALESCE(SUM(price_tax), 0) FROM purchase_order_line WHERE order_id = %s", (order_id,))
        untaxed, tax_amount = env.cur.fetchone()
        env.cur.execute("UPDATE purchase_order SET amount_untaxed = %s, amount_tax = %s, amount_total = %s WHERE id = %s", (untaxed, tax_amount, untaxed + tax_amount, order_id))
        _update_bill_status(env, order_id)


@order.action("mark_sent", "Mark as Sent", groups=["purchase.user", "purchase.manager"])
def action_sent(env, ids, params):
    env.sudo()["purchase.order"].write(ids, {"state": "sent"})


def confirm_order(env, order_id: int):
    env = env.sudo()
    o = env["purchase.order"].get(order_id)
    if o["state"] not in ("draft", "sent"):
        raise UserError("Only RFQs can be confirmed.")
    env.cur.execute("SELECT id, product_id, name, product_qty FROM purchase_order_line WHERE order_id = %s ORDER BY id", (order_id,))
    lines = env.cur.fetchall()
    if not lines:
        raise UserError("Add at least one product before confirming.")
    env["purchase.order"].write([order_id], {"state": "purchase"})
    receivable = [row for row in lines if env["product.product"].get(row[1], ["product_type"])["product_type"] != "service"]
    if receivable:
        pick = env["stock.picking"].create({
            "kind": "incoming", "partner_id": o["partner_id"], "origin": o["name"], "purchase_id": order_id,
            "location_id": location_by_usage(env, "supplier"), "location_dest_id": main_stock(env), "state": "assigned", "scheduled_date": o["date_planned"],
        })
        for line_id, product_id, name, qty in receivable:
            env["stock.move"].create({"picking_id": pick, "product_id": product_id, "name": name, "quantity": qty, "purchase_line_id": line_id})
    _update_bill_status(env, order_id)


@order.action("confirm", "Confirm Order", groups=["purchase.user", "purchase.manager"])
def action_confirm(env, ids, params):
    for order_id in ids:
        confirm_order(env, order_id)


def update_received(env, order_id: int):
    env = env.sudo()
    env.cur.execute(
        """UPDATE purchase_order_line l SET qty_received = COALESCE((
             SELECT SUM(m.quantity_done) FROM stock_move m WHERE m.purchase_line_id = l.id AND m.state = 'done'), 0)
           WHERE l.order_id = %s""", (order_id,))
    _update_bill_status(env, order_id)


def create_bill(env, order_id: int) -> int:
    env = env.sudo()
    o = env["purchase.order"].get(order_id)
    if o["state"] != "purchase":
        raise UserError("Confirm the order before billing it.")
    env.cur.execute(
        """SELECT l.id, l.product_id, l.name, l.product_qty, l.qty_received, l.qty_billed, l.price_unit, l.tax_id, p.product_type, p.expense_account_id
           FROM purchase_order_line l JOIN product_product p ON p.id = l.product_id WHERE l.order_id = %s ORDER BY l.id""", (order_id,))
    rows = env.cur.fetchall()
    todo = []
    for lid, pid, name, qty, received, billed, price, tax_id, ptype, account_id in rows:
        open_qty = dec(qty if ptype == "service" else received) - dec(billed)
        if open_qty > 0:
            todo.append((lid, pid, name, open_qty, price, tax_id, account_id))
    if not todo:
        raise UserError("There is nothing to bill yet. Receive the products first.")
    move_id = env["account.move"].create({
        "move_type": "in_invoice", "partner_id": o["partner_id"], "ref": o["partner_ref"] or o["name"], "date": date.today(),
        "invoice_date_due": date.today() + timedelta(days=30), "purchase_id": order_id,
    })
    for lid, pid, name, qty, price, tax_id, account_id in todo:
        env["account.invoice.line"].create({"move_id": move_id, "product_id": pid, "name": name, "quantity": qty, "price_unit": price, "tax_id": tax_id, "account_id": account_id, "purchase_line_id": lid})
    env.cur.execute(
        """UPDATE purchase_order_line l SET qty_billed = COALESCE((
             SELECT SUM(CASE WHEN m.move_type = 'in_refund' THEN -il.quantity ELSE il.quantity END)
             FROM account_invoice_line il JOIN account_move m ON m.id = il.move_id
             WHERE il.purchase_line_id = l.id AND m.state != 'cancel'), 0) WHERE l.order_id = %s""", (order_id,))
    _update_bill_status(env, order_id)
    return move_id


@order.action("create_bill", "Create Bill", groups=["purchase.user", "purchase.manager"])
def action_bill(env, ids, params):
    return {"type": "open", "model": "account.move", "id": create_bill(env, ids[0])}


@order.action("cancel", "Cancel", groups=["purchase.user", "purchase.manager"])
def action_cancel(env, ids, params):
    env = env.sudo()
    for order_id in ids:
        env.cur.execute("SELECT COUNT(*) FROM stock_picking WHERE purchase_id = %s AND state = 'done'", (order_id,))
        if env.cur.fetchone()[0]:
            raise UserError("Part of this order has been received, so it cannot be cancelled.")
        env.cur.execute("SELECT COUNT(*) FROM account_move WHERE purchase_id = %s AND state = 'posted'", (order_id,))
        if env.cur.fetchone()[0]:
            raise UserError("This order has posted bills.")
        env.cur.execute("SELECT id FROM stock_picking WHERE purchase_id = %s AND state NOT IN ('done', 'cancel')", (order_id,))
        open_pickings = [r[0] for r in env.cur.fetchall()]
        if open_pickings:
            env["stock.picking"].run_action("cancel", open_pickings)
        env["purchase.order"].write([order_id], {"state": "cancel"})
        _update_bill_status(env, order_id)


app("purchase", "Purchase", "credit-card", "#6a5a9e", [
    menu("rfq", "Requests for Quotation", "purchase.order", ("list", "form", "graph", "pivot"), [["state", "in", ["draft", "sent", "cancel"]]]),
    menu("orders", "Purchase Orders", "purchase.order", ("list", "form", "graph", "pivot"), [["state", "=", "purchase"]]),
    menu("vendors", "Vendors", "res.partner", ("kanban", "list", "form"), [["is_vendor", "=", True]], {"default_is_vendor": True, "default_is_customer": False}),
    menu("products", "Products", "product.product", ("kanban", "list", "form"), [["purchase_ok", "=", True]]),
    menu("reporting", "Reporting", "purchase.order", ("graph", "pivot", "list", "form"), [["state", "=", "purchase"]]),
], groups=["purchase.user", "purchase.manager"], sequence=50)
