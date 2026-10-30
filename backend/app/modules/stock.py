"""Products and inventory: locations, transfers, on-hand quantities, adjustments, reordering."""
from datetime import date
from decimal import Decimal

from ..orm import F, UserError, app, dec, menu, model, post_note
from ..views import btn, col, flt, form, graph, kanban, list_view, pivot, search, section, smart, tab

STOCK = {"stock.user": "rwc", "stock.manager": "rwcd", "base.admin": "rwcd"}
ON_HAND = ("SELECT COALESCE(SUM(q.quantity), 0) FROM stock_quant q JOIN stock_location l ON l.id = q.location_id "
           "WHERE q.product_id = {t}.id AND l.usage = 'internal'")

category = model("product.category", "Product Category", [F.char("name", "Name", required=True)],
                 access={"base.user": "r", "stock.manager": "rwcd", "sales.manager": "rwcd", "base.admin": "rwcd"}, order="name ASC",
                 views={"list": list_view("name"), "form": form()})
uom = model("uom.uom", "Unit of Measure", [F.char("name", "Unit", required=True)],
            access={"base.user": "r", "stock.manager": "rwcd", "base.admin": "rwcd"}, order="name ASC",
            views={"list": list_view("name"), "form": form()})

product = model("product.product", "Product", [
    F.char("name", "Product Name", required=True, tracked=True),
    F.char("default_code", "Internal Reference"), F.char("barcode", "Barcode"),
    F.sel("product_type", "Product Type", [("storable", "Storable"), ("consumable", "Consumable"), ("service", "Service")], default="storable", required=True),
    F.sel("invoice_policy", "Invoicing Policy", [("order", "Ordered quantities"), ("delivery", "Delivered quantities")], default="order"),
    F.m2o("category_id", "Category", "product.category", ondelete="SET NULL"),
    F.m2o("uom_id", "Unit", "uom.uom", ondelete="SET NULL"),
    F.money("list_price", "Sales Price", default=0, tracked=True),
    F.money("standard_price", "Cost", default=0, tracked=True),
    F.m2o("sale_tax_id", "Customer Tax", "account.tax", domain=[["scope", "=", "sale"]], ondelete="SET NULL"),
    F.m2o("purchase_tax_id", "Vendor Tax", "account.tax", domain=[["scope", "=", "purchase"]], ondelete="SET NULL"),
    F.m2o("income_account_id", "Income Account", "account.account", ondelete="SET NULL"),
    F.m2o("expense_account_id", "Expense Account", "account.account", ondelete="SET NULL"),
    F.m2o("seller_id", "Vendor", "res.partner", domain=[["is_vendor", "=", True]], ondelete="SET NULL"),
    F.bool("sale_ok", "Can be Sold", default=True), F.bool("purchase_ok", "Can be Purchased", default=True),
    F.bool("pos_ok", "Available in POS", default=False),
    F.text("description", "Description"), F.bool("active", "Active", default=True),
    F.sql("qty_available", "On Hand", "float", ON_HAND),
], access={"base.user": "r", "stock.manager": "rwcd", "sales.manager": "rwcd", "purchase.manager": "rwcd", "mrp.manager": "rwcd", "pos.manager": "rwcd", "base.admin": "rwcd"},
    mail=True, order="name ASC", search_fields=["name", "default_code", "barcode"], views={
    "kanban": kanban(title="name", subtitle="default_code", amount="list_price", fields=["qty_available"], badge="product_type"),
    "list": list_view("default_code", "name", "category_id", "list_price", "standard_price", "qty_available", "product_type"),
    "form": form(chatter=True, smart_buttons=[
        smart("Stock", "boxes", "stock.quant", [["product_id", "=", "$id"], ["location_id.usage", "=", "internal"]]),
        smart("Moves", "arrow-left-right", "stock.move", [["product_id", "=", "$id"]]),
        smart("BoMs", "layers", "mrp.bom", [["product_id", "=", "$id"]]),
    ], sections=[
        section(col("product_type", "invoice_policy", "category_id", "uom_id", "default_code", "barcode"),
                col("list_price", "sale_tax_id", "standard_price", "purchase_tax_id", "qty_available", "seller_id"),
                tabs=[tab("Accounting", "income_account_id", "expense_account_id"),
                      tab("Options", "sale_ok", "purchase_ok", "pos_ok"), tab("Description", "description")]),
    ]),
    "search": search(fields=["name", "default_code", "barcode"], filters=[
        flt("services", "Services", [["product_type", "=", "service"]]), flt("goods", "Goods", [["product_type", "!=", "service"]]),
        flt("archived", "Archived", [["active", "=", False]]),
    ], group_by=["category_id", "product_type"]),
})

