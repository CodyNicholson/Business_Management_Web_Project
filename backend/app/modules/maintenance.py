"""Equipment and maintenance requests."""
from ..orm import F, app, menu, model
from ..views import col, flt, form, graph, kanban, list_view, search, section, smart, tab

MT = {"base.user": "r", "maintenance.user": "rwcd", "base.admin": "rwcd"}
equipment = model("maintenance.equipment", "Equipment", [
    F.char("name", "Equipment", required=True, tracked=True), F.char("serial_no", "Serial Number"), F.char("location", "Location"), F.m2o("employee_id", "Assigned to", "hr.employee", ondelete="SET NULL", tracked=True),
    F.date("assign_date", "Assigned Date"), F.money("cost", "Cost", default=0), F.int("maintenance_days", "Preventive every (days)", default=0), F.text("note", "Note"), F.bool("active", "Active", default=True),
    F.sql("request_count", "Requests", "int", "SELECT COUNT(*) FROM maintenance_request r WHERE r.equipment_id = {t}.id"),
], access=MT, mail=True, order="name ASC", views={
    "kanban": kanban(title="name", subtitle="location", fields=["employee_id", "request_count"]), "list": list_view("name", "serial_no", "location", "employee_id", "request_count"),
    "form": form(chatter=True, smart_buttons=[smart("Requests", "wrench", "maintenance.request", [["equipment_id", "=", "$id"]])], sections=[section(col("serial_no", "location", "employee_id"), col("assign_date", "cost", "maintenance_days"), tabs=[tab("Note", "note")])]),
    "search": search(fields=["name", "serial_no", "location"], group_by=["location", "employee_id"]),
})
stage = model("maintenance.stage", "Stage", [F.char("name", "Stage", required=True), F.int("sequence", "Sequence", default=10), F.bool("done", "Request Done", default=False)],
              access=MT, order="sequence ASC", views={"list": list_view("sequence", "name", "done"), "form": form(sections=[section(col("sequence", "done"))])})


def _first_stage(env):
    ids = env.sudo()["maintenance.stage"].search([], limit=1)
    return ids[0] if ids else None


request = model("maintenance.request", "Maintenance Request", [
    F.char("name", "Subject", required=True, tracked=True), F.m2o("equipment_id", "Equipment", "maintenance.equipment", ondelete="SET NULL", tracked=True), F.m2o("user_id", "Technician", "res.users", ondelete="SET NULL", tracked=True),
    F.m2o("stage_id", "Stage", "maintenance.stage", default=_first_stage, tracked=True), F.sel("kind", "Type", [("corrective", "Corrective"), ("preventive", "Preventive")], default="corrective"),
    F.sel("priority", "Priority", [("0", "Low"), ("1", "Normal"), ("2", "High"), ("3", "Very High")], default="0"), F.date("schedule_date", "Scheduled Date"), F.float("duration", "Duration (hours)", default=0), F.text("description", "Description"),
], access={"base.user": "rwc", "maintenance.user": "rwcd", "base.admin": "rwcd"}, mail=True, views={
    "kanban": kanban(group_by="stage_id", title="name", subtitle="equipment_id", priority="priority", user="user_id", date="schedule_date"), "list": list_view("name", "equipment_id", "kind", "user_id", "schedule_date", "stage_id"),
    "form": form(status="stage_id", status_clickable=True, chatter=True, sections=[section(col("equipment_id", "kind", {"field": "priority", "widget": "priority"}), col("user_id", "schedule_date", "duration"), tabs=[tab("Description", "description")])]),
    "calendar": {"start": "schedule_date", "stop": None, "title": "name"}, "graph": graph("stage_id"),
    "search": search(fields=["name", "equipment_id.name"], filters=[flt("my", "My Requests", [["user_id", "=", "$uid"]]), flt("open", "Open", [["stage_id.done", "=", False]])], group_by=["stage_id", "equipment_id", "user_id", "kind"]),
})
app("maintenance", "Maintenance", "wrench", "#6b6f3a", [
    menu("requests", "Requests", "maintenance.request", ("kanban", "list", "form", "calendar", "graph")),
    menu("equipment", "Equipment", "maintenance.equipment", ("kanban", "list", "form")),
    menu("config", "Configuration", groups=["maintenance.user"], children=[menu("stages", "Stages", "maintenance.stage")]),
], sequence=180)
