"""Invoicing and double-entry accounting.

Draft documents hold invoice lines; posting generates balanced journal items. Posted journal items
are never edited: a document is either reset to draft (which removes them, while unpaid) or reversed.
"""
from datetime import date
from decimal import Decimal

from ..orm import F, UserError, app, dec, menu, model, money, post_note
from ..views import btn, col, flt, form, graph, list_view, pivot, search, section, smart, tab

BILLING = {"account.user": "rwc", "account.manager": "rwcd", "base.admin": "rwcd"}
READ_BILLING = {"account.user": "r", "account.manager": "rwcd", "base.admin": "rwcd"}

ACCOUNT_TYPES = [
    ("receivable", "Receivable"), ("payable", "Payable"), ("bank", "Bank and Cash"), ("current_asset", "Current Assets"),
    ("fixed_asset", "Fixed Assets"), ("current_liability", "Current Liabilities"), ("equity", "Equity"),
    ("income", "Income"), ("other_income", "Other Income"), ("expense", "Expenses"), ("cogs", "Cost of Revenue"),
]
ASSET_TYPES = ("receivable", "bank", "current_asset", "fixed_asset")
LIABILITY_TYPES = ("payable", "current_liability")
INCOME_TYPES = ("income", "other_income")
EXPENSE_TYPES = ("expense", "cogs")

account = model("account.account", "Account", [
    F.char("code", "Code", required=True), F.char("name", "Account Name", required=True),
    F.sel("account_type", "Type", ACCOUNT_TYPES, required=True),
    F.bool("active", "Active", default=True),
    F.sql("balance", "Balance", "money",
          "SELECT COALESCE(SUM(l.debit - l.credit), 0) FROM account_move_line l WHERE l.account_id = {t}.id AND l.parent_state = 'posted'"),
], access={"base.user": "r", "account.manager": "rwcd", "base.admin": "rwcd"}, order="code ASC",
    search_fields=["code", "name"], constraints=[("code_uniq", "UNIQUE (code)")], views={
    "list": list_view("code", "name", "account_type", "balance"),
    "form": form(sections=[section(col("code", "account_type", "balance"))]),
    "search": search(fields=["code", "name"], group_by=["account_type"]),
})

journal = model("account.journal", "Journal", [
    F.char("name", "Journal Name", required=True), F.char("code", "Short Code", required=True),
    F.sel("kind", "Type", [("sale", "Sales"), ("purchase", "Purchase"), ("bank", "Bank"), ("cash", "Cash"), ("general", "Miscellaneous")], required=True),
    F.m2o("default_account_id", "Default Account", "account.account"),
], access={"base.user": "r", "account.manager": "rwcd", "base.admin": "rwcd"}, order="code ASC",
    constraints=[("code_uniq", "UNIQUE (code)")], views={
    "list": list_view("name", "code", "kind", "default_account_id"),
    "form": form(sections=[section(col("code", "kind", "default_account_id"))]),
})

tax = model("account.tax", "Tax", [
    F.char("name", "Tax Name", required=True), F.float("rate", "Rate (%)", required=True),
    F.sel("scope", "Applies to", [("sale", "Sales"), ("purchase", "Purchases")], required=True, default="sale"),
    F.m2o("account_id", "Tax Account", "account.account", required=True),
    F.bool("active", "Active", default=True),
], access={"base.user": "r", "account.manager": "rwcd", "base.admin": "rwcd"}, order="name ASC", views={
    "list": list_view("name", "rate", "scope", "account_id"),
    "form": form(sections=[section(col("rate", "scope", "account_id"))]),
})

terms = model("account.payment.term", "Payment Terms", [
    F.char("name", "Name", required=True), F.int("days", "Due in (days)", default=30),
], access={"base.user": "r", "account.manager": "rwcd", "base.admin": "rwcd"}, order="days ASC", views={
    "list": list_view("name", "days"), "form": form(sections=[section(col("days"))]),
})

MOVE_TYPES = [("entry", "Journal Entry"), ("out_invoice", "Customer Invoice"), ("out_refund", "Credit Note"),
              ("in_invoice", "Vendor Bill"), ("in_refund", "Vendor Refund")]
INVOICE_TYPES = ("out_invoice", "out_refund", "in_invoice", "in_refund")
OPEN = [["state", "!=", "draft"]]
is_invoice = ["move_type", "in", list(INVOICE_TYPES)]

