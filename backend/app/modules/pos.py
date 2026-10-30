"""Point of sale. Each sale moves stock immediately; closing a session posts one summary journal
entry. Prices always come from the server, never from the till."""
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal

from ..orm import F, UserError, app, dec, menu, model, money
from ..views import btn, col, flt, form, graph, kanban, list_view, pivot, search, section, smart, tab
from .account import get_account, journal_of, line_amounts, post_move
from .stock import apply_move, location_by_usage, main_stock

CASHIER = ["pos.user", "pos.manager"]
config_m = model("pos.config", "Shop", [
    F.char("name", "Shop Name", required=True), F.m2o("cash_journal_id", "Cash Journal", "account.journal", required=True, domain=[["kind", "=", "cash"]]),
    F.m2o("card_journal_id", "Card Journal", "account.journal", required=True, domain=[["kind", "=", "bank"]]), F.bool("active", "Active", default=True),
], access={"pos.user": "r", "pos.manager": "rwcd", "base.admin": "rwcd"}, order="name ASC", views={
    "list": list_view("name", "cash_journal_id", "card_journal_id"), "form": form(sections=[section(col("cash_journal_id", "card_journal_id"))]),
})
session = model("pos.session", "Session", [
    F.char("name", "Session", readonly=True), F.m2o("config_id", "Shop", "pos.config", required=True), F.m2o("user_id", "Opened by", "res.users", readonly=True),
    F.sel("state", "Status", [("opened", "In Progress"), ("closed", "Closed")], readonly=True, default="opened"),
    F.datetime("start_at", "Opened", readonly=True), F.datetime("stop_at", "Closed", readonly=True),
    F.money("cash_register_start", "Opening Cash", default=0), F.m2o("move_id", "Journal Entry", "account.move", readonly=True, ondelete="SET NULL"),
    F.sql("total_sales", "Total Sales", "money", "SELECT COALESCE(SUM(o.amount_total), 0) FROM pos_order o WHERE o.session_id = {t}.id"),
    F.sql("order_count", "Orders", "int", "SELECT COUNT(*) FROM pos_order o WHERE o.session_id = {t}.id"),
], access={"pos.user": "rw", "pos.manager": "rwcd", "base.admin": "rwcd"}, order="id DESC", locked_when=[["state", "=", "closed"]], views={
    "list": list_view("name", "config_id", "user_id", "start_at", "stop_at", "order_count", {"field": "total_sales", "sum": True}, {"field": "state", "widget": "badge"}),
    "form": form(status="state", buttons=[btn("close", "Close Session & Post", primary=True, when=[["state", "=", "opened"]])], smart_buttons=[smart("Orders", "shopping-bag", "pos.order", [["session_id", "=", "$id"]])],
                 sections=[section(col("config_id", "user_id", "cash_register_start"), col("start_at", "stop_at", "total_sales", "order_count", "move_id"))]),
})
order = model("pos.order", "POS Order", [
    F.char("name", "Receipt", readonly=True, sequence="pos.order"), F.m2o("session_id", "Session", "pos.session", required=True, readonly=True),
    F.m2o("partner_id", "Customer", "res.partner", readonly=True, ondelete="SET NULL"), F.datetime("date_order", "Date", readonly=True), F.m2o("user_id", "Cashier", "res.users", readonly=True),
    F.sel("payment_method", "Payment", [("cash", "Cash"), ("card", "Card")], readonly=True),
    F.money("amount_untaxed", "Untaxed", readonly=True), F.money("amount_tax", "Tax", readonly=True), F.money("amount_total", "Total", readonly=True),
    F.money("amount_paid", "Tendered", readonly=True), F.money("amount_return", "Change", readonly=True), F.m2o("refund_of_id", "Refund of", "pos.order", readonly=True, ondelete="SET NULL"),
    F.o2m("line_ids", "Order Lines", "pos.order.line", "order_id", columns=["product_id", "qty", "price_unit", "discount", "price_subtotal", "price_tax"]),
], access={"pos.user": "r", "pos.manager": "r", "base.admin": "r"}, order="id DESC", views={
    "list": list_view("name", "date_order", "user_id", "partner_id", "payment_method", {"field": "amount_total", "sum": True}),
    "form": form(buttons=[btn("refund", "Refund")], sections=[section(col("session_id", "date_order", "user_id", "partner_id", "refund_of_id"), col("payment_method", "amount_untaxed", "amount_tax", "amount_total", "amount_paid", "amount_return"), tabs=[tab("Order Lines", {"field": "line_ids", "readonly": [["id", ">", 0]]})])]),
    "graph": graph("date_order:day", ["amount_total"]), "pivot": pivot("session_id", "payment_method", ["amount_total"]),
    "search": search(fields=["name", "partner_id.name"], group_by=["session_id", "payment_method", "user_id", "date_order:day"]),
})
line = model("pos.order.line", "POS Line", [
    F.m2o("order_id", "Order", "pos.order", required=True, readonly=True, ondelete="CASCADE"), F.m2o("product_id", "Product", "product.product", required=True, readonly=True),
    F.char("name", "Description", readonly=True), F.float("qty", "Qty", readonly=True), F.money("price_unit", "Unit Price", readonly=True), F.float("discount", "Disc.%", readonly=True),
    F.m2o("tax_id", "Tax", "account.tax", readonly=True), F.money("price_subtotal", "Subtotal", readonly=True), F.money("price_tax", "Tax Amount", readonly=True),
    F.m2o("income_account_id", "Income Account", "account.account", readonly=True),
], access={"pos.user": "r", "pos.manager": "r", "base.admin": "r"}, order="id ASC")


