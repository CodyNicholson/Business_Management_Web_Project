"""CRM pipeline: leads and opportunities moving through stages, converted into quotations."""
from datetime import date

from ..orm import F, UserError, app, menu, model
from ..views import btn, col, flt, form, graph, kanban, list_view, pivot, search, section, smart, tab

CRM = {"crm.user": "rwc", "crm.manager": "rwcd", "sales.manager": "r", "base.admin": "rwcd"}
stage = model("crm.stage", "Stage", [
    F.char("name", "Stage Name", required=True), F.int("sequence", "Sequence", default=10),
    F.bool("is_won", "Is Won Stage", default=False),
], access={"base.user": "r", "crm.manager": "rwcd", "base.admin": "rwcd"}, order="sequence ASC", views={
    "list": list_view("sequence", "name", "is_won"), "form": form(sections=[section(col("sequence", "is_won"))]),
})
lost_reason = model("crm.lost.reason", "Lost Reason", [F.char("name", "Reason", required=True)],
                    access={"base.user": "r", "crm.manager": "rwcd", "base.admin": "rwcd"}, order="name ASC",
                    views={"list": list_view("name"), "form": form()})
lead_tag = model("crm.tag", "Tag", [F.char("name", "Tag", required=True)], access={"base.user": "r", "crm.user": "rwc", "crm.manager": "rwcd", "base.admin": "rwcd"}, order="name ASC",
                 views={"list": list_view("name"), "form": form()})


def _first_stage(env):
    ids = env.sudo()["crm.stage"].search([], limit=1)
    return ids[0] if ids else None


lead = model("crm.lead", "Opportunity", [
    F.char("name", "Opportunity", required=True, tracked=True),
    F.m2o("partner_id", "Customer", "res.partner", tracked=True),
    F.char("contact_name", "Contact Name"), F.char("email_from", "Email"), F.char("phone", "Phone"),
    F.m2o("stage_id", "Stage", "crm.stage", default=_first_stage, tracked=True),
    F.m2o("user_id", "Salesperson", "res.users", default=lambda env: env.user.id or None, tracked=True, ondelete="SET NULL"),
    F.money("expected_revenue", "Expected Revenue", default=0, tracked=True),
    F.float("probability", "Probability (%)", default=10),
    F.date("date_deadline", "Expected Closing", tracked=True),
    F.sel("priority", "Priority", [("0", "Low"), ("1", "Medium"), ("2", "High"), ("3", "Very High")], default="0"),
    F.sel("status", "Status", [("open", "Open"), ("won", "Won"), ("lost", "Lost")], readonly=True, default="open", tracked=True),
    F.m2o("lost_reason_id", "Lost Reason", "crm.lost.reason", readonly=True, ondelete="SET NULL"),
    F.m2m("tag_ids", "Tags", "crm.tag", "crm_lead_tags"),
    F.text("description", "Internal Notes"),
    F.sql("weighted_revenue", "Weighted Revenue", "money", "SELECT {t}.expected_revenue * {t}.probability / 100"),
    F.bool("active", "Active", default=True),
], access=CRM, mail=True, order="id DESC", search_fields=["name", "contact_name", "email_from"], views={
    "kanban": kanban(group_by="stage_id", title="name", subtitle="partner_id", amount="expected_revenue", user="user_id", priority="priority", date="date_deadline"),
    "list": list_view("name", "partner_id", "contact_name", "stage_id", "user_id", {"field": "expected_revenue", "sum": True}, "probability", "date_deadline", {"field": "status", "widget": "badge"}),
    "form": form(status="stage_id", status_clickable=True, chatter=True, buttons=[
        btn("new_quotation", "New Quotation", primary=True, when=[["status", "=", "open"]]),
        btn("won", "Won", when=[["status", "=", "open"]]),
        btn("lost", "Lost", when=[["status", "=", "open"]]),
        btn("restore", "Restore", when=[["status", "!=", "open"]]),
    ], smart_buttons=[smart("Quotations", "shopping-cart", "sale.order", [["opportunity_id", "=", "$id"]])], sections=[section(
        col("partner_id", "contact_name", "email_from", "phone"),
        col("expected_revenue", "probability", "user_id", "date_deadline", {"field": "priority", "widget": "priority"}, "tag_ids", "status", "lost_reason_id"),
        tabs=[tab("Internal Notes", "description")])]),
    "calendar": {"start": "date_deadline", "stop": None, "title": "name"},
    "graph": graph("stage_id", ["expected_revenue"]),
    "pivot": pivot("stage_id", "user_id", ["expected_revenue", "weighted_revenue"]),
    "search": search(fields=["name", "partner_id.name", "contact_name", "email_from"], filters=[
        flt("my", "My Pipeline", [["user_id", "=", "$uid"]]), flt("open", "Open", [["status", "=", "open"]]),
        flt("won", "Won", [["status", "=", "won"]]), flt("lost", "Lost", [["status", "=", "lost"]]),
        flt("overdue", "Overdue", [["status", "=", "open"], ["date_deadline", "<", "$today"]]),
    ], group_by=["stage_id", "user_id", "partner_id", "date_deadline:month"]),
})


@lead.action("won", "Won", groups=["crm.user", "crm.manager"])
def action_won(env, ids, params):
    env = env.sudo()
    vals = {"status": "won", "probability": 100}
    won = env["crm.stage"].search([["is_won", "=", True]], limit=1)
    if won:
        vals["stage_id"] = won[0]
    env["crm.lead"].write(ids, vals)


@lead.action("lost", "Lost", groups=["crm.user", "crm.manager"], params=[
    {"name": "lost_reason_id", "label": "Lost Reason", "type": "many2one", "comodel": "crm.lost.reason"}])
def action_lost(env, ids, params):
    reason = params.get("lost_reason_id")
    reason = reason[0] if isinstance(reason, (list, tuple)) else reason
    env.sudo()["crm.lead"].write(ids, {"status": "lost", "probability": 0, "lost_reason_id": reason or None})


@lead.action("restore", "Restore", groups=["crm.user", "crm.manager"])
def action_restore(env, ids, params):
    env.sudo()["crm.lead"].write(ids, {"status": "open", "lost_reason_id": None, "probability": 10})


@lead.action("new_quotation", "New Quotation", groups=["sales.user", "sales.manager"])
def action_quotation(env, ids, params):
    env = env.sudo()
    rec = env["crm.lead"].get(ids[0])
    if not rec["partner_id"]:
        raise UserError("Set a customer on the opportunity first.")
    order_id = env["sale.order"].create({"partner_id": rec["partner_id"], "opportunity_id": ids[0], "user_id": rec["user_id"]})
    return {"type": "open", "model": "sale.order", "id": order_id}


app("crm", "CRM", "target", "#22847c", [
    menu("pipeline", "Pipeline", "crm.lead", ("kanban", "list", "form", "calendar", "graph", "pivot"), [["status", "=", "open"]]),
    menu("all", "All Opportunities", "crm.lead", ("list", "form", "graph", "pivot")),
    menu("customers", "Customers", "res.partner", ("kanban", "list", "form"), [["is_customer", "=", True]]),
    menu("config", "Configuration", children=[menu("stages", "Stages", "crm.stage"), menu("tags", "Tags", "crm.tag"), menu("reasons", "Lost Reasons", "crm.lost.reason")]),
], groups=["crm.user", "crm.manager"], sequence=30)