move = model("account.move", "Invoice / Entry", [
    F.char("name", "Number", readonly=True, default="Draft"),
    F.sel("move_type", "Type", MOVE_TYPES, required=True, default="entry"),
    F.sel("state", "Status", [("draft", "Draft"), ("posted", "Posted"), ("cancel", "Cancelled")], readonly=True, default="draft", tracked=True),
    F.m2o("partner_id", "Partner", "res.partner", tracked=True),
    F.date("date", "Accounting Date", required=True, default=lambda env: date.today()),
    F.date("invoice_date_due", "Due Date", tracked=True),
    F.m2o("payment_term_id", "Payment Terms", "account.payment.term", onchange=True),
    F.m2o("journal_id", "Journal", "account.journal"),
    F.char("ref", "Reference"), F.text("narration", "Terms and Conditions"),
    F.m2o("invoice_user_id", "Salesperson", "res.users", ondelete="SET NULL"),
    F.o2m("invoice_line_ids", "Invoice Lines", "account.invoice.line", "move_id",
          columns=["product_id", "name", "account_id", "quantity", "price_unit", "tax_id", "price_subtotal"]),
    F.o2m("line_ids", "Journal Items", "account.move.line", "move_id",
          columns=["account_id", "partner_id", "name", "debit", "credit"]),
    F.money("amount_untaxed", "Untaxed Amount", readonly=True, default=0),
    F.money("amount_tax", "Tax", readonly=True, default=0),
    F.money("amount_total", "Total", readonly=True, default=0),
    F.money("amount_residual", "Amount Due", readonly=True, default=0),
    F.sel("payment_state", "Payment", [("not_paid", "Not Paid"), ("partial", "Partially Paid"), ("paid", "Paid"), ("na", "N/A")], readonly=True, default="na"),
    F.m2o("reversed_entry_id", "Reversal of", "account.move", readonly=True, ondelete="SET NULL"),
    F.m2o("sale_id", "Sales Order", "sale.order", readonly=True, ondelete="SET NULL"),
    F.m2o("purchase_id", "Purchase Order", "purchase.order", readonly=True, ondelete="SET NULL"),
    F.m2o("subscription_id", "Subscription", "subscription.subscription", readonly=True, ondelete="SET NULL"),
], access=BILLING, mail=True, order="date DESC, id DESC", rec_name="name", locked_when=[["state", "!=", "draft"]],
    search_fields=["name", "ref"], views={
    "list": list_view("name", "partner_id", "date", "invoice_date_due",
                      {"field": "amount_untaxed", "sum": True}, {"field": "amount_total", "sum": True},
                      {"field": "amount_residual", "sum": True}, {"field": "payment_state", "widget": "badge"},
                      {"field": "state", "widget": "badge"}),
    "form": form(title="name", status="state", printable="invoice", chatter=True, buttons=[
        btn("post", "Confirm", primary=True, when=[["state", "=", "draft"]]),
        btn("register_payment", "Register Payment", primary=True, when=[["state", "=", "posted"], ["payment_state", "!=", "paid"], is_invoice]),
        btn("reverse", "Credit Note", when=[["state", "=", "posted"], is_invoice]),
        btn("reset_draft", "Reset to Draft", when=[["state", "in", ["posted", "cancel"]]]),
        btn("cancel", "Cancel", when=[["state", "=", "draft"]]),
    ], sections=[
        section(col("partner_id", "ref", "payment_term_id"), col("move_type", "date", "invoice_date_due", "journal_id", "payment_state"),
                tabs=[tab("Invoice Lines", {"field": "invoice_line_ids", "totals": ["amount_untaxed", "amount_tax", "amount_total", "amount_residual"], "invisible": [["move_type", "=", "entry"]]}),
                      tab("Journal Items", {"field": "line_ids", "readonly": [["move_type", "!=", "entry"]]}),
                      tab("Other Info", "invoice_user_id", "reversed_entry_id", "narration")]),
    ]),
    "graph": graph("date:month", ["amount_total"]),
    "pivot": pivot("partner_id", "date:quarter", ["amount_total", "amount_residual"]),
    "search": search(fields=["name", "ref", "partner_id.name"], filters=[
        flt("draft", "Draft", [["state", "=", "draft"]]), flt("posted", "Posted", [["state", "=", "posted"]]),
        flt("unpaid", "Unpaid", [["state", "=", "posted"], ["payment_state", "in", ["not_paid", "partial"]]]),
        flt("overdue", "Overdue", [["state", "=", "posted"], ["payment_state", "in", ["not_paid", "partial"]], ["invoice_date_due", "<", "$today"]]),
    ], group_by=["partner_id", "state", "payment_state", "date:month"]),
})

invoice_line = model("account.invoice.line", "Invoice Line", [
    F.m2o("move_id", "Invoice", "account.move", required=True, ondelete="CASCADE"),
    F.m2o("product_id", "Product", "product.product", onchange=True),
    F.char("name", "Description", required=True),
    F.m2o("account_id", "Account", "account.account"),
    F.float("quantity", "Quantity", default=1, onchange=True),
    F.money("price_unit", "Unit Price", default=0, onchange=True),
    F.m2o("tax_id", "Tax", "account.tax", onchange=True),
    F.money("price_subtotal", "Subtotal", readonly=True, default=0),
    F.money("price_tax", "Tax Amount", readonly=True, default=0),
    F.m2o("sale_line_id", "Sale Line", "sale.order.line", readonly=True, ondelete="SET NULL"),
    F.m2o("purchase_line_id", "Purchase Line", "purchase.order.line", readonly=True, ondelete="SET NULL"),
], access=BILLING, parent="move_id", order="id ASC")

move_line = model("account.move.line", "Journal Item", [
    F.m2o("move_id", "Entry", "account.move", required=True, ondelete="CASCADE"),
    F.m2o("account_id", "Account", "account.account", required=True),
    F.m2o("partner_id", "Partner", "res.partner"),
    F.char("name", "Label"),
    F.money("debit", "Debit", default=0), F.money("credit", "Credit", default=0),
    F.date("date", "Date", readonly=True),
    F.sel("parent_state", "Status", [("draft", "Draft"), ("posted", "Posted"), ("cancel", "Cancelled")], readonly=True, default="draft"),
    F.m2o("tax_id", "Tax", "account.tax", readonly=True),
], access=BILLING, order="id ASC", parent="move_id",
    constraints=[("amounts_positive", "CHECK (debit >= 0 AND credit >= 0)")], views={
    "list": list_view("date", "move_id", "account_id", "partner_id", "name", {"field": "debit", "sum": True}, {"field": "credit", "sum": True}),
    "search": search(fields=["name", "account_id.name", "partner_id.name"], filters=[flt("posted", "Posted", [["parent_state", "=", "posted"]])], group_by=["account_id", "partner_id"]),
    "graph": graph("account_id", ["debit", "credit"]),
})