location = model("stock.location", "Location", [
    F.char("name", "Location Name", required=True),
    F.sel("usage", "Type", [("internal", "Internal"), ("supplier", "Vendor"), ("customer", "Customer"), ("inventory", "Inventory Loss"), ("production", "Production")], required=True, default="internal"),
    F.bool("active", "Active", default=True),
], access={"base.user": "r", "stock.manager": "rwcd", "base.admin": "rwcd"}, order="name ASC", views={
    "list": list_view("name", "usage"), "form": form(sections=[section(col("usage"))]),
})

quant = model("stock.quant", "On Hand", [
    F.m2o("product_id", "Product", "product.product", required=True, readonly=True),
    F.m2o("location_id", "Location", "stock.location", required=True, readonly=True),
    F.float("quantity", "Quantity", readonly=True, default=0),
    F.sql("value", "Value", "money", "SELECT {t}.quantity * p.standard_price FROM product_product p WHERE p.id = {t}.product_id"),
], access={"base.user": "r"}, rec_name="id", order="id DESC", constraints=[("uniq", "UNIQUE (product_id, location_id)")], views={
    "list": list_view("product_id", "location_id", {"field": "quantity", "sum": True}, {"field": "value", "sum": True}),
    "search": search(fields=["product_id.name", "location_id.name"], group_by=["product_id", "location_id"]),
    "graph": graph("location_id", ["value"]),
})

PICKING_STATES = [("draft", "Draft"), ("assigned", "Ready"), ("done", "Done"), ("cancel", "Cancelled")]
picking = model("stock.picking", "Transfer", [
    F.char("name", "Reference", readonly=True),
    F.sel("kind", "Operation", [("incoming", "Receipt"), ("outgoing", "Delivery"), ("internal", "Internal Transfer")], required=True, default="internal"),
    F.sel("state", "Status", PICKING_STATES, readonly=True, default="draft", tracked=True),
    F.m2o("partner_id", "Contact", "res.partner"),
    F.m2o("location_id", "Source", "stock.location", required=True),
    F.m2o("location_dest_id", "Destination", "stock.location", required=True),
    F.date("scheduled_date", "Scheduled Date", default=lambda env: date.today()),
    F.date("date_done", "Effective Date", readonly=True),
    F.char("origin", "Source Document"),
    F.m2o("sale_id", "Sales Order", "sale.order", readonly=True, ondelete="SET NULL"),
    F.m2o("purchase_id", "Purchase Order", "purchase.order", readonly=True, ondelete="SET NULL"),
    F.m2o("backorder_id", "Backorder of", "stock.picking", readonly=True, ondelete="SET NULL"),
    F.o2m("move_ids", "Operations", "stock.move", "picking_id", columns=["product_id", "name", "quantity", "quantity_done"]),
], access=STOCK, mail=True, order="scheduled_date DESC, id DESC", locked_when=[["state", "in", ["done", "cancel"]]], search_fields=["name", "origin"], views={
    "kanban": kanban(group_by="kind", title="name", subtitle="partner_id", date="scheduled_date", badge="state"),
    "list": list_view("name", "partner_id", "location_id", "location_dest_id", "scheduled_date", "origin", {"field": "state", "widget": "badge"}),
    "form": form(status="state", printable="picking", chatter=True, buttons=[
        btn("confirm", "Mark as Ready", primary=True, when=[["state", "=", "draft"]]),
        btn("validate", "Validate", primary=True, when=[["state", "=", "assigned"]]),
        btn("cancel", "Cancel", when=[["state", "in", ["draft", "assigned"]]]),
    ], sections=[section(col("kind", "partner_id", "location_id", "location_dest_id"), col("scheduled_date", "date_done", "origin", "backorder_id"), tabs=[tab("Operations", "move_ids")])]),
    "search": search(fields=["name", "origin", "partner_id.name"], filters=[
        flt("ready", "Ready", [["state", "=", "assigned"]]), flt("late", "Late", [["state", "=", "assigned"], ["scheduled_date", "<", "$today"]]),
        flt("done", "Done", [["state", "=", "done"]]),
    ], group_by=["kind", "state", "partner_id"]),
})

