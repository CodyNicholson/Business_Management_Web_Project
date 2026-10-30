"""Quotations and sales orders: delivery and invoicing flow from the confirmed order."""
from datetime import date, timedelta
from decimal import Decimal

from ..orm import F, UserError, app, dec, menu, model, money, post_note
from ..views import btn, col, flt, form, graph, kanban, list_view, pivot, search, section, smart, tab
from .account import _id, line_amounts, m2o_value, post_move
from .stock import location_by_usage, main_stock

SALES = {"sales.user": "rwc", "sales.manager": "rwcd", "base.admin": "rwcd", "account.user": "r", "account.manager": "r"}
STATES = [("draft", "Quotation"), ("sent", "Quotation Sent"), ("sale", "Sales Order"), ("cancel", "Cancelled")]

order = model("sale.order", "Sales Order", [
    F.char("name", "Order Number", readonly=True, sequence="sale.order"),
    F.m2o("partner_id", "Customer", "res.partner", required=True, tracked=True, domain=[["is_customer", "=", True]], onchange=True),
    F.sel("state", "Status", STATES, readonly=True, default="draft", tracked=True),
    F.date("date_order", "Order Date", required=True, default=lambda env: date.today()),
    F.date("validity_date", "Expiration", default=lambda env: date.today() + timedelta(days=30)),
    F.m2o("payment_term_id", "Payment Terms", "account.payment.term", tracked=True),
    F.m2o("user_id", "Salesperson", "res.users", default=lambda env: env.user.id or None, tracked=True, ondelete="SET NULL"),
    F.m2o("opportunity_id", "Opportunity", "crm.lead", ondelete="SET NULL"),
    F.char("client_order_ref", "Customer Reference"), F.text("note", "Terms and Conditions"),
    F.o2m("order_line", "Order Lines", "sale.order.line", "order_id",
          columns=["product_id", "name", "product_uom_qty", "qty_delivered", "qty_invoiced", "price_unit", "discount", "tax_id", "price_subtotal"]),
    F.money("amount_untaxed", "Untaxed Amount", readonly=True, default=0),
    F.money("amount_tax", "Taxes", readonly=True, default=0),
    F.money("amount_total", "Total", readonly=True, default=0),
    F.sel("invoice_status", "Invoice Status", [("no", "Nothing to Invoice"), ("to_invoice", "To Invoice"), ("invoiced", "Fully Invoiced")], readonly=True, default="no"),
], access=SALES, mail=True, order="date_order DESC, id DESC", locked_when=[["state", "in", ["sale", "cancel"]]], search_fields=["name", "client_order_ref"], views={
    "kanban": kanban(group_by="state", title="partner_id", subtitle="name", amount="amount_total", date="date_order", user="user_id"),
    "list": list_view("name", "date_order", "partner_id", "user_id", {"field": "amount_total", "sum": True}, {"field": "invoice_status", "widget": "badge"}, {"field": "state", "widget": "badge"}),
    "form": form(status="state", printable="sale", chatter=True, buttons=[
        btn("mark_sent", "Mark as Sent", when=[["state", "=", "draft"]]),
        btn("confirm", "Confirm", primary=True, when=[["state", "in", ["draft", "sent"]]]),
        btn("create_invoice", "Create Invoice", primary=True, when=[["state", "=", "sale"], ["invoice_status", "=", "to_invoice"]]),
        btn("cancel", "Cancel", when=[["state", "!=", "cancel"]]),
        btn("to_draft", "Set to Quotation", when=[["state", "=", "cancel"]]),
    ], smart_buttons=[
        smart("Delivery", "truck", "stock.picking", [["sale_id", "=", "$id"]]),
        smart("Invoices", "file-text", "account.move", [["sale_id", "=", "$id"]]),
    ], sections=[section(col("partner_id", "client_order_ref", "payment_term_id"), col("date_order", "validity_date", "user_id", "invoice_status"), tabs=[
        tab("Order Lines", {"field": "order_line", "totals": ["amount_untaxed", "amount_tax", "amount_total"]}), tab("Other Info", "opportunity_id", "note")])]),
    "graph": graph("date_order:month", ["amount_total"]),
    "pivot": pivot("partner_id", "date_order:quarter", ["amount_untaxed", "amount_total"]),
    "search": search(fields=["name", "client_order_ref", "partner_id.name"], filters=[
        flt("my", "My Orders", [["user_id", "=", "$uid"]]), flt("quotes", "Quotations", [["state", "in", ["draft", "sent"]]]),
        flt("orders", "Sales Orders", [["state", "=", "sale"]]), flt("to_invoice", "To Invoice", [["invoice_status", "=", "to_invoice"]]),
    ], group_by=["partner_id", "user_id", "state", "date_order:month"]),
})