partial = model("account.partial", "Payment Allocation", [
    F.m2o("invoice_id", "Invoice", "account.move", required=True, ondelete="CASCADE"),
    F.m2o("source_move_id", "Paid by", "account.move", required=True, ondelete="CASCADE"),
    F.money("amount", "Amount", required=True),
], access={"account.user": "r", "account.manager": "r", "base.admin": "r"}, rec_name="amount")

payment = model("account.payment", "Payment", [
    F.char("name", "Number", readonly=True, sequence="account.payment"),
    F.sel("payment_type", "Type", [("inbound", "Receive"), ("outbound", "Send")], required=True, default="inbound"),
    F.sel("state", "Status", [("draft", "Draft"), ("posted", "Posted"), ("cancel", "Cancelled")], readonly=True, default="draft", tracked=True),
    F.m2o("partner_id", "Partner", "res.partner", required=True),
    F.money("amount", "Amount", required=True),
    F.date("date", "Date", required=True, default=lambda env: date.today()),
    F.m2o("journal_id", "Journal", "account.journal", required=True, domain=[["kind", "in", ["bank", "cash"]]]),
    F.char("memo", "Memo"),
    F.m2o("move_id", "Journal Entry", "account.move", readonly=True, ondelete="SET NULL"),
], access=BILLING, mail=True, order="date DESC, id DESC", locked_when=[["state", "!=", "draft"]],
    constraints=[("amount_positive", "CHECK (amount > 0)")], views={
    "list": list_view("name", "date", "partner_id", "journal_id", "payment_type", {"field": "amount", "sum": True}, {"field": "state", "widget": "badge"}),
    "form": form(status="state", chatter=True, buttons=[
        btn("post", "Confirm", primary=True, when=[["state", "=", "draft"]]),
        btn("cancel", "Cancel Payment", when=[["state", "=", "posted"]]),
    ], smart_buttons=[smart("Journal Entry", "book-open", "account.move", [["id", "=", "$move_id"]])],
        sections=[section(col("payment_type", "partner_id", "amount"), col("date", "journal_id", "memo", "move_id"))]),
    "search": search(fields=["name", "partner_id.name", "memo"], group_by=["partner_id", "journal_id", "state"]),
})


# -- helpers ------------------------------------------------------------------------------------
def get_account(env, code: str) -> int:
    ids = env.sudo()["account.account"].search([["code", "=", code]], limit=1)
    if not ids:
        raise UserError(f"The chart of accounts has no account {code}.")
    return ids[0]


def journal_of(env, kind: str) -> int:
    ids = env.sudo()["account.journal"].search([["kind", "=", kind]], order="id ASC", limit=1)
    if not ids:
        raise UserError(f"Create a journal of type {kind} first.")
    return ids[0]


def check_lock_date(env, when):
    lock = env.company().get("lock_date")
    if lock and when <= lock:
        raise UserError(f"Entries on or before the lock date ({lock.isoformat()}) cannot be changed.")


def _sign(move_type: str) -> tuple[Decimal, Decimal]:
    """(direction of the income/expense lines, direction of the counterpart) as +1 debit, -1 credit."""
    return {"out_invoice": (-1, 1), "out_refund": (1, -1), "in_invoice": (1, -1), "in_refund": (-1, 1)}[move_type]


def line_amounts(env, quantity, price_unit, tax_id):
    subtotal = money(dec(quantity) * dec(price_unit))
    rate = Decimal(0)
    if tax_id:
        rate = env.sudo()["account.tax"].get(tax_id, ["rate"])["rate"]
    return subtotal, money(subtotal * rate / 100)


@invoice_line.hook("recompute")
def _recompute_invoice_line(env, ids):
    for line in env["account.invoice.line"].read(ids, ["id", "quantity", "price_unit", "tax_id"]):
        subtotal, tax_amount = line_amounts(env, line["quantity"], line["price_unit"], line["tax_id"])
        env.cur.execute("UPDATE account_invoice_line SET price_subtotal = %s, price_tax = %s WHERE id = %s",
                        (subtotal, tax_amount, line["id"]))


@move.hook("recompute")
def _recompute_move(env, ids):
    for move_id in ids:
        env.cur.execute("SELECT COALESCE(SUM(price_subtotal), 0), COALESCE(SUM(price_tax), 0) FROM account_invoice_line WHERE move_id = %s", (move_id,))
        untaxed, tax_amount = env.cur.fetchone()
        rec = env["account.move"].get(move_id, ["move_type", "state", "payment_term_id", "date", "invoice_date_due"])
        total = untaxed + tax_amount
        if rec["move_type"] == "entry":
            env.cur.execute("SELECT COALESCE(SUM(debit), 0) FROM account_move_line WHERE move_id = %s", (move_id,))
            total = env.cur.fetchone()[0]
            untaxed, tax_amount = total, Decimal(0)
        env.cur.execute("SELECT COALESCE(SUM(amount), 0) FROM account_partial WHERE invoice_id = %s", (move_id,))
        paid = env.cur.fetchone()[0]
        residual = total - paid if rec["move_type"] != "entry" else Decimal(0)
        if rec["move_type"] == "entry" or rec["state"] != "posted":
            state = "na"
        elif residual <= 0:
            state = "paid"
        elif paid > 0:
            state = "partial"
        else:
            state = "not_paid"
        env.cur.execute(
            """UPDATE account_move SET amount_untaxed = %s, amount_tax = %s, amount_total = %s,
               amount_residual = %s, payment_state = %s WHERE id = %s""",
            (untaxed, tax_amount, total, residual, state, move_id))


