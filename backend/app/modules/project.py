"""Projects, tasks and timesheets."""
from datetime import date

from ..orm import F, app, menu, model
from ..views import col, flt, form, graph, kanban, list_view, pivot, search, section, smart, tab

PROJECT = {"base.user": "r", "project.user": "rwc", "project.manager": "rwcd", "base.admin": "rwcd"}
stage = model("project.task.stage", "Task Stage", [F.char("name", "Name", required=True), F.int("sequence", "Sequence", default=10), F.bool("fold", "Closed Stage", default=False)],
              access={"base.user": "r", "project.manager": "rwcd", "base.admin": "rwcd"}, order="sequence ASC",
              views={"list": list_view("sequence", "name", "fold"), "form": form(sections=[section(col("sequence", "fold"))])})
tag = model("project.tag", "Project Tag", [F.char("name", "Name", required=True)], access={"base.user": "r", "project.user": "rwc", "project.manager": "rwcd", "base.admin": "rwcd"}, order="name ASC",
            views={"list": list_view("name"), "form": form()})

project = model("project.project", "Project", [
    F.char("name", "Project Name", required=True, tracked=True), F.m2o("partner_id", "Customer", "res.partner", ondelete="SET NULL"),
    F.m2o("user_id", "Project Manager", "res.users", default=lambda env: env.user.id or None, ondelete="SET NULL", tracked=True),
    F.date("date_start", "Start Date"), F.date("date", "Expiration Date"), F.text("description", "Description"),
    F.bool("active", "Active", default=True),
    F.sql("task_count", "Open Tasks", "int", "SELECT COUNT(*) FROM project_task k LEFT JOIN project_task_stage s ON s.id = k.stage_id WHERE k.project_id = {t}.id AND s.fold IS NOT TRUE AND k.active IS TRUE"),
    F.sql("hours_logged", "Hours Logged", "float", "SELECT COALESCE(SUM(h.unit_amount), 0) FROM hr_timesheet h WHERE h.project_id = {t}.id"),
], access=PROJECT, mail=True, order="name ASC", views={
    "kanban": kanban(title="name", subtitle="partner_id", user="user_id", fields=["task_count", "hours_logged"], date="date"),
    "list": list_view("name", "partner_id", "user_id", "task_count", "hours_logged", "date"),
    "form": form(chatter=True, smart_buttons=[smart("Tasks", "check-square", "project.task", [["project_id", "=", "$id"]]), smart("Timesheets", "clock", "hr.timesheet", [["project_id", "=", "$id"]])],
                 sections=[section(col("partner_id", "user_id"), col("date_start", "date", "task_count", "hours_logged"), tabs=[tab("Description", "description")])]),
    "search": search(fields=["name", "partner_id.name"], filters=[flt("my", "My Projects", [["user_id", "=", "$uid"]])], group_by=["user_id", "partner_id"]),
})


def _first_stage(env):
    ids = env.sudo()["project.task.stage"].search([], limit=1)
    return ids[0] if ids else None


task = model("project.task", "Task", [
    F.char("name", "Task Title", required=True, tracked=True),
    F.m2o("project_id", "Project", "project.project", tracked=True, ondelete="CASCADE"),
    F.m2o("stage_id", "Stage", "project.task.stage", default=_first_stage, tracked=True),
    F.m2m("user_ids", "Assignees", "res.users", "project_task_users"), F.m2m("tag_ids", "Tags", "project.tag", "project_task_tags"),
    F.date("date_deadline", "Deadline", tracked=True), F.float("allocated_hours", "Allocated Hours", default=0),
    F.sel("priority", "Priority", [("0", "Normal"), ("1", "Important")], default="0"),
    F.m2o("parent_id", "Parent Task", "project.task", ondelete="SET NULL"), F.text("description", "Description"),
    F.o2m("timesheet_ids", "Timesheets", "hr.timesheet", "task_id", columns=["date", "employee_id", "name", "unit_amount"]),
    F.bool("active", "Active", default=True),
    F.sql("effective_hours", "Hours Spent", "float", "SELECT COALESCE(SUM(h.unit_amount), 0) FROM hr_timesheet h WHERE h.task_id = {t}.id"),
], access=PROJECT, mail=True, order="priority DESC, id DESC", views={
    "kanban": kanban(group_by="stage_id", title="name", subtitle="project_id", priority="priority", date="date_deadline", fields=["effective_hours"]),
    "list": list_view("name", "project_id", "stage_id", "user_ids", "date_deadline", "allocated_hours", "effective_hours"),
    "form": form(status="stage_id", status_clickable=True, chatter=True, sections=[section(col("project_id", "user_ids", "tag_ids", "parent_id"), col({"field": "priority", "widget": "priority"}, "date_deadline", "allocated_hours", "effective_hours"),
                 tabs=[tab("Description", "description"), tab("Timesheets", "timesheet_ids")])]),
    "calendar": {"start": "date_deadline", "stop": None, "title": "name"},
    "graph": graph("stage_id"), "pivot": pivot("project_id", "stage_id", ["allocated_hours", "effective_hours"]),
    "search": search(fields=["name", "project_id.name"], filters=[
        flt("my", "My Tasks", [["user_ids", "=", "$uid"]]), flt("late", "Late", [["date_deadline", "<", "$today"], ["stage_id.fold", "=", False]]),
    ], group_by=["project_id", "stage_id", "date_deadline:month"]),
})