line = model("sale.order.line", "Order Line", [
    F.m2o("order_id", "Order", "sale.order", required=True, ondelete="CASCADE"),
    F.m2o("product_id", "Product", "product.product", required=True, domain=[["sale_ok", "=", True]], onchange=True),
    F.char("name", "Description", required=True),
    F.float("product_uom_qty", "Quantity", default=1, onchange=True),
    F.float("qty_delivered", "Delivered", readonly=True, default=0),
    F.float("qty_invoiced", "Invoiced", readonly=True, default=0),
    F.money("price_unit", "Unit Price", default=0, onchange=True),
    F.float("discount", "Disc.%", default=0, onchange=True),
    F.m2o("tax_id", "Taxes", "account.tax", domain=[["scope", "=", "sale"]], onchange=True),
    F.money("price_subtotal", "Subtotal", readonly=True, default=0),
    F.money("price_tax", "Tax Amount", readonly=True, default=0),
], access={"sales.user": "rwcd", "sales.manager": "rwcd", "base.admin": "rwcd", "account.user": "r", "account.manager": "r"}, parent="order_id", order="id ASC",
    constraints=[("qty_positive", "CHECK (product_uom_qty >= 0)"), ("discount_range", "CHECK (discount >= 0 AND discount <= 100)")])


def _discounted(qty, price, discount):
    return dec(price) * (1 - dec(discount) / 100), dec(qty)


@line.set_onchange
def _line_onchange(env, values, changed, parent=None):
    out = {}
    if changed == "product_id" and values.get("product_id"):
        p = env.sudo()["product.product"].get(_id(values["product_id"]))
        out = {"name": p["name"], "price_unit": float(p["list_price"]), "tax_id": m2o_value(env, "account.tax", p["sale_tax_id"])}
    merged = {**values, **out}
    price, qty = _discounted(merged.get("product_uom_qty"), merged.get("price_unit"), merged.get("discount"))
    subtotal, _ = line_amounts(env, qty, price, _id(merged.get("tax_id")))
    out["price_subtotal"] = float(subtotal)
    return out


@line.hook("recompute")
def _recompute_line(env, ids):
    for rec in env["sale.order.line"].read(ids, ["id", "product_uom_qty", "price_unit", "discount", "tax_id"]):
        price, qty = _discounted(rec["product_uom_qty"], rec["price_unit"], rec["discount"])
        subtotal, tax_amount = line_amounts(env, qty, price, rec["tax_id"])
        env.cur.execute("UPDATE sale_order_line SET price_subtotal = %s, price_tax = %s WHERE id = %s", (subtotal, tax_amount, rec["id"]))


def _to_invoice(rec, product) -> Decimal:
    target = rec["qty_delivered"] if product["invoice_policy"] == "delivery" and product["product_type"] != "service" else rec["product_uom_qty"]
    return dec(target) - dec(rec["qty_invoiced"])


@order.hook("recompute")
def _recompute_order(env, ids):
    for order_id in ids:
        env.cur.execute("SELECT COALESCE(SUM(price_subtotal), 0), COALESCE(SUM(price_tax), 0) FROM sale_order_line WHERE order_id = %s", (order_id,))
        untaxed, tax_amount = env.cur.fetchone()
        env.cur.execute("UPDATE sale_order SET amount_untaxed = %s, amount_tax = %s, amount_total = %s WHERE id = %s", (untaxed, tax_amount, untaxed + tax_amount, order_id))
        _update_invoice_status(env, order_id)


