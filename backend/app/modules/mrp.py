"""Manufacturing: bills of materials, work centers, manufacturing orders and work orders."""
from datetime import datetime, timezone

from ..orm import F, UserError, app, dec, menu, model, post_note
from ..views import btn, col, flt, form, graph, kanban, list_view, search, section, smart, tab
from .stock import apply_move, location_by_usage, main_stock

MRP = {"mrp.user": "rwc", "mrp.manager": "rwcd", "base.admin": "rwcd"}
workcenter = model("mrp.workcenter", "Work Center", [
    F.char("name", "Work Center", required=True), F.char("code", "Code"), F.money("costs_hour", "Cost per Hour", default=0), F.bool("active", "Active", default=True),
], access={"mrp.user": "r", "mrp.manager": "rwcd", "base.admin": "rwcd"}, order="name ASC", views={
    "list": list_view("name", "code", "costs_hour"), "form": form(sections=[section(col("code", "costs_hour"))]),
})
bom = model("mrp.bom", "Bill of Materials", [
    F.char("name", "Reference", required=True), F.m2o("product_id", "Product", "product.product", required=True),
    F.float("product_qty", "Quantity Produced", default=1), F.bool("active", "Active", default=True),
    F.o2m("line_ids", "Components", "mrp.bom.line", "bom_id", columns=["product_id", "product_qty"]),
    F.o2m("operation_ids", "Operations", "mrp.bom.operation", "bom_id", columns=["name", "workcenter_id", "duration_minutes"]),
], access=MRP, mail=True, order="name ASC", views={
    "list": list_view("name", "product_id", "product_qty"),
    "form": form(chatter=True, sections=[section(col("product_id"), col("product_qty"), tabs=[tab("Components", "line_ids"), tab("Operations", "operation_ids")])]),
    "search": search(fields=["name", "product_id.name"]),
})
model("mrp.bom.line", "BoM Component", [
    F.m2o("bom_id", "BoM", "mrp.bom", required=True, ondelete="CASCADE"), F.m2o("product_id", "Component", "product.product", required=True), F.float("product_qty", "Quantity", default=1),
], access={"mrp.user": "r", "mrp.manager": "rwcd", "base.admin": "rwcd"}, rec_name="id", order="id ASC", parent="bom_id")
model("mrp.bom.operation", "BoM Operation", [
    F.m2o("bom_id", "BoM", "mrp.bom", required=True, ondelete="CASCADE"), F.char("name", "Operation", required=True),
    F.m2o("workcenter_id", "Work Center", "mrp.workcenter", required=True), F.float("duration_minutes", "Duration (min)", default=10),
], access={"mrp.user": "r", "mrp.manager": "rwcd", "base.admin": "rwcd"}, order="id ASC", parent="bom_id")