def _own_timesheet(env):
    return [["employee_id.user_id", "=", env.user.id]]


def _my_employee(env):
    if not env.user.id:
        return None
    env.cur.execute("SELECT id FROM hr_employee WHERE user_id = %s AND active IS TRUE LIMIT 1", (env.user.id,))
    row = env.cur.fetchone()
    return row[0] if row else None


timesheet = model("hr.timesheet", "Timesheet", [
    F.date("date", "Date", required=True, default=lambda env: date.today()),
    F.m2o("employee_id", "Employee", "hr.employee", required=True, default=_my_employee),
    F.m2o("project_id", "Project", "project.project", required=True), F.m2o("task_id", "Task", "project.task", ondelete="SET NULL"),
    F.char("name", "Description"), F.float("unit_amount", "Hours", default=0),
], access={"base.user": "rwcd"}, rule=_own_timesheet, rule_bypass=["project.manager", "hr.manager"], rec_name="id", order="date DESC, id DESC",
    constraints=[("hours_range", "CHECK (unit_amount >= 0 AND unit_amount <= 24)")], views={
    "list": list_view("date", "employee_id", "project_id", "task_id", "name", {"field": "unit_amount", "sum": True}),
    "form": form(title=None, sections=[section(col("date", "employee_id"), col("project_id", "task_id", "unit_amount", "name"))]),
    "graph": graph("project_id", ["unit_amount"]), "pivot": pivot("employee_id", "date:week", ["unit_amount"]),
    "calendar": {"start": "date", "stop": None, "title": "name"},
    "search": search(fields=["name", "project_id.name", "employee_id.name"], filters=[flt("mine", "My Timesheets", [["employee_id.user_id", "=", "$uid"]])], group_by=["project_id", "employee_id", "date:week"]),
})

@timesheet.hook("prepare_create")
def _timesheet_project(env, vals):
    # Logged from a task's form, the project comes from the task.
    if vals.get("task_id") and not vals.get("project_id"):
        vals["project_id"] = env.sudo()["project.task"].get(vals["task_id"], ["project_id"])["project_id"]
    return vals


app("project", "Project", "kanban", "#3b6fb5", [
    menu("projects", "Projects", "project.project", ("kanban", "list", "form")),
    menu("tasks", "All Tasks", "project.task", ("kanban", "list", "form", "calendar", "graph", "pivot")),
    menu("my_tasks", "My Tasks", "project.task", ("kanban", "list", "form", "calendar"), [["user_ids", "=", "$uid"]]),
    menu("config", "Configuration", children=[menu("stages", "Task Stages", "project.task.stage"), menu("tags", "Tags", "project.tag")]),
], sequence=90)
app("timesheets", "Timesheets", "clock", "#4c7f4f", [
    menu("mine", "My Timesheets", "hr.timesheet", ("list", "form", "calendar", "graph", "pivot"), [["employee_id.user_id", "=", "$uid"]]),
    menu("all", "All Timesheets", "hr.timesheet", ("list", "form", "graph", "pivot"), groups=["project.manager", "hr.manager"]),
], sequence=95)