def open_session(env, config_id: int, opening_cash=0) -> int:
    su = env.sudo()
    su["pos.config"].get(config_id, ["id"])
    env.cur.execute("SELECT pg_advisory_xact_lock(727500 + %s)", (config_id,))
    existing = su["pos.session"].search([["config_id", "=", config_id], ["state", "=", "opened"]], limit=1)
    if existing:
        return existing[0]
    return su["pos.session"].create({"config_id": config_id, "user_id": env.user.id or None, "start_at": datetime.now(timezone.utc), "cash_register_start": opening_cash,
                                     "name": env.next_sequence("pos.session", "POS/SESSION/")})


def create_order(env, session_id: int, lines: list[dict], payment_method: str, amount_paid=None, partner_id=None, refund_of=None) -> int:
    su = env.sudo()
    s = su["pos.session"].get(session_id, ["state", "config_id"])
    if s["state"] != "opened":
        raise UserError("This session is closed.")
    if payment_method not in ("cash", "card"):
        raise UserError("Choose a payment method.")
    if not lines:
        raise UserError("The order is empty.")
    prepared, untaxed, tax_total = [], Decimal(0), Decimal(0)
    for raw in lines:
        try:
            product_id, qty = int(raw["product_id"]), dec(raw["qty"])
            discount = dec(raw.get("discount", 0))
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            raise UserError("Invalid order line.") from exc
        if qty == 0 or (qty < 0 and not refund_of):
            raise UserError("Quantities must be positive.")
        if not 0 <= discount <= 100:
            raise UserError("Discounts must be between 0 and 100.")
        p = su["product.product"].get(product_id)
        if not p["pos_ok"] and not refund_of:
            raise UserError(f"{p['name']} is not available in the point of sale.")
        price = raw["price_unit"] if refund_of else p["list_price"]
        net_price = dec(price) * (1 - discount / 100)
        tax_id = raw["tax_id"] if refund_of else p["sale_tax_id"]
        subtotal, tax_amount = line_amounts(su, qty, net_price, tax_id)
        untaxed += subtotal
        tax_total += tax_amount
        prepared.append({"product_id": product_id, "name": p["name"], "qty": qty, "price_unit": price, "discount": discount, "tax_id": tax_id, "price_subtotal": subtotal,
                         "price_tax": tax_amount, "income_account_id": p["income_account_id"]})
    total = untaxed + tax_total
    paid = money(amount_paid) if amount_paid is not None and payment_method == "cash" and total > 0 else total
    if paid < total:
        raise UserError("The amount tendered is less than the total.")
    order_id = su["pos.order"].create({
        "session_id": session_id, "partner_id": partner_id, "date_order": datetime.now(timezone.utc), "user_id": env.user.id or None, "payment_method": payment_method,
        "amount_untaxed": untaxed, "amount_tax": tax_total, "amount_total": total, "amount_paid": paid, "amount_return": paid - total, "refund_of_id": refund_of,
    })
    receipt = su["pos.order"].get(order_id, ["name"])["name"]
    customer = location_by_usage(su, "customer")
    stock = main_stock(su)
    for vals in prepared:
        su["pos.order.line"].create({"order_id": order_id, **vals})
        if vals["qty"] > 0:
            apply_move(su, vals["product_id"], stock, customer, vals["qty"], reference=receipt)
        else:
            apply_move(su, vals["product_id"], customer, stock, -vals["qty"], reference=receipt)
    return order_id