@invoice_line.set_onchange
def _invoice_line_onchange(env, values, changed, parent=None):
    out = {}
    move_type = (parent or {}).get("move_type", "out_invoice")
    if changed == "product_id" and values.get("product_id"):
        p = env.sudo()["product.product"].get(_id(values["product_id"]))
        sale = move_type in ("out_invoice", "out_refund")
        out["name"] = p["name"]
        out["price_unit"] = float(p["list_price"] if sale else p["standard_price"])
        tax_id = p["sale_tax_id"] if sale else p["purchase_tax_id"]
        out["tax_id"] = m2o_value(env, "account.tax", tax_id)
        out["account_id"] = m2o_value(env, "account.account", p["income_account_id"] if sale else p["expense_account_id"])
    merged = {**values, **out}
    subtotal, _ = line_amounts(env, merged.get("quantity") or 0, merged.get("price_unit") or 0, _id(merged.get("tax_id")))
    out["price_subtotal"] = float(subtotal)
    return out


def _id(value):
    return value[0] if isinstance(value, (list, tuple)) else value


def m2o_value(env, model_name, rec_id):
    if not rec_id:
        return None
    return [rec_id, env.sudo()[model_name].to_api([rec_id], ["id"])[0]["display_name"]]


@move.set_onchange
def _move_onchange(env, values, changed, parent=None):
    term = _id(values.get("payment_term_id"))
    if changed == "payment_term_id" and term and values.get("date"):
        days = env.sudo()["account.payment.term"].get(term, ["days"])["days"]
        due = date.fromisoformat(values["date"][:10]).toordinal() + (days or 0)
        return {"invoice_date_due": date.fromordinal(due).isoformat()}
    return {}


@move.hook("prepare_create")
def _move_prepare(env, vals):
    t = vals.get("move_type", "entry")
    if t != "entry" and not vals.get("journal_id"):
        vals["journal_id"] = journal_of(env, "sale" if t.startswith("out") else "purchase")
    elif t == "entry" and not vals.get("journal_id"):
        vals["journal_id"] = journal_of(env, "general")
    return vals


@move.hook("before_unlink")
def _move_unlink(env, ids):
    for rec in env.sudo()["account.move"].read(ids, ["state", "name"]):
        if rec["state"] != "draft":
            raise UserError(f"{rec['name']} is not a draft, so it cannot be deleted. Cancel or reverse it instead.")


def _sequence_for(env, m):
    year = m["date"].year
    code, prefix = {
        "out_invoice": (f"inv.{year}", f"INV/{year}/"), "out_refund": (f"rinv.{year}", f"RINV/{year}/"),
        "in_invoice": (f"bill.{year}", f"BILL/{year}/"), "in_refund": (f"rbill.{year}", f"RBILL/{year}/"),
        "entry": (f"misc.{year}", f"MISC/{year}/"),
    }[m["move_type"]]
    return env.next_sequence(code, prefix)


def post_move(env, move_id: int):
    env = env.sudo()
    moves = env["account.move"]
    m = moves.get(move_id)
    if m["state"] != "draft":
        raise UserError("Only draft entries can be posted.")
    check_lock_date(env, m["date"])
    if m["move_type"] != "entry":
        _generate_invoice_items(env, m)
    env.cur.execute("SELECT COALESCE(SUM(debit), 0), COALESCE(SUM(credit), 0), COUNT(*) FROM account_move_line WHERE move_id = %s", (move_id,))
    debit, credit, count = env.cur.fetchone()
    if not count or debit == 0:
        raise UserError("An entry needs at least one line with an amount.")
    if debit != credit:
        raise UserError(f"The entry is not balanced: debit {debit} and credit {credit}.")
    name = m["name"] if m["name"] and m["name"] != "Draft" else _sequence_for(env, m)
    moves.write([move_id], {"state": "posted", "name": name})
    env.cur.execute("UPDATE account_move_line SET parent_state = 'posted', date = %s WHERE move_id = %s", (m["date"], move_id))
    moves.m.hooks["recompute"](env, [move_id])
    # A credit note posted against an invoice reduces what is still owed on that invoice.
    if m["reversed_entry_id"] and m["move_type"] in ("out_refund", "in_refund"):
        allocate(env, m["reversed_entry_id"], move_id, moves.get(move_id, ["amount_total"])["amount_total"])