stock_move = model("stock.move", "Stock Move", [
    F.m2o("picking_id", "Transfer", "stock.picking", ondelete="CASCADE"),
    F.m2o("product_id", "Product", "product.product", required=True, onchange=True),
    F.char("name", "Description"),
    F.float("quantity", "Demand", default=1), F.float("quantity_done", "Done", default=0),
    F.m2o("location_id", "From", "stock.location", required=False),
    F.m2o("location_dest_id", "To", "stock.location", required=False),
    F.sel("state", "Status", [("draft", "Draft"), ("done", "Done"), ("cancel", "Cancelled")], readonly=True, default="draft"),
    F.date("date", "Date", readonly=True),
    F.m2o("sale_line_id", "Sale Line", "sale.order.line", readonly=True, ondelete="SET NULL"),
    F.m2o("purchase_line_id", "Purchase Line", "purchase.order.line", readonly=True, ondelete="SET NULL"),
    F.char("reference", "Reference", readonly=True),
], access=STOCK, parent="picking_id", rec_name="name", order="id DESC", locked_when=[["state", "!=", "draft"]],
    constraints=[("qty_positive", "CHECK (quantity >= 0 AND quantity_done >= 0)")], views={
    "list": list_view("date", "reference", "product_id", "location_id", "location_dest_id", {"field": "quantity_done", "sum": True}, {"field": "state", "widget": "badge"}),
    "search": search(fields=["product_id.name", "reference"], filters=[flt("done", "Done", [["state", "=", "done"]])], group_by=["product_id", "location_id", "location_dest_id"]),
    "graph": graph("product_id", ["quantity_done"]),
})


@stock_move.set_onchange
def _move_onchange(env, values, changed, parent=None):
    if changed == "product_id" and values.get("product_id"):
        pid = values["product_id"]
        pid = pid[0] if isinstance(pid, (list, tuple)) else pid
        return {"name": env.sudo()["product.product"].get(pid, ["name"])["name"]}
    return {}


inventory = model("stock.inventory", "Inventory Adjustment", [
    F.char("name", "Reference", required=True, default=lambda env: f"Count {date.today().isoformat()}"),
    F.m2o("location_id", "Location", "stock.location", required=True, domain=[["usage", "=", "internal"]]),
    F.date("date", "Date", required=True, default=lambda env: date.today()),
    F.sel("state", "Status", [("draft", "Draft"), ("done", "Applied")], readonly=True, default="draft", tracked=True),
    F.o2m("line_ids", "Counted Products", "stock.inventory.line", "inventory_id", columns=["product_id", "counted_qty", "theoretical_qty", "difference"]),
], access={"stock.manager": "rwcd", "base.admin": "rwcd", "stock.user": "rwc"}, mail=True, locked_when=[["state", "=", "done"]], views={
    "list": list_view("name", "location_id", "date", {"field": "state", "widget": "badge"}),
    "form": form(status="state", chatter=True, buttons=[btn("apply", "Apply Count", primary=True, when=[["state", "=", "draft"]])],
                 sections=[section(col("location_id"), col("date"), tabs=[tab("Counted Products", "line_ids")])]),
})
model("stock.inventory.line", "Counted Product", [
    F.m2o("inventory_id", "Adjustment", "stock.inventory", required=True, ondelete="CASCADE"),
    F.m2o("product_id", "Product", "product.product", required=True),
    F.float("counted_qty", "Counted", default=0),
    F.float("theoretical_qty", "On Hand When Applied", readonly=True),
    F.float("difference", "Difference", readonly=True),
], access={"stock.manager": "rwcd", "base.admin": "rwcd", "stock.user": "rwcd"}, rec_name="id", order="id ASC", parent="inventory_id")