@order.action("refund", "Refund", groups=CASHIER, confirm="Return every item on this receipt?")
def action_refund(env, ids, params):
    su = env.sudo()
    o = su["pos.order"].get(ids[0])
    if o["refund_of_id"]:
        raise UserError("A refund cannot be refunded.")
    if su["pos.order"].search([["refund_of_id", "=", ids[0]]], limit=1):
        raise UserError("This receipt has already been refunded.")
    s = su["pos.session"].get(o["session_id"], ["config_id"])
    current = su["pos.session"].search([["config_id", "=", s["config_id"]], ["state", "=", "opened"]], limit=1)
    if not current:
        raise UserError("Open a session for this shop to process the refund.")
    su.cur.execute("SELECT product_id, qty, price_unit, discount, tax_id FROM pos_order_line WHERE order_id = %s", (ids[0],))
    lines = [{"product_id": p, "qty": -q, "price_unit": pu, "discount": d, "tax_id": t} for p, q, pu, d, t in su.cur.fetchall()]
    new_id = create_order(env, current[0], lines, o["payment_method"], refund_of=ids[0])
    return {"type": "open", "model": "pos.order", "id": new_id}


def close_session(env, session_id: int):
    su = env.sudo()
    s = su["pos.session"].get(session_id)
    if s["state"] != "opened":
        raise UserError("This session is already closed.")
    env.cur.execute("SELECT id FROM pos_session WHERE id = %s FOR UPDATE", (session_id,))
    conf = su["pos.config"].get(s["config_id"])
    env.cur.execute("SELECT payment_method, COALESCE(SUM(amount_total), 0) FROM pos_order WHERE session_id = %s GROUP BY 1", (session_id,))
    payments = dict(env.cur.fetchall())
    env.cur.execute("""SELECT l.income_account_id, l.tax_id, SUM(l.price_subtotal), SUM(l.price_tax) FROM pos_order_line l JOIN pos_order o ON o.id = l.order_id
                       WHERE o.session_id = %s GROUP BY 1, 2""", (session_id,))
    rows = env.cur.fetchall()
    move_id = None
    if rows:
        default_income = get_account(su, "400000")
        income, taxes = defaultdict(Decimal), defaultdict(Decimal)
        for account_id, tax_id, subtotal, tax_amount in rows:
            income[account_id or default_income] += subtotal
            if tax_id:
                taxes[tax_id] += tax_amount
        move_id = su["account.move"].create({"move_type": "entry", "date": date.today(), "ref": s["name"], "journal_id": journal_of(su, "general")})

        def add(account_id, amount, label, tax_id=None):
            # Positive amounts are credits here, negative (net refunds) are debits.
            if not amount:
                return
            credit, debit = (amount, 0) if amount > 0 else (0, -amount)
            su["account.move.line"].create({"move_id": move_id, "account_id": account_id, "debit": debit, "credit": credit, "name": label})
            if tax_id:
                env.cur.execute("UPDATE account_move_line SET tax_id = %s WHERE id = (SELECT MAX(id) FROM account_move_line WHERE move_id = %s)", (tax_id, move_id))

        for account_id, amount in income.items():
            add(account_id, amount, f"{s['name']} sales")
        for tax_id, amount in taxes.items():
            t = su["account.tax"].get(tax_id, ["account_id", "name"])
            add(t["account_id"], amount, t["name"], tax_id)
        journals = {"cash": conf["cash_journal_id"], "card": conf["card_journal_id"]}
        for method, amount in payments.items():
            account = su["account.journal"].get(journals[method], ["default_account_id"])["default_account_id"]
            add(account, -amount, f"{s['name']} {method}")
        post_move(su, move_id)
    su["pos.session"].write([session_id], {"state": "closed", "stop_at": datetime.now(timezone.utc), "move_id": move_id})


@session.action("close", "Close Session & Post", groups=CASHIER, confirm="Close this session and post its journal entry?")
def action_close(env, ids, params):
    close_session(env, ids[0])


app("pos", "Point of Sale", "store", "#bc6c25", [
    menu("till", "Till", client="pos"),
    menu("orders", "Orders", "pos.order", ("list", "form", "graph", "pivot")),
    menu("sessions", "Sessions", "pos.session"),
    menu("config", "Configuration", groups=["pos.manager"], children=[menu("shops", "Shops", "pos.config")]),
], groups=CASHIER, sequence=45)
