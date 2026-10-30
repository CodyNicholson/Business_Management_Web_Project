"""Helpdesk tickets with priorities and SLA deadlines."""
from datetime import datetime, timedelta, timezone

from ..orm import F, app, menu, model
from ..views import btn, col, flt, form, graph, kanban, list_view, pivot, search, section, tab

HD = {"helpdesk.user": "rwc", "helpdesk.manager": "rwcd", "base.admin": "rwcd"}
team = model("helpdesk.team", "Team", [
    F.char("name", "Team", required=True), F.int("sla_hours_normal", "SLA Hours: Normal", default=48), F.int("sla_hours_urgent", "SLA Hours: Urgent", default=8),
    F.sql("open_tickets", "Open Tickets", "int", "SELECT COUNT(*) FROM helpdesk_ticket k LEFT JOIN helpdesk_stage s ON s.id = k.stage_id WHERE k.team_id = {t}.id AND s.is_closed IS NOT TRUE"),
], access={"helpdesk.user": "r", "helpdesk.manager": "rwcd", "base.admin": "rwcd"}, order="name ASC", views={
    "kanban": kanban(title="name", fields=["open_tickets", "sla_hours_normal"]), "list": list_view("name", "open_tickets", "sla_hours_normal", "sla_hours_urgent"),
    "form": form(sections=[section(col("sla_hours_normal", "sla_hours_urgent"))]),
})
stage = model("helpdesk.stage", "Stage", [F.char("name", "Stage", required=True), F.int("sequence", "Sequence", default=10), F.bool("is_closed", "Closing Stage", default=False)],
              access={"helpdesk.user": "r", "helpdesk.manager": "rwcd", "base.admin": "rwcd"}, order="sequence ASC", views={"list": list_view("sequence", "name", "is_closed"), "form": form(sections=[section(col("sequence", "is_closed"))])})


def _first_stage(env):
    ids = env.sudo()["helpdesk.stage"].search([], limit=1)
    return ids[0] if ids else None


ticket = model("helpdesk.ticket", "Ticket", [
    F.char("number", "Ticket", readonly=True, sequence="helpdesk.ticket"), F.char("name", "Subject", required=True, tracked=True),
    F.m2o("team_id", "Team", "helpdesk.team", tracked=True), F.m2o("user_id", "Assigned to", "res.users", tracked=True, ondelete="SET NULL"),
    F.m2o("partner_id", "Customer", "res.partner", ondelete="SET NULL"), F.char("partner_email", "Customer Email"),
    F.m2o("stage_id", "Stage", "helpdesk.stage", default=_first_stage, tracked=True),
    F.sel("priority", "Priority", [("0", "Low"), ("1", "Medium"), ("2", "High"), ("3", "Urgent")], default="0", tracked=True),
    F.datetime("sla_deadline", "SLA Deadline", readonly=True), F.datetime("closed_at", "Closed on", readonly=True), F.text("description", "Description"),
    F.sql("sla_failed", "SLA Failed", "bool", "SELECT {t}.sla_deadline IS NOT NULL AND COALESCE({t}.closed_at, now()) > {t}.sla_deadline"),
], access=HD, mail=True, views={
    "kanban": kanban(group_by="stage_id", title="name", subtitle="partner_id", priority="priority", user="user_id", date="sla_deadline", badge="number"),
    "list": list_view("number", "name", "team_id", "user_id", "partner_id", "priority", "sla_deadline", "sla_failed", "stage_id"),
    "form": form(status="stage_id", status_clickable=True, chatter=True, buttons=[btn("assign_me", "Assign to Me", when=[["user_id", "=", False]])], sections=[section(
        col("team_id", "user_id", "partner_id", "partner_email"), col({"field": "priority", "widget": "priority"}, "number", "sla_deadline", "sla_failed", "closed_at"), tabs=[tab("Description", "description")])]),
    "graph": graph("stage_id"), "pivot": pivot("team_id", "stage_id"),
    "search": search(fields=["name", "number", "partner_id.name", "partner_email"], filters=[
        flt("my", "My Tickets", [["user_id", "=", "$uid"]]), flt("unassigned", "Unassigned", [["user_id", "=", False]]),
        flt("open", "Open", [["stage_id.is_closed", "=", False]]), flt("sla", "SLA Failed", [["sla_failed", "=", True]]),
    ], group_by=["team_id", "user_id", "stage_id", "priority"]),
})


def _deadline(env, team_id, priority):
    if not team_id:
        return None
    t = env.sudo()["helpdesk.team"].get(team_id, ["sla_hours_normal", "sla_hours_urgent"])
    hours = t["sla_hours_urgent"] if priority in ("2", "3") else t["sla_hours_normal"]
    return datetime.now(timezone.utc) + timedelta(hours=hours or 48)


@ticket.hook("prepare_create")
def _prepare(env, vals):
    vals["sla_deadline"] = _deadline(env, vals.get("team_id"), vals.get("priority", "0"))
    return vals


@ticket.hook("after_write")
def _after_write(env, ids, vals):
    su = env.sudo()
    if "stage_id" in vals:
        closed = bool(vals["stage_id"]) and su["helpdesk.stage"].get(vals["stage_id"], ["is_closed"])["is_closed"]
        env.cur.execute("UPDATE helpdesk_ticket SET closed_at = CASE WHEN %s THEN COALESCE(closed_at, now()) ELSE NULL END WHERE id = ANY(%s)", (closed, list(ids)))
    if "team_id" in vals or "priority" in vals:
        for rec in su["helpdesk.ticket"].read(ids, ["id", "team_id", "priority"]):
            env.cur.execute("UPDATE helpdesk_ticket SET sla_deadline = %s WHERE id = %s", (_deadline(env, rec["team_id"], rec["priority"]), rec["id"]))


@ticket.action("assign_me", "Assign to Me", groups=["helpdesk.user", "helpdesk.manager"])
def assign_me(env, ids, params):
    env.sudo()["helpdesk.ticket"].write(ids, {"user_id": env.user.id})


app("helpdesk", "Helpdesk", "life-buoy", "#b3543c", [
    menu("teams", "Overview", "helpdesk.team", ("kanban", "list", "form")),
    menu("tickets", "Tickets", "helpdesk.ticket", ("kanban", "list", "form", "graph", "pivot")),
    menu("my", "My Tickets", "helpdesk.ticket", ("kanban", "list", "form"), [["user_id", "=", "$uid"]]),
    menu("config", "Configuration", groups=["helpdesk.manager"], children=[menu("stages", "Stages", "helpdesk.stage")]),
], groups=["helpdesk.user", "helpdesk.manager"], sequence=140)