orderpoint = model("stock.orderpoint", "Reordering Rule", [
    F.m2o("product_id", "Product", "product.product", required=True),
    F.m2o("location_id", "Location", "stock.location", required=True, domain=[["usage", "=", "internal"]]),
    F.float("product_min_qty", "Min Quantity", default=0), F.float("product_max_qty", "Max Quantity", default=0),
    F.bool("active", "Active", default=True),
    F.sql("qty_on_hand", "On Hand", "float", "SELECT COALESCE(SUM(q.quantity), 0) FROM stock_quant q WHERE q.product_id = {t}.product_id AND q.location_id = {t}.location_id"),
], access=STOCK, rec_name="id", views={
    "list": list_view("product_id", "location_id", "qty_on_hand", "product_min_qty", "product_max_qty", buttons=[btn("replenish", "Order Replenishment", primary=True)]),
    "form": form(title=None, buttons=[btn("replenish", "Order Replenishment", primary=True)], sections=[section(col("product_id", "location_id"), col("product_min_qty", "product_max_qty", "qty_on_hand"))]),
})


# -- core stock logic ---------------------------------------------------------------------------
def location_by_usage(env, usage: str) -> int:
    ids = env.sudo()["stock.location"].search([["usage", "=", usage]], order="id ASC", limit=1)
    if not ids:
        raise UserError(f"No {usage} location is configured.")
    return ids[0]


def main_stock(env) -> int:
    return location_by_usage(env, "internal")


def change_quant(env, product_id, location_id, delta):
    env.cur.execute(
        """INSERT INTO stock_quant (product_id, location_id, quantity) VALUES (%s, %s, %s)
           ON CONFLICT (product_id, location_id) DO UPDATE SET quantity = stock_quant.quantity + EXCLUDED.quantity, write_date = now()
           RETURNING quantity""", (product_id, location_id, delta))
    return env.cur.fetchone()[0]


def apply_move(env, product_id, source_id, dest_id, qty, reference="", picking_id=None, name=None, **extra):
    """Moves quantity between locations and records it. Stock in internal locations cannot go below zero."""
    env = env.sudo()
    qty = dec(qty)
    if qty <= 0:
        return None
    usages = {r["id"]: r["usage"] for r in env["stock.location"].read(list({source_id, dest_id}), ["id", "usage"])}
    prod = env["product.product"].get(product_id, ["name", "product_type"])
    tracked = prod["product_type"] == "storable"
    if tracked:
        # The upsert takes the row lock, so concurrent deliveries cannot both spend the same stock.
        remaining = change_quant(env, product_id, source_id, -qty)
        change_quant(env, product_id, dest_id, qty)
        if usages[source_id] == "internal" and remaining < 0:
            raise UserError(f"Not enough stock of {prod['name']}: short by {-remaining}.")
    env.cur.execute(
        """INSERT INTO stock_move (picking_id, product_id, name, quantity, quantity_done, location_id, location_dest_id, state, date, reference)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'done', %s, %s) RETURNING id""",
        (picking_id, product_id, name or prod["name"], qty, qty, source_id, dest_id, date.today(), reference))
    return env.cur.fetchone()[0]


@picking.hook("prepare_create")
def _picking_prepare(env, vals):
    kind = vals.get("kind", "internal")
    vals["name"] = vals.get("name") or env.next_sequence(*{"incoming": ("stock.in", "WH/IN/"), "outgoing": ("stock.out", "WH/OUT/"), "internal": ("stock.int", "WH/INT/")}[kind])
    if not vals.get("location_id"):
        vals["location_id"] = location_by_usage(env, "supplier") if kind == "incoming" else main_stock(env)
    if not vals.get("location_dest_id"):
        vals["location_dest_id"] = location_by_usage(env, "customer") if kind == "outgoing" else main_stock(env)
    return vals


@picking.action("confirm", "Mark as Ready", groups=["stock.user", "stock.manager"])
def picking_confirm(env, ids, params):
    env = env.sudo()
    for pid in ids:
        env.cur.execute("SELECT COUNT(*) FROM stock_move WHERE picking_id = %s", (pid,))
        if not env.cur.fetchone()[0]:
            raise UserError("Add at least one product before marking the transfer as ready.")
    env["stock.picking"].write(ids, {"state": "assigned"})