def _update_invoice_status(env, order_id):
    state = env["sale.order"].get(order_id, ["state"])["state"]
    status = "no"
    if state == "sale":
        env.cur.execute("SELECT id FROM sale_order_line WHERE order_id = %s", (order_id,))
        line_ids = [r[0] for r in env.cur.fetchall()]
        lines = env["sale.order.line"].read(line_ids, ["id", "product_id", "product_uom_qty", "qty_delivered", "qty_invoiced"])
        pending = False
        for rec in lines:
            prod = env["product.product"].get(rec["product_id"], ["invoice_policy", "product_type"])
            if _to_invoice(rec, prod) > 0:
                pending = True
        fully = all(dec(r["qty_invoiced"]) >= dec(r["product_uom_qty"]) for r in lines) and lines
        status = "to_invoice" if pending else ("invoiced" if fully else "no")
    env.cur.execute("UPDATE sale_order SET invoice_status = %s WHERE id = %s", (status, order_id))


@order.action("mark_sent", "Mark as Sent", groups=["sales.user", "sales.manager"])
def action_sent(env, ids, params):
    env.sudo()["sale.order"].write(ids, {"state": "sent"})


def confirm_order(env, order_id: int):
    env = env.sudo()
    o = env["sale.order"].get(order_id)
    if o["state"] not in ("draft", "sent"):
        raise UserError("Only quotations can be confirmed.")
    env.cur.execute("SELECT id, product_id, name, product_uom_qty FROM sale_order_line WHERE order_id = %s ORDER BY id", (order_id,))
    lines = env.cur.fetchall()
    if not lines:
        raise UserError("Add at least one product before confirming.")
    env["sale.order"].write([order_id], {"state": "sale"})
    deliverable = []
    for line_id, product_id, name, qty in lines:
        if env["product.product"].get(product_id, ["product_type"])["product_type"] != "service":
            deliverable.append((line_id, product_id, name, qty))
    if deliverable:
        pick = env["stock.picking"].create({
            "kind": "outgoing", "partner_id": o["partner_id"], "origin": o["name"], "sale_id": order_id,
            "location_id": main_stock(env), "location_dest_id": location_by_usage(env, "customer"), "state": "assigned",
        })
        for line_id, product_id, name, qty in deliverable:
            env["stock.move"].create({"picking_id": pick, "product_id": product_id, "name": name, "quantity": qty, "sale_line_id": line_id})
    env["sale.order"].m.hooks["recompute"](env, [order_id])
    if o.get("opportunity_id"):
        post_note(env, "crm.lead", o["opportunity_id"], f"Quotation {o['name']} was confirmed.")


@order.action("confirm", "Confirm", groups=["sales.user", "sales.manager"])
def action_confirm(env, ids, params):
    for order_id in ids:
        confirm_order(env, order_id)


def update_delivered(env, order_id: int):
    env = env.sudo()
    env.cur.execute(
        """UPDATE sale_order_line l SET qty_delivered = COALESCE((
             SELECT SUM(m.quantity_done) FROM stock_move m WHERE m.sale_line_id = l.id AND m.state = 'done'), 0)
           WHERE l.order_id = %s""", (order_id,))
    _update_invoice_status(env, order_id)


def update_invoiced(env, order_id: int):
    env.cur.execute(
        """UPDATE sale_order_line l SET qty_invoiced = COALESCE((
             SELECT SUM(CASE WHEN m.move_type = 'out_refund' THEN -il.quantity ELSE il.quantity END)
             FROM account_invoice_line il JOIN account_move m ON m.id = il.move_id
             WHERE il.sale_line_id = l.id AND m.state != 'cancel'), 0)
           WHERE l.order_id = %s""", (order_id,))
    _update_invoice_status(env, order_id)