production = model("mrp.production", "Manufacturing Order", [
    F.char("name", "Reference", readonly=True, sequence="mrp.production"),
    F.m2o("product_id", "Product", "product.product", required=True, tracked=True, onchange=True),
    F.m2o("bom_id", "Bill of Materials", "mrp.bom"),
    F.float("product_qty", "Quantity", default=1, tracked=True),
    F.sel("state", "Status", [("draft", "Draft"), ("confirmed", "Confirmed"), ("progress", "In Progress"), ("done", "Done"), ("cancel", "Cancelled")], readonly=True, default="draft", tracked=True),
    F.date("date_start", "Scheduled Date"), F.date("date_finished", "Finished", readonly=True),
    F.m2o("user_id", "Responsible", "res.users", default=lambda env: env.user.id or None, ondelete="SET NULL"),
    F.char("origin", "Source"),
    F.o2m("component_ids", "Components", "mrp.production.component", "production_id", columns=["product_id", "required_qty", "consumed_qty", "available_qty"]),
    F.o2m("workorder_ids", "Work Orders", "mrp.workorder", "production_id", columns=["name", "workcenter_id", "duration_expected", "duration_real", "state"]),
], access=MRP, mail=True, order="id DESC", locked_when=[["state", "in", ["done", "cancel"]]], views={
    "kanban": kanban(group_by="state", title="name", subtitle="product_id", fields=["product_qty"], date="date_start", user="user_id"),
    "list": list_view("name", "product_id", "product_qty", "date_start", "user_id", {"field": "state", "widget": "badge"}),
    "form": form(status="state", chatter=True, buttons=[
        btn("confirm", "Confirm", primary=True, when=[["state", "=", "draft"]]),
        btn("start", "Start", when=[["state", "=", "confirmed"]]),
        btn("produce", "Produce All", primary=True, when=[["state", "in", ["confirmed", "progress"]]]),
        btn("cancel", "Cancel", when=[["state", "in", ["draft", "confirmed", "progress"]]]),
    ], smart_buttons=[smart("Moves", "arrow-left-right", "stock.move", [["reference", "=", "$name"]])], sections=[section(
        col("product_id", "bom_id", "product_qty"), col("date_start", "date_finished", "user_id", "origin"),
        tabs=[tab("Components", {"field": "component_ids", "readonly": [["state", "!=", "draft"]]}), tab("Work Orders", {"field": "workorder_ids", "readonly": [["state", "!=", "draft"]]})])]),
    "graph": graph("product_id", ["product_qty"]),
    "search": search(fields=["name", "product_id.name", "origin"], filters=[
        flt("todo", "To Do", [["state", "in", ["confirmed", "progress"]]]), flt("done", "Done", [["state", "=", "done"]]),
    ], group_by=["product_id", "state"]),
})
model("mrp.production.component", "Component", [
    F.m2o("production_id", "Order", "mrp.production", required=True, ondelete="CASCADE"),
    F.m2o("product_id", "Component", "product.product", required=True),
    F.float("required_qty", "To Consume", default=1), F.float("consumed_qty", "Consumed", readonly=True, default=0),
    F.sql("available_qty", "On Hand", "float", "SELECT COALESCE(SUM(q.quantity), 0) FROM stock_quant q JOIN stock_location l ON l.id = q.location_id WHERE q.product_id = {t}.product_id AND l.usage = 'internal'"),
], access=MRP, rec_name="id", order="id ASC", parent="production_id")
workorder = model("mrp.workorder", "Work Order", [
    F.m2o("production_id", "Manufacturing Order", "mrp.production", required=True, ondelete="CASCADE"),
    F.char("name", "Operation", required=True), F.m2o("workcenter_id", "Work Center", "mrp.workcenter", required=True),
    F.float("duration_expected", "Expected (min)", default=0), F.float("duration_real", "Real (min)", readonly=True, default=0),
    F.datetime("started_at", "Started", readonly=True),
    F.sel("state", "Status", [("pending", "Pending"), ("progress", "In Progress"), ("done", "Finished")], readonly=True, default="pending"),
], access=MRP, order="id ASC", parent="production_id", views={
    "kanban": kanban(group_by="workcenter_id", title="name", subtitle="production_id", badge="state", fields=["duration_expected"]),
    "list": list_view("production_id", "name", "workcenter_id", "duration_expected", "duration_real", {"field": "state", "widget": "badge"}),
    "form": form(status="state", buttons=[btn("start", "Start", primary=True, when=[["state", "=", "pending"]]), btn("finish", "Finish", primary=True, when=[["state", "=", "progress"]])],
                 sections=[section(col("production_id", "workcenter_id"), col("duration_expected", "duration_real", "started_at"))]),
    "search": search(fields=["name", "production_id.name"], filters=[flt("open", "Open", [["state", "!=", "done"]])], group_by=["workcenter_id", "state"]),
})


@production.set_onchange
def _onchange(env, values, changed, parent=None):
    if changed == "product_id" and values.get("product_id"):
        pid = values["product_id"][0] if isinstance(values["product_id"], (list, tuple)) else values["product_id"]
        boms = env.sudo()["mrp.bom"].search([["product_id", "=", pid]], limit=1)
        if boms:
            return {"bom_id": [boms[0], env.sudo()["mrp.bom"].get(boms[0], ["name"])["name"]]}
    return {}