@picking.action("validate", "Validate", groups=["stock.user", "stock.manager"],
                confirm=None)
def picking_validate(env, ids, params):
    env = env.sudo()
    for pid in ids:
        validate_picking(env, pid)


def validate_picking(env, picking_id: int):
    env = env.sudo()
    p = env["stock.picking"].get(picking_id)
    if p["state"] != "assigned":
        raise UserError("Only ready transfers can be validated.")
    env.cur.execute("SELECT id, product_id, name, quantity, quantity_done, sale_line_id, purchase_line_id FROM stock_move WHERE picking_id = %s AND state = 'draft' ORDER BY id FOR UPDATE", (picking_id,))
    moves = env.cur.fetchall()
    # With nothing entered, the whole demand is processed; otherwise the rest goes on a backorder.
    process_all = not any(m[4] for m in moves)
    remainder = []
    for move_id, product_id, name, quantity, done, sale_line, purchase_line in moves:
        qty = quantity if process_all else done
        if qty > 0:
            ptype = env["product.product"].get(product_id, ["product_type"])["product_type"]
            if ptype == "storable":
                left = change_quant(env, product_id, p["location_id"], -qty)
                change_quant(env, product_id, p["location_dest_id"], qty)
                src_usage = env["stock.location"].get(p["location_id"], ["usage"])["usage"]
                if src_usage == "internal" and left < 0:
                    raise UserError(f"Not enough stock of {name}: short by {-left}.")
        env.cur.execute(
            """UPDATE stock_move SET state = 'done', quantity_done = %s, location_id = %s, location_dest_id = %s, date = %s, reference = %s WHERE id = %s""",
            (qty, p["location_id"], p["location_dest_id"], date.today(), p["name"], move_id))
        if qty < quantity:
            remainder.append((product_id, name, quantity - qty, sale_line, purchase_line))
    env["stock.picking"].write([picking_id], {"state": "done", "date_done": date.today()})
    if remainder:
        back = env["stock.picking"].create({
            "kind": p["kind"], "partner_id": p["partner_id"], "location_id": p["location_id"], "location_dest_id": p["location_dest_id"],
            "origin": p["origin"], "sale_id": p["sale_id"], "purchase_id": p["purchase_id"], "backorder_id": picking_id, "state": "assigned",
        })
        for product_id, name, qty, sale_line, purchase_line in remainder:
            env["stock.move"].create({"picking_id": back, "product_id": product_id, "name": name, "quantity": qty,
                                      "sale_line_id": sale_line, "purchase_line_id": purchase_line})
        post_note(env, "stock.picking", picking_id, "Partially processed. The remainder is on a backorder.")
    from . import purchase, sales  # noqa: PLC0415  (they import this module)
    if p["sale_id"]:
        sales.update_delivered(env, p["sale_id"])
    if p["purchase_id"]:
        purchase.update_received(env, p["purchase_id"])
    return {"backorder": bool(remainder)}


@picking.action("cancel", "Cancel", groups=["stock.user", "stock.manager"])
def picking_cancel(env, ids, params):
    env = env.sudo()
    env["stock.picking"].write(ids, {"state": "cancel"})
    env.cur.execute("UPDATE stock_move SET state = 'cancel' WHERE picking_id = ANY(%s) AND state = 'draft'", (list(ids),))


@inventory.action("apply", "Apply Count", groups=["stock.manager"])
def inventory_apply(env, ids, params):
    env = env.sudo()
    loss = location_by_usage(env, "inventory")
    for inv in env["stock.inventory"].read(ids, ["id", "location_id", "name", "state"]):
        if inv["state"] != "draft":
            raise UserError("This adjustment has already been applied.")
        env.cur.execute("SELECT id, product_id, counted_qty FROM stock_inventory_line WHERE inventory_id = %s", (inv["id"],))
        lines = env.cur.fetchall()
        if not lines:
            raise UserError("Add the counted products first.")
        for line_id, product_id, counted in lines:
            env.cur.execute("SELECT quantity FROM stock_quant WHERE product_id = %s AND location_id = %s FOR UPDATE", (product_id, inv["location_id"]))
            row = env.cur.fetchone()
            theoretical = row[0] if row else Decimal(0)
            diff = dec(counted) - theoretical
            if diff > 0:
                apply_move(env, product_id, loss, inv["location_id"], diff, reference=inv["name"])
            elif diff < 0:
                apply_move(env, product_id, inv["location_id"], loss, -diff, reference=inv["name"])
            env.cur.execute("UPDATE stock_inventory_line SET theoretical_qty = %s, difference = %s WHERE id = %s", (theoretical, diff, line_id))
        env["stock.inventory"].write([inv["id"]], {"state": "done"})