def _generate_invoice_items(env, m):
    if not m["partner_id"]:
        raise UserError("Select a partner before confirming.")
    env.cur.execute("SELECT id, product_id, name, account_id, price_subtotal, price_tax, tax_id FROM account_invoice_line WHERE move_id = %s ORDER BY id", (m["id"],))
    lines = env.cur.fetchall()
    if not lines:
        raise UserError("Add at least one invoice line before confirming.")
    env.cur.execute("DELETE FROM account_move_line WHERE move_id = %s", (m["id"],))
    direction, counter = _sign(m["move_type"])
    sale = m["move_type"].startswith("out")
    default_account = get_account(env, "400000" if sale else "600000")
    total = Decimal(0)
    taxes: dict[int, Decimal] = {}

    def item(account_id, amount, name, tax_id=None, sign=direction):
        if amount == 0:
            return
        debit, credit = (amount, Decimal(0)) if (sign > 0) == (amount > 0) else (Decimal(0), amount)
        debit, credit = abs(debit), abs(credit)
        env.cur.execute(
            """INSERT INTO account_move_line (move_id, account_id, partner_id, name, debit, credit, tax_id, date)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (m["id"], account_id, m["partner_id"], name, debit, credit, tax_id, m["date"]))

    for _, _product, name, account_id, subtotal, tax_amount, tax_id in lines:
        item(account_id or default_account, subtotal, name)
        total += subtotal + tax_amount
        if tax_id and tax_amount:
            taxes[tax_id] = taxes.get(tax_id, Decimal(0)) + tax_amount
    for tax_id, amount in taxes.items():
        t = env["account.tax"].get(tax_id, ["account_id", "name"])
        item(t["account_id"], amount, t["name"], tax_id)
    item(get_account(env, "121000" if sale else "211000"), total, m["name"] if m["name"] != "Draft" else "Balance", sign=counter)


def allocate(env, invoice_id: int, source_move_id: int, amount) -> Decimal:
    """Applies up to amount against an invoice. Returns what was actually used."""
    env = env.sudo()
    env.cur.execute("SELECT amount_residual FROM account_move WHERE id = %s FOR UPDATE", (invoice_id,))
    residual = env.cur.fetchone()[0]
    used = min(money(amount), residual)
    if used <= 0:
        return Decimal(0)
    env["account.partial"].create({"invoice_id": invoice_id, "source_move_id": source_move_id, "amount": used})
    env["account.move"].m.hooks["recompute"](env, [invoice_id])
    return used


@move.action("post", "Confirm", groups=["account.user", "account.manager"])
def action_post(env, ids, params):
    for move_id in ids:
        post_move(env, move_id)


@move.action("cancel", "Cancel", groups=["account.user", "account.manager"])
def action_cancel(env, ids, params):
    env = env.sudo()
    for rec in env["account.move"].read(ids, ["state"]):
        if rec["state"] != "draft":
            raise UserError("Only drafts can be cancelled. Reset posted entries to draft first.")
    env["account.move"].write(ids, {"state": "cancel"})


@move.action("reset_draft", "Reset to Draft", groups=["account.manager"], confirm="Remove the journal items and return to draft?")
def action_reset(env, ids, params):
    env = env.sudo()
    for rec in env["account.move"].read(ids, ["id", "date", "move_type"]):
        check_lock_date(env, rec["date"])
        env.cur.execute("SELECT 1 FROM account_payment WHERE move_id = %s", (rec["id"],))
        if env.cur.fetchone():
            raise UserError("This entry belongs to a payment. Cancel the payment instead.")
        env.cur.execute("SELECT COUNT(*) FROM account_partial WHERE invoice_id = %s", (rec["id"],))
        if env.cur.fetchone()[0]:
            raise UserError("This invoice has payments or credit notes applied. Cancel those first.")
        env.cur.execute("SELECT invoice_id FROM account_partial WHERE source_move_id = %s", (rec["id"],))
        touched = [r[0] for r in env.cur.fetchall()]
        env.cur.execute("DELETE FROM account_partial WHERE source_move_id = %s", (rec["id"],))
        if touched:
            env["account.move"].m.hooks["recompute"](env, touched)
        if rec["move_type"] != "entry":
            env.cur.execute("DELETE FROM account_move_line WHERE move_id = %s", (rec["id"],))
        else:
            env.cur.execute("UPDATE account_move_line SET parent_state = 'draft' WHERE move_id = %s", (rec["id"],))
        env["account.move"].write([rec["id"]], {"state": "draft"})


@move.action("reverse", "Credit Note", groups=["account.user", "account.manager"])
def action_reverse(env, ids, params):
    env = env.sudo()
    rec = env["account.move"].get(ids[0])
    if rec["state"] != "posted" or rec["move_type"] not in INVOICE_TYPES:
        raise UserError("Only posted invoices and bills can be reversed.")
    if rec["move_type"] in ("out_refund", "in_refund"):
        raise UserError("A credit note cannot itself be reversed.")
    new_type = {"out_invoice": "out_refund", "in_invoice": "in_refund"}[rec["move_type"]]
    new_id = env["account.move"].create({
        "move_type": new_type, "partner_id": rec["partner_id"], "date": date.today(), "journal_id": rec["journal_id"],
        "ref": f"Reversal of {rec['name']}", "reversed_entry_id": ids[0], "invoice_date_due": date.today(),
    })
    env.cur.execute("SELECT product_id, name, account_id, quantity, price_unit, tax_id FROM account_invoice_line WHERE move_id = %s ORDER BY id", (ids[0],))
    for product_id, name, account_id, quantity, price_unit, tax_id in env.cur.fetchall():
        env["account.invoice.line"].create({"move_id": new_id, "product_id": product_id, "name": name, "account_id": account_id,
                                            "quantity": quantity, "price_unit": price_unit, "tax_id": tax_id})
    return {"type": "open", "model": "account.move", "id": new_id}


PAYMENT_PARAMS = [
    {"name": "amount", "label": "Amount", "type": "money", "required": True, "default_field": "amount_residual"},
    {"name": "date", "label": "Payment Date", "type": "date", "required": True, "default_today": True},
    {"name": "journal_id", "label": "Journal", "type": "many2one", "comodel": "account.journal", "required": True,
     "domain": [["kind", "in", ["bank", "cash"]]]},
    {"name": "memo", "label": "Memo", "type": "char"},
]


@move.action("register_payment", "Register Payment", groups=["account.user", "account.manager"], params=PAYMENT_PARAMS)
def action_register_payment(env, ids, params):
    env = env.sudo()
    rec = env["account.move"].get(ids[0])
    if rec["state"] != "posted" or rec["payment_state"] == "paid":
        raise UserError("This document has nothing left to pay.")
    amount = money(params.get("amount"))
    if amount <= 0:
        raise UserError("The payment amount must be positive.")
    if amount > rec["amount_residual"]:
        raise UserError(f"The payment is more than the {rec['amount_residual']} due.")
    if not params.get("journal_id"):
        raise UserError("Select a journal.")
    inbound = rec["move_type"] in ("out_invoice", "in_refund")
    payment_id = env["account.payment"].create({
        "payment_type": "inbound" if inbound else "outbound", "partner_id": rec["partner_id"], "amount": amount,
        "date": params.get("date") or date.today(), "journal_id": _id(params["journal_id"]),
        "memo": params.get("memo") or rec["name"],
    })
    post_payment(env, payment_id, invoice_id=ids[0])
    post_note(env, "account.move", ids[0], f"Payment of {amount} registered.")
    return {"type": "notify", "message": "Payment registered."}


def post_payment(env, payment_id: int, invoice_id: int | None = None):
    env = env.sudo()
    p = env["account.payment"].get(payment_id)
    if p["state"] != "draft":
        raise UserError("This payment is already posted.")
    d = p["date"] if isinstance(p["date"], date) else date.fromisoformat(str(p["date"]))
    check_lock_date(env, d)
    j = env["account.journal"].get(p["journal_id"], ["default_account_id"])
    if not j["default_account_id"]:
        raise UserError("The journal has no default account.")
    inbound = p["payment_type"] == "inbound"
    counter = get_account(env, "121000" if inbound else "211000")
    move_id = env["account.move"].create({"move_type": "entry", "date": d, "journal_id": p["journal_id"], "partner_id": p["partner_id"], "ref": p["name"]})
    bank_vals = {"debit": p["amount"], "credit": 0} if inbound else {"debit": 0, "credit": p["amount"]}
    other_vals = {"debit": 0, "credit": p["amount"]} if inbound else {"debit": p["amount"], "credit": 0}
    env["account.move.line"].create({"move_id": move_id, "account_id": j["default_account_id"], "partner_id": p["partner_id"], "name": p["name"], **bank_vals})
    env["account.move.line"].create({"move_id": move_id, "account_id": counter, "partner_id": p["partner_id"], "name": p["name"], **other_vals})
    post_move(env, move_id)
    env["account.payment"].write([payment_id], {"state": "posted", "move_id": move_id})
    if invoice_id:
        allocate(env, invoice_id, move_id, p["amount"])
    return move_id


@payment.action("post", "Confirm", groups=["account.user", "account.manager"])
def action_payment_post(env, ids, params):
    for payment_id in ids:
        post_payment(env, payment_id)


@payment.action("cancel", "Cancel Payment", groups=["account.manager"], confirm="Reverse this payment?")
def action_payment_cancel(env, ids, params):
    env = env.sudo()
    for p in env["account.payment"].read(ids, ["id", "move_id", "state"]):
        if p["state"] != "posted":
            raise UserError("Only posted payments can be cancelled.")
        entry = env["account.move"].get(p["move_id"], ["date"])
        check_lock_date(env, entry["date"])
        env.cur.execute("SELECT invoice_id FROM account_partial WHERE source_move_id = %s", (p["move_id"],))
        touched = [r[0] for r in env.cur.fetchall()]
        env.cur.execute("DELETE FROM account_partial WHERE source_move_id = %s", (p["move_id"],))
        env["account.move"].write([p["move_id"]], {"state": "cancel"})
        env.cur.execute("UPDATE account_move_line SET parent_state = 'cancel' WHERE move_id = %s", (p["move_id"],))
        if touched:
            env["account.move"].m.hooks["recompute"](env, touched)
        env["account.payment"].write([p["id"]], {"state": "cancel"})


# -- reports ------------------------------------------------------------------------------------
def _balances(env, date_from, date_to):
    env.cur.execute(
        """SELECT a.id, a.code, a.name, a.account_type, COALESCE(SUM(l.debit), 0), COALESCE(SUM(l.credit), 0)
           FROM account_account a
           LEFT JOIN account_move_line l ON l.account_id = a.id AND l.parent_state = 'posted'
                AND (%(from)s::date IS NULL OR l.date >= %(from)s) AND (%(to)s::date IS NULL OR l.date <= %(to)s)
           GROUP BY a.id ORDER BY a.code""", {"from": date_from, "to": date_to})
    return env.cur.fetchall()


def report_trial_balance(env, date_from, date_to):
    rows = [{"code": c, "name": n, "type": dict(ACCOUNT_TYPES)[t], "debit": float(d), "credit": float(cr), "balance": float(d - cr)}
            for _, c, n, t, d, cr in _balances(env, date_from, date_to) if d or cr]
    return {"title": "Trial Balance", "columns": [
        {"key": "code", "label": "Code"}, {"key": "name", "label": "Account"}, {"key": "type", "label": "Type"},
        {"key": "debit", "label": "Debit", "money": True}, {"key": "credit", "label": "Credit", "money": True},
        {"key": "balance", "label": "Balance", "money": True}], "rows": rows,
        "totals": {"name": "Total", "debit": sum(r["debit"] for r in rows), "credit": sum(r["credit"] for r in rows), "balance": sum(r["balance"] for r in rows)}}


def _section(title, accounts, sign):
    rows = [{"section": True, "name": title}]
    total = Decimal(0)
    for _, code, name, _t, d, c in accounts:
        amount = (d - c) * sign
        if amount:
            rows.append({"code": code, "name": name, "amount": float(amount)})
            total += amount
    rows.append({"subtotal": True, "name": f"Total {title}", "amount": float(total)})
    return rows, total


def report_profit_loss(env, date_from, date_to):
    data = _balances(env, date_from, date_to)
    income, t_income = _section("Income", [r for r in data if r[3] in INCOME_TYPES], -1)
    cogs, t_cogs = _section("Cost of Revenue", [r for r in data if r[3] == "cogs"], 1)
    expense, t_expense = _section("Expenses", [r for r in data if r[3] == "expense"], 1)
    gross = {"subtotal": True, "name": "Gross Profit", "amount": float(t_income - t_cogs)}
    return {"title": "Profit and Loss", "columns": [{"key": "code", "label": "Code"}, {"key": "name", "label": "Account"}, {"key": "amount", "label": "Amount", "money": True}],
            "rows": income + cogs + [gross] + expense, "totals": {"name": "Net Profit", "amount": float(t_income - t_cogs - t_expense)}}


def report_balance_sheet(env, date_from, date_to):
    # A balance sheet is a position at a date, so everything up to date_to counts.
    data = _balances(env, None, date_to)
    assets, t_assets = _section("Assets", [r for r in data if r[3] in ASSET_TYPES], 1)
    liabilities, t_liab = _section("Liabilities", [r for r in data if r[3] in LIABILITY_TYPES], -1)
    equity, t_equity = _section("Equity", [r for r in data if r[3] == "equity"], -1)
    earnings = sum(((r[5] - r[4]) for r in data if r[3] in INCOME_TYPES + EXPENSE_TYPES), Decimal(0))
    equity.insert(-1, {"name": "Unallocated Earnings", "amount": float(earnings)})
    equity[-1]["amount"] = float(t_equity + earnings)
    return {"title": "Balance Sheet", "columns": [{"key": "code", "label": "Code"}, {"key": "name", "label": "Account"}, {"key": "amount", "label": "Amount", "money": True}],
            "rows": assets + liabilities + equity, "totals": {"name": "Liabilities + Equity", "amount": float(t_liab + t_equity + earnings)},
            "meta": {"assets": float(t_assets), "liabilities_equity": float(t_liab + t_equity + earnings)}}


def _aged(env, types, date_to, title):
    env.cur.execute(
        """SELECT p.name,
             SUM(CASE WHEN COALESCE(m.invoice_date_due, m.date) >= %(d)s THEN residual ELSE 0 END),
             SUM(CASE WHEN %(d)s - COALESCE(m.invoice_date_due, m.date) BETWEEN 1 AND 30 THEN residual ELSE 0 END),
             SUM(CASE WHEN %(d)s - COALESCE(m.invoice_date_due, m.date) BETWEEN 31 AND 60 THEN residual ELSE 0 END),
             SUM(CASE WHEN %(d)s - COALESCE(m.invoice_date_due, m.date) BETWEEN 61 AND 90 THEN residual ELSE 0 END),
             SUM(CASE WHEN %(d)s - COALESCE(m.invoice_date_due, m.date) > 90 THEN residual ELSE 0 END),
             SUM(residual)
           FROM (SELECT *, amount_residual * CASE WHEN move_type LIKE '%%refund' THEN -1 ELSE 1 END AS residual FROM account_move) m
           JOIN res_partner p ON p.id = m.partner_id
           WHERE m.state = 'posted' AND m.move_type = ANY(%(t)s) AND m.amount_residual <> 0 AND m.date <= %(d)s
           GROUP BY p.name ORDER BY p.name""", {"d": date_to, "t": list(types)})
    keys = ["current", "d30", "d60", "d90", "older", "total"]
    rows = [{"name": r[0], **{k: float(v) for k, v in zip(keys, r[1:])}} for r in env.cur.fetchall()]
    labels = ["Not due", "1-30", "31-60", "61-90", "Older", "Total"]
    return {"title": title, "columns": [{"key": "name", "label": "Partner"}] + [{"key": k, "label": lab, "money": True} for k, lab in zip(keys, labels)],
            "rows": rows, "totals": {"name": "Total", **{k: sum(r[k] for r in rows) for k in keys}}}


def report_aged_receivable(env, date_from, date_to):
    return _aged(env, ("out_invoice", "out_refund"), date_to or date.today(), "Aged Receivable")


def report_aged_payable(env, date_from, date_to):
    return _aged(env, ("in_invoice", "in_refund"), date_to or date.today(), "Aged Payable")


def report_general_ledger(env, date_from, date_to):
    env.cur.execute(
        """SELECT l.date, a.code, a.name, m.name, p.name, l.name, l.debit, l.credit
           FROM account_move_line l JOIN account_account a ON a.id = l.account_id JOIN account_move m ON m.id = l.move_id
           LEFT JOIN res_partner p ON p.id = l.partner_id
           WHERE l.parent_state = 'posted' AND (%(from)s::date IS NULL OR l.date >= %(from)s) AND (%(to)s::date IS NULL OR l.date <= %(to)s)
           ORDER BY a.code, l.date, l.id LIMIT 5000""", {"from": date_from, "to": date_to})
    rows = [{"date": r[0].isoformat(), "code": r[1], "account": r[2], "move": r[3], "partner": r[4] or "", "label": r[5] or "", "debit": float(r[6]), "credit": float(r[7])} for r in env.cur.fetchall()]
    return {"title": "General Ledger", "columns": [
        {"key": "date", "label": "Date"}, {"key": "code", "label": "Code"}, {"key": "account", "label": "Account"}, {"key": "move", "label": "Entry"},
        {"key": "partner", "label": "Partner"}, {"key": "label", "label": "Label"}, {"key": "debit", "label": "Debit", "money": True}, {"key": "credit", "label": "Credit", "money": True}],
        "rows": rows, "totals": {"account": "Total", "debit": sum(r["debit"] for r in rows), "credit": sum(r["credit"] for r in rows)}}


def report_tax(env, date_from, date_to):
    env.cur.execute(
        """SELECT t.name, t.scope, COALESCE(SUM(l.credit - l.debit), 0) FROM account_move_line l JOIN account_tax t ON t.id = l.tax_id
           WHERE l.parent_state = 'posted' AND (%(from)s::date IS NULL OR l.date >= %(from)s) AND (%(to)s::date IS NULL OR l.date <= %(to)s)
           GROUP BY t.name, t.scope ORDER BY t.scope DESC, t.name""", {"from": date_from, "to": date_to})
    rows = [{"name": n, "scope": "Sales" if s == "sale" else "Purchases", "amount": float(a)} for n, s, a in env.cur.fetchall()]
    return {"title": "Tax Report", "columns": [{"key": "name", "label": "Tax"}, {"key": "scope", "label": "Scope"}, {"key": "amount", "label": "Net Tax Collected", "money": True}],
            "rows": rows, "totals": {"name": "Net Tax Due", "amount": sum(r["amount"] for r in rows)}}


REPORTS = {
    "trial_balance": report_trial_balance, "profit_loss": report_profit_loss, "balance_sheet": report_balance_sheet,
    "aged_receivable": report_aged_receivable, "aged_payable": report_aged_payable, "general_ledger": report_general_ledger, "tax": report_tax,
}

ctx_invoice = {"default_move_type": "out_invoice"}
ctx_bill = {"default_move_type": "in_invoice"}
app("accounting", "Accounting", "landmark", "#1f6f54", [
    menu("dashboard", "Overview", client="accounting_overview"),
    menu("customers", "Customers", children=[
        menu("invoices", "Invoices", "account.move", ("list", "form", "graph", "pivot"), [["move_type", "=", "out_invoice"]], ctx_invoice),
        menu("credit_notes", "Credit Notes", "account.move", ("list", "form"), [["move_type", "=", "out_refund"]], {"default_move_type": "out_refund"}),
        menu("receipts", "Payments", "account.payment", domain=[["payment_type", "=", "inbound"]], context={"default_payment_type": "inbound"}),
    ]),
    menu("vendors", "Vendors", children=[
        menu("bills", "Bills", "account.move", ("list", "form", "graph", "pivot"), [["move_type", "=", "in_invoice"]], ctx_bill),
        menu("refunds", "Refunds", "account.move", ("list", "form"), [["move_type", "=", "in_refund"]], {"default_move_type": "in_refund"}),
        menu("payments", "Payments", "account.payment", domain=[["payment_type", "=", "outbound"]], context={"default_payment_type": "outbound"}),
    ]),
    menu("accounting", "Accounting", children=[
        menu("entries", "Journal Entries", "account.move", ("list", "form"), [["move_type", "=", "entry"]], {"default_move_type": "entry"}),
        menu("items", "Journal Items", "account.move.line", ("list", "graph")),
    ]),
    menu("reporting", "Reporting", children=[
        menu("pnl", "Profit and Loss", client="report:profit_loss"), menu("bs", "Balance Sheet", client="report:balance_sheet"),
        menu("tb", "Trial Balance", client="report:trial_balance"), menu("gl", "General Ledger", client="report:general_ledger"),
        menu("ar", "Aged Receivable", client="report:aged_receivable"), menu("ap", "Aged Payable", client="report:aged_payable"),
        menu("tax", "Tax Report", client="report:tax"),
    ]),
    menu("config", "Configuration", children=[
        menu("accounts", "Chart of Accounts", "account.account"), menu("journals", "Journals", "account.journal"),
        menu("taxes", "Taxes", "account.tax"), menu("terms", "Payment Terms", "account.payment.term"),
    ]),
], groups=["account.user", "account.manager"], sequence=60)