def create_invoice(env, order_id: int) -> int:
    env = env.sudo()
    o = env["sale.order"].get(order_id)
    if o["state"] != "sale":
        raise UserError("Confirm the order before invoicing it.")
    env.cur.execute("SELECT id FROM sale_order_line WHERE order_id = %s ORDER BY id", (order_id,))
    lines = env["sale.order.line"].read([r[0] for r in env.cur.fetchall()])
    todo = []
    for rec in lines:
        prod = env["product.product"].get(rec["product_id"], ["invoice_policy", "product_type", "income_account_id"])
        qty = _to_invoice(rec, prod)
        if qty > 0:
            todo.append((rec, prod, qty))
    if not todo:
        raise UserError("There is nothing to invoice yet. Products invoiced on delivery must be delivered first.")
    days = 0
    if o["payment_term_id"]:
        days = env["account.payment.term"].get(o["payment_term_id"], ["days"])["days"] or 0
    move_id = env["account.move"].create({
        "move_type": "out_invoice", "partner_id": o["partner_id"], "ref": o["client_order_ref"] or o["name"], "date": date.today(),
        "invoice_date_due": date.today() + timedelta(days=days), "payment_term_id": o["payment_term_id"], "sale_id": order_id, "invoice_user_id": o["user_id"],
    })
    for rec, prod, qty in todo:
        price, _ = _discounted(qty, rec["price_unit"], rec["discount"])
        env["account.invoice.line"].create({
            "move_id": move_id, "product_id": rec["product_id"], "name": rec["name"], "quantity": qty, "price_unit": money(price),
            "tax_id": rec["tax_id"], "account_id": prod["income_account_id"], "sale_line_id": rec["id"],
        })
    update_invoiced(env, order_id)
    return move_id


@order.action("create_invoice", "Create Invoice", groups=["sales.user", "sales.manager"])
def action_create_invoice(env, ids, params):
    move_id = create_invoice(env, ids[0])
    return {"type": "open", "model": "account.move", "id": move_id}


@order.action("cancel", "Cancel", groups=["sales.user", "sales.manager"])
def action_cancel(env, ids, params):
    env = env.sudo()
    for order_id in ids:
        env.cur.execute("SELECT COUNT(*) FROM stock_picking WHERE sale_id = %s AND state = 'done'", (order_id,))
        if env.cur.fetchone()[0]:
            raise UserError("Part of this order has been delivered, so it cannot be cancelled.")
        env.cur.execute("SELECT COUNT(*) FROM account_move WHERE sale_id = %s AND state = 'posted'", (order_id,))
        if env.cur.fetchone()[0]:
            raise UserError("This order has posted invoices. Issue a credit note first.")
        env.cur.execute("SELECT id FROM stock_picking WHERE sale_id = %s AND state NOT IN ('done', 'cancel')", (order_id,))
        open_pickings = [r[0] for r in env.cur.fetchall()]
        if open_pickings:
            env["stock.picking"].run_action("cancel", open_pickings)
        env["sale.order"].write([order_id], {"state": "cancel"})
        env["sale.order"].m.hooks["recompute"](env, [order_id])


@order.action("to_draft", "Set to Quotation", groups=["sales.user", "sales.manager"])
def action_to_draft(env, ids, params):
    env.sudo()["sale.order"].write(ids, {"state": "draft"})


app("sales", "Sales", "shopping-cart", "#a2415d", [
    menu("orders", "Orders", children=[
        menu("quotations", "Quotations", "sale.order", ("list", "kanban", "form", "graph", "pivot"), [["state", "in", ["draft", "sent", "cancel"]]]),
        menu("orders", "Sales Orders", "sale.order", ("list", "kanban", "form", "graph", "pivot"), [["state", "=", "sale"]]),
        menu("customers", "Customers", "res.partner", ("kanban", "list", "form"), [["is_customer", "=", True]], {"default_is_customer": True}),
    ]),
    menu("to_invoice", "To Invoice", "sale.order", ("list", "form"), [["invoice_status", "=", "to_invoice"]]),
    menu("products", "Products", "product.product", ("kanban", "list", "form"), [["sale_ok", "=", True]]),
    menu("reporting", "Reporting", "sale.order", ("graph", "pivot", "list", "form"), [["state", "=", "sale"]]),
], groups=["sales.user", "sales.manager"], sequence=40)