def replenish(env, orderpoint_ids=None):
    """Creates or tops up one draft RFQ per vendor for products below their minimum."""
    env = env.sudo()
    rules = env["stock.orderpoint"]
    ids = orderpoint_ids if orderpoint_ids is not None else rules.search([])
    created = 0
    skipped = []
    for r in rules.read(ids, ["id", "product_id", "location_id", "product_min_qty", "product_max_qty", "qty_on_hand"]):
        if r["qty_on_hand"] >= r["product_min_qty"]:
            continue
        p = env["product.product"].get(r["product_id"], ["name", "seller_id", "standard_price", "purchase_tax_id"])
        if not p["seller_id"]:
            skipped.append(p["name"])
            continue
        # Do not reorder what an open purchase order already covers.
        env.cur.execute(
            """SELECT COALESCE(SUM(l.product_qty - l.qty_received), 0) FROM purchase_order_line l JOIN purchase_order o ON o.id = l.order_id
               WHERE l.product_id = %s AND o.state IN ('draft', 'purchase')""", (r["product_id"],))
        incoming = env.cur.fetchone()[0]
        need = dec(r["product_max_qty"]) - dec(r["qty_on_hand"]) - incoming
        if need <= 0:
            continue
        drafts = env["purchase.order"].search([["partner_id", "=", p["seller_id"]], ["state", "=", "draft"], ["origin", "=", "Reordering rules"]], limit=1)
        order_id = drafts[0] if drafts else env["purchase.order"].create({"partner_id": p["seller_id"], "origin": "Reordering rules"})
        env["purchase.order.line"].create({"order_id": order_id, "product_id": r["product_id"], "name": p["name"], "product_qty": need,
                                           "price_unit": p["standard_price"], "tax_id": p["purchase_tax_id"]})
        created += 1
    return created, skipped


@orderpoint.action("replenish", "Order Replenishment", groups=["stock.user", "stock.manager"])
def orderpoint_replenish(env, ids, params):
    created, skipped = replenish(env, ids)
    message = f"{created} product(s) added to draft purchase orders."
    if skipped:
        message += f" No vendor set for: {', '.join(skipped)}."
    return {"type": "notify", "message": message}


app("inventory", "Inventory", "boxes", "#b5651d", [
    menu("overview", "Overview", "stock.picking", ("kanban", "list", "form"), [["state", "=", "assigned"]]),
    menu("operations", "Operations", children=[
        menu("receipts", "Receipts", "stock.picking", ("list", "form"), [["kind", "=", "incoming"]], {"default_kind": "incoming"}),
        menu("deliveries", "Deliveries", "stock.picking", ("list", "form"), [["kind", "=", "outgoing"]], {"default_kind": "outgoing"}),
        menu("internal", "Internal Transfers", "stock.picking", ("list", "form"), [["kind", "=", "internal"]], {"default_kind": "internal"}),
        menu("adjustments", "Inventory Adjustments", "stock.inventory"),
        menu("reorder", "Reordering Rules", "stock.orderpoint"),
    ]),
    menu("products", "Products", "product.product", ("kanban", "list", "form")),
    menu("reporting", "Reporting", children=[
        menu("onhand", "Stock On Hand", "stock.quant", ("list", "graph"), [["location_id.usage", "=", "internal"]]),
        menu("moves", "Moves History", "stock.move", ("list", "graph"), [["state", "=", "done"]]),
    ]),
    menu("config", "Configuration", children=[
        menu("locations", "Locations", "stock.location"), menu("categories", "Product Categories", "product.category"),
        menu("uom", "Units of Measure", "uom.uom"),
    ]),
], groups=["stock.user", "stock.manager"], sequence=70)