@production.action("confirm", "Confirm", groups=["mrp.user", "mrp.manager"])
def action_confirm(env, ids, params):
    env = env.sudo()
    for rec in env["mrp.production"].read(ids):
        if rec["state"] != "draft":
            raise UserError("Only draft orders can be confirmed.")
        bom_id = rec["bom_id"]
        if not bom_id:
            found = env["mrp.bom"].search([["product_id", "=", rec["product_id"]]], limit=1)
            bom_id = found[0] if found else None
        env.cur.execute("SELECT COUNT(*) FROM mrp_production_component WHERE production_id = %s", (rec["id"],))
        has_components = env.cur.fetchone()[0] > 0
        if not bom_id and not has_components:
            raise UserError("This product has no bill of materials. Add components or create a BoM.")
        if bom_id:
            b = env["mrp.bom"].get(bom_id, ["product_qty"])
            factor = dec(rec["product_qty"]) / (dec(b["product_qty"]) or 1)
            if not has_components:
                env.cur.execute("SELECT product_id, product_qty FROM mrp_bom_line WHERE bom_id = %s", (bom_id,))
                for product_id, qty in env.cur.fetchall():
                    env["mrp.production.component"].create({"production_id": rec["id"], "product_id": product_id, "required_qty": dec(qty) * factor})
            env.cur.execute("SELECT name, workcenter_id, duration_minutes FROM mrp_bom_operation WHERE bom_id = %s ORDER BY id", (bom_id,))
            for name, wc, minutes in env.cur.fetchall():
                env["mrp.workorder"].create({"production_id": rec["id"], "name": name, "workcenter_id": wc, "duration_expected": dec(minutes) * factor})
        env["mrp.production"].write([rec["id"]], {"state": "confirmed", "bom_id": bom_id})


@production.action("start", "Start", groups=["mrp.user", "mrp.manager"])
def action_start(env, ids, params):
    env.sudo()["mrp.production"].write(ids, {"state": "progress"})


@production.action("produce", "Produce All", groups=["mrp.user", "mrp.manager"])
def action_produce(env, ids, params):
    env = env.sudo()
    prod_loc = location_by_usage(env, "production")
    stock = main_stock(env)
    for rec in env["mrp.production"].read(ids):
        if rec["state"] not in ("confirmed", "progress"):
            raise UserError("This order is not ready to produce.")
        env.cur.execute("SELECT id, product_id, required_qty FROM mrp_production_component WHERE production_id = %s", (rec["id"],))
        for cid, product_id, qty in env.cur.fetchall():
            apply_move(env, product_id, stock, prod_loc, qty, reference=rec["name"])
            env.cur.execute("UPDATE mrp_production_component SET consumed_qty = %s WHERE id = %s", (qty, cid))
        apply_move(env, rec["product_id"], prod_loc, stock, rec["product_qty"], reference=rec["name"])
        env.cur.execute("UPDATE mrp_workorder SET state = 'done', duration_real = CASE WHEN duration_real = 0 THEN duration_expected ELSE duration_real END WHERE production_id = %s", (rec["id"],))
        env["mrp.production"].write([rec["id"]], {"state": "done", "date_finished": datetime.now(timezone.utc).date()})
        post_note(env, "mrp.production", rec["id"], f"Produced {rec['product_qty']:g} units.")


@production.action("cancel", "Cancel", groups=["mrp.user", "mrp.manager"])
def action_cancel(env, ids, params):
    env.sudo()["mrp.production"].write(ids, {"state": "cancel"})


@workorder.action("start", "Start", groups=["mrp.user", "mrp.manager"])
def wo_start(env, ids, params):
    env.sudo()["mrp.workorder"].write(ids, {"state": "progress", "started_at": datetime.now(timezone.utc)})
    env.cur.execute("UPDATE mrp_production SET state = 'progress' WHERE state = 'confirmed' AND id IN (SELECT production_id FROM mrp_workorder WHERE id = ANY(%s))", (list(ids),))


@workorder.action("finish", "Finish", groups=["mrp.user", "mrp.manager"])
def wo_finish(env, ids, params):
    env = env.sudo()
    for rec in env["mrp.workorder"].read(ids, ["id", "started_at"]):
        minutes = (datetime.now(timezone.utc) - rec["started_at"]).total_seconds() / 60 if rec["started_at"] else 0
        env["mrp.workorder"].write([rec["id"]], {"state": "done", "duration_real": round(minutes, 2)})


app("manufacturing", "Manufacturing", "factory", "#8a6d2f", [
    menu("orders", "Manufacturing Orders", "mrp.production", ("list", "kanban", "form", "graph")),
    menu("workorders", "Work Orders", "mrp.workorder", ("kanban", "list", "form"), [["state", "!=", "done"]]),
    menu("boms", "Bills of Materials", "mrp.bom"),
    menu("products", "Products", "product.product", ("kanban", "list", "form")),
    menu("config", "Configuration", children=[menu("workcenters", "Work Centers", "mrp.workcenter")]),
], groups=["mrp.user", "mrp.manager"], sequence=80)
