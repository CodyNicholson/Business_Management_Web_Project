"""Employees, time off, attendance, expenses, recruitment and a simple payroll."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from ..orm import F, UserError, app, dec, menu, model, money, post_note
from ..views import btn, col, flt, form, graph, kanban, list_view, pivot, search, section, smart, tab
from .account import get_account, journal_of, post_move
from .project import _my_employee

HR_OFFICER = {"hr.user": "rwc", "hr.manager": "rwcd", "base.admin": "rwcd"}
HR_BYPASS = ["hr.user", "hr.manager"]
HR_PRIVATE = ["hr.user", "hr.manager"]
PAYROLL = {"hr.manager": "rwcd", "base.admin": "rwcd"}


def _own(env):
    return [["employee_id.user_id", "=", env.user.id]]


department = model("hr.department", "Department", [
    F.char("name", "Department", required=True), F.m2o("manager_id", "Manager", "hr.employee", ondelete="SET NULL"),
    F.m2o("parent_id", "Parent", "hr.department", ondelete="SET NULL"),
    F.sql("employee_count", "Employees", "int", "SELECT COUNT(*) FROM hr_employee e WHERE e.department_id = {t}.id AND e.active IS TRUE"),
], access={"base.user": "r", **HR_OFFICER}, order="name ASC", views={
    "kanban": kanban(title="name", subtitle="manager_id", fields=["employee_count"]),
    "list": list_view("name", "manager_id", "parent_id", "employee_count"), "form": form(sections=[section(col("manager_id", "parent_id"))]),
})
job = model("hr.job", "Job Position", [
    F.char("name", "Job Position", required=True), F.m2o("department_id", "Department", "hr.department", ondelete="SET NULL"),
    F.int("no_of_recruitment", "Target Hires", default=1), F.text("description", "Job Description"),
    F.sel("state", "Status", [("open", "Recruiting"), ("closed", "Not Recruiting")], default="open"),
    F.sql("applicant_count", "Applications", "int", "SELECT COUNT(*) FROM hr_applicant a WHERE a.job_id = {t}.id AND a.active IS TRUE"),
], access={"base.user": "r", **HR_OFFICER}, order="name ASC", views={
    "kanban": kanban(title="name", subtitle="department_id", fields=["no_of_recruitment", "applicant_count"], badge="state"),
    "list": list_view("name", "department_id", "no_of_recruitment", "applicant_count", {"field": "state", "widget": "badge"}),
    "form": form(smart_buttons=[smart("Applications", "users", "hr.applicant", [["job_id", "=", "$id"]])], sections=[section(col("department_id", "state"), col("no_of_recruitment"), tabs=[tab("Job Description", "description")])]),
})
employee = model("hr.employee", "Employee", [
    F.char("name", "Employee Name", required=True, tracked=True), F.m2o("user_id", "Related User", "res.users", ondelete="SET NULL"),
    F.m2o("department_id", "Department", "hr.department", tracked=True, ondelete="SET NULL"), F.m2o("job_id", "Job Position", "hr.job", tracked=True, ondelete="SET NULL"),
    F.m2o("parent_id", "Manager", "hr.employee", ondelete="SET NULL", tracked=True),
    F.char("work_email", "Work Email"), F.char("work_phone", "Work Phone"), F.char("work_location", "Work Location"),
    F.char("private_email", "Private Email", groups=HR_PRIVATE), F.char("private_phone", "Private Phone", groups=HR_PRIVATE), F.char("private_address", "Home Address", groups=HR_PRIVATE),
    F.date("birthday", "Date of Birth", groups=HR_PRIVATE), F.char("identification_id", "Identification No", groups=HR_PRIVATE),
    F.char("emergency_contact", "Emergency Contact", groups=HR_PRIVATE), F.char("emergency_phone", "Emergency Phone", groups=HR_PRIVATE),
    F.bool("active", "Active", default=True),
    F.sql("presence", "Presence", "selection", "SELECT CASE WHEN EXISTS (SELECT 1 FROM hr_attendance a WHERE a.employee_id = {t}.id AND a.check_out IS NULL) THEN 'present' ELSE 'absent' END",
          selection=[("present", "Present"), ("absent", "Absent")]),
], access={"base.user": "r", **HR_OFFICER}, mail=True, order="name ASC", search_fields=["name", "work_email"], views={
    "kanban": kanban(title="name", subtitle="job_id", fields=["department_id", "work_email", "work_phone"], badge="presence"),
    "list": list_view("name", "work_phone", "work_email", "department_id", "job_id", "parent_id"),
    "form": form(chatter=True, smart_buttons=[
        smart("Time Off", "palmtree", "hr.leave", [["employee_id", "=", "$id"]]), smart("Attendance", "user-check", "hr.attendance", [["employee_id", "=", "$id"]]),
        smart("Contracts", "file-signature", "hr.contract", [["employee_id", "=", "$id"]]), smart("Payslips", "banknote", "hr.payslip", [["employee_id", "=", "$id"]]),
    ], sections=[section(col("work_email", "work_phone", "work_location", "user_id"), col("department_id", "job_id", "parent_id", "presence"), tabs=[
        tab("Private Information", "private_email", "private_phone", "private_address", "birthday", "identification_id", "emergency_contact", "emergency_phone")])]),
    "search": search(fields=["name", "work_email"], filters=[flt("archived", "Archived", [["active", "=", False]])], group_by=["department_id", "job_id", "parent_id"]),
})

contract = model("hr.contract", "Contract", [
    F.char("name", "Contract Reference", required=True), F.m2o("employee_id", "Employee", "hr.employee", required=True),
    F.date("date_start", "Start Date", required=True, default=lambda env: date.today()), F.date("date_end", "End Date"),
    F.money("wage", "Monthly Wage", default=0, tracked=True), F.float("deduction_rate", "Withholding (%)", default=0, help="Flat rate applied to gross pay. This is not a statutory tax calculation."),
    F.sel("state", "Status", [("draft", "New"), ("open", "Running"), ("close", "Expired")], default="draft", tracked=True),
], access=PAYROLL, mail=True, views={
    "list": list_view("name", "employee_id", "date_start", "date_end", "wage", {"field": "state", "widget": "badge"}),
    "form": form(status="state", status_clickable=True, chatter=True, sections=[section(col("employee_id", "date_start", "date_end"), col("wage", "deduction_rate"))]),
})

leave_type = model("hr.leave.type", "Time Off Type", [
    F.char("name", "Name", required=True), F.bool("requires_allocation", "Requires Allocation", default=True),
    F.bool("active", "Active", default=True),
], access={"base.user": "r", "hr.manager": "rwcd", "base.admin": "rwcd"}, order="name ASC", views={
    "list": list_view("name", "requires_allocation"), "form": form(sections=[section(col("requires_allocation"))]),
})
allocation = model("hr.leave.allocation", "Allocation", [
    F.char("name", "Description", required=True), F.m2o("employee_id", "Employee", "hr.employee", required=True), F.m2o("leave_type_id", "Type", "hr.leave.type", required=True),
    F.float("number_of_days", "Days", default=0), F.sel("state", "Status", [("draft", "To Approve"), ("validate", "Approved"), ("refuse", "Refused")], readonly=True, default="draft", tracked=True),
], access={"base.user": "r", **HR_OFFICER}, rule=_own, rule_bypass=HR_BYPASS, mail=True, locked_when=[["state", "=", "validate"]], views={
    "list": list_view("name", "employee_id", "leave_type_id", "number_of_days", {"field": "state", "widget": "badge"}),
    "form": form(status="state", chatter=True, buttons=[btn("approve", "Approve", primary=True, when=[["state", "=", "draft"]]), btn("refuse", "Refuse", when=[["state", "=", "draft"]])],
                 sections=[section(col("employee_id", "leave_type_id"), col("number_of_days"))]),
})


def _workdays(start: date, end: date) -> int:
    days = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days


leave = model("hr.leave", "Time Off", [
    F.char("name", "Description"), F.m2o("employee_id", "Employee", "hr.employee", required=True, default=_my_employee),
    F.m2o("leave_type_id", "Time Off Type", "hr.leave.type", required=True), F.date("date_from", "From", required=True), F.date("date_to", "To", required=True),
    F.float("number_of_days", "Days", readonly=True, default=0),
    F.sel("state", "Status", [("draft", "Draft"), ("confirm", "To Approve"), ("validate", "Approved"), ("refuse", "Refused")], readonly=True, default="draft", tracked=True),
], access={"base.user": "rwcd"}, rule=_own, rule_bypass=HR_BYPASS, mail=True, rec_name="name", order="date_from DESC", locked_when=[["state", "in", ["validate"]]],
    constraints=[("dates_order", "CHECK (date_to >= date_from)")], views={
    "list": list_view("employee_id", "leave_type_id", "name", "date_from", "date_to", "number_of_days", {"field": "state", "widget": "badge"}),
    "form": form(title=None, status="state", chatter=True, buttons=[
        btn("submit", "Submit", primary=True, when=[["state", "=", "draft"]]), btn("approve", "Approve", primary=True, when=[["state", "=", "confirm"]]),
        btn("refuse", "Refuse", when=[["state", "=", "confirm"]]), btn("reset", "Reset to Draft", when=[["state", "in", ["refuse", "confirm"]]]),
    ], sections=[section(col("employee_id", "leave_type_id", "name"), col("date_from", "date_to", "number_of_days"))]),
    "calendar": {"start": "date_from", "stop": "date_to", "title": "employee_id"}, "graph": graph("leave_type_id", ["number_of_days"]),
    "search": search(fields=["name", "employee_id.name"], filters=[flt("approve", "To Approve", [["state", "=", "confirm"]]), flt("mine", "My Time Off", [["employee_id.user_id", "=", "$uid"]])], group_by=["employee_id", "leave_type_id", "state"]),
})


@leave.hook("recompute")
def _leave_days(env, ids):
    for rec in env["hr.leave"].read(ids, ["id", "date_from", "date_to"]):
        env.cur.execute("UPDATE hr_leave SET number_of_days = %s WHERE id = %s", (_workdays(rec["date_from"], rec["date_to"]), rec["id"]))


def leave_balance(env, employee_id: int, leave_type_id: int, exclude_id=None) -> Decimal:
    env.cur.execute("SELECT COALESCE(SUM(number_of_days), 0) FROM hr_leave_allocation WHERE employee_id = %s AND leave_type_id = %s AND state = 'validate'", (employee_id, leave_type_id))
    allocated = env.cur.fetchone()[0]
    env.cur.execute("SELECT COALESCE(SUM(number_of_days), 0) FROM hr_leave WHERE employee_id = %s AND leave_type_id = %s AND state IN ('confirm', 'validate') AND id IS DISTINCT FROM %s", (employee_id, leave_type_id, exclude_id))
    return allocated - env.cur.fetchone()[0]


@leave.action("submit", "Submit")
def leave_submit(env, ids, params):
    env = env.sudo()
    for rec in env["hr.leave"].read(ids):
        if rec["number_of_days"] <= 0:
            raise UserError("The selected dates contain no working days.")
        env.cur.execute("SELECT 1 FROM hr_leave WHERE employee_id = %s AND id != %s AND state IN ('confirm', 'validate') AND date_from <= %s AND date_to >= %s",
                        (rec["employee_id"], rec["id"], rec["date_to"], rec["date_from"]))
        if env.cur.fetchone():
            raise UserError("These dates overlap another time off request.")
        lt = env["hr.leave.type"].get(rec["leave_type_id"], ["requires_allocation"])
        if lt["requires_allocation"]:
            # Serialize per employee so two concurrent requests cannot spend the same balance.
            env.cur.execute("SELECT id FROM hr_employee WHERE id = %s FOR UPDATE", (rec["employee_id"],))
            balance = leave_balance(env, rec["employee_id"], rec["leave_type_id"], rec["id"])
            if balance < rec["number_of_days"]:
                raise UserError(f"Not enough time off left: {balance:g} day(s) available, {rec['number_of_days']:g} requested.")
    env["hr.leave"].write(ids, {"state": "confirm"})


@leave.action("approve", "Approve", groups=HR_BYPASS)
def leave_approve(env, ids, params):
    env.sudo()["hr.leave"].write(ids, {"state": "validate"})


@leave.action("refuse", "Refuse", groups=HR_BYPASS)
def leave_refuse(env, ids, params):
    env.sudo()["hr.leave"].write(ids, {"state": "refuse"})


@leave.action("reset", "Reset to Draft")
def leave_reset(env, ids, params):
    env.sudo()["hr.leave"].write(ids, {"state": "draft"})


@allocation.action("approve", "Approve", groups=["hr.manager", "hr.user"])
def alloc_approve(env, ids, params):
    env.sudo()["hr.leave.allocation"].write(ids, {"state": "validate"})


@allocation.action("refuse", "Refuse", groups=["hr.manager", "hr.user"])
def alloc_refuse(env, ids, params):
    env.sudo()["hr.leave.allocation"].write(ids, {"state": "refuse"})


attendance = model("hr.attendance", "Attendance", [
    F.m2o("employee_id", "Employee", "hr.employee", required=True), F.datetime("check_in", "Check In", required=True), F.datetime("check_out", "Check Out"),
    F.sql("worked_hours", "Worked Hours", "float", "SELECT EXTRACT(EPOCH FROM ({t}.check_out - {t}.check_in)) / 3600"),
], access={"base.user": "r", "hr.user": "rwcd", "hr.manager": "rwcd", "base.admin": "rwcd"}, rule=_own, rule_bypass=HR_BYPASS, rec_name="id", order="check_in DESC", views={
    "list": list_view("employee_id", "check_in", "check_out", {"field": "worked_hours", "sum": True}),
    "form": form(title=None, sections=[section(col("employee_id"), col("check_in", "check_out", "worked_hours"))]),
    "graph": graph("employee_id", ["worked_hours"]),
    "search": search(fields=["employee_id.name"], filters=[flt("open", "Checked In", [["check_out", "=", False]])], group_by=["employee_id", "check_in:week"]),
})


def toggle_attendance(env) -> dict:
    su = env.sudo()
    employee_id = _my_employee(env)
    if not employee_id:
        raise UserError("Your user is not linked to an employee. Ask HR to link it.")
    env.cur.execute("SELECT id FROM hr_employee WHERE id = %s FOR UPDATE", (employee_id,))
    env.cur.execute("SELECT id FROM hr_attendance WHERE employee_id = %s AND check_out IS NULL LIMIT 1", (employee_id,))
    row = env.cur.fetchone()
    now = datetime.now(timezone.utc)
    if row:
        su["hr.attendance"].write([row[0]], {"check_out": now})
    else:
        su["hr.attendance"].create({"employee_id": employee_id, "check_in": now})
    return attendance_status(env)


def attendance_status(env) -> dict:
    employee_id = _my_employee(env)
    if not employee_id:
        return {"employee": None, "checked_in": False, "since": None, "today_hours": 0}
    env.cur.execute("SELECT name FROM hr_employee WHERE id = %s", (employee_id,))
    name = env.cur.fetchone()[0]
    env.cur.execute("SELECT check_in FROM hr_attendance WHERE employee_id = %s AND check_out IS NULL LIMIT 1", (employee_id,))
    row = env.cur.fetchone()
    env.cur.execute(
        "SELECT COALESCE(SUM(EXTRACT(EPOCH FROM (COALESCE(check_out, now()) - check_in))), 0) / 3600 FROM hr_attendance WHERE employee_id = %s AND check_in >= date_trunc('day', now())",
        (employee_id,))
    hours = float(env.cur.fetchone()[0])
    return {"employee": name, "checked_in": bool(row), "since": row[0].isoformat() if row else None, "today_hours": round(hours, 2)}


expense_cat = model("hr.expense.category", "Expense Category", [
    F.char("name", "Category", required=True), F.m2o("account_id", "Expense Account", "account.account", required=True), F.m2o("tax_id", "Tax", "account.tax", domain=[["scope", "=", "purchase"]], ondelete="SET NULL"),
], access={"base.user": "r", "account.manager": "rwcd", "hr.manager": "rwcd", "base.admin": "rwcd"}, order="name ASC", views={
    "list": list_view("name", "account_id"), "form": form(sections=[section(col("account_id", "tax_id"))]),
})
EXPENSE_STATES = [("draft", "Draft"), ("submitted", "Submitted"), ("approved", "Approved"), ("posted", "Posted"), ("paid", "Paid"), ("refused", "Refused")]
expense = model("hr.expense", "Expense", [
    F.char("name", "Description", required=True), F.m2o("employee_id", "Employee", "hr.employee", required=True, default=_my_employee),
    F.m2o("category_id", "Category", "hr.expense.category", required=True), F.date("date", "Expense Date", required=True, default=lambda env: date.today()),
    F.money("total_amount", "Total", required=True, default=0, tracked=True), F.char("reference", "Bill Reference"), F.text("notes", "Notes"),
    F.sel("state", "Status", EXPENSE_STATES, readonly=True, default="draft", tracked=True),
    F.m2o("move_id", "Journal Entry", "account.move", readonly=True, ondelete="SET NULL"),
    F.m2o("payment_move_id", "Payment Entry", "account.move", readonly=True, ondelete="SET NULL"),
], access={"base.user": "rwcd"}, rule=_own, rule_bypass=["hr.manager", "account.user", "account.manager"], mail=True, order="date DESC, id DESC",
    locked_when=[["state", "not in", ["draft", "refused"]]], constraints=[("amount_positive", "CHECK (total_amount >= 0)")], views={
    "list": list_view("date", "name", "employee_id", "category_id", {"field": "total_amount", "sum": True}, {"field": "state", "widget": "badge"}),
    "form": form(status="state", chatter=True, buttons=[
        btn("submit", "Submit to Manager", primary=True, when=[["state", "=", "draft"]]), btn("approve", "Approve", primary=True, when=[["state", "=", "submitted"]]),
        btn("refuse", "Refuse", when=[["state", "=", "submitted"]]), btn("post", "Post Journal Entry", primary=True, when=[["state", "=", "approved"]]),
        btn("pay", "Register Payment", primary=True, when=[["state", "=", "posted"]]), btn("reset", "Reset to Draft", when=[["state", "=", "refused"]]),
    ], sections=[section(col("employee_id", "category_id", "total_amount"), col("date", "reference", "move_id", "payment_move_id"), tabs=[tab("Notes", "notes")])]),
    "graph": graph("category_id", ["total_amount"]), "pivot": pivot("employee_id", "category_id", ["total_amount"]),
    "search": search(fields=["name", "employee_id.name", "reference"], filters=[
        flt("mine", "My Expenses", [["employee_id.user_id", "=", "$uid"]]), flt("approve", "To Approve", [["state", "=", "submitted"]]), flt("post", "To Post", [["state", "=", "approved"]]), flt("pay", "To Pay", [["state", "=", "posted"]]),
    ], group_by=["employee_id", "category_id", "state", "date:month"]),
})
APPROVERS = ["hr.user", "hr.manager", "account.manager"]
ACCOUNTANTS = ["account.user", "account.manager"]


def _entry(env, d, ref, journal_kind, lines, partner_id=None) -> int:
    move_id = env["account.move"].create({"move_type": "entry", "date": d, "ref": ref, "journal_id": journal_of(env, journal_kind), "partner_id": partner_id})
    for account_id, debit, credit, label in lines:
        env["account.move.line"].create({"move_id": move_id, "account_id": account_id, "debit": debit, "credit": credit, "name": label})
    post_move(env, move_id)
    return move_id


def _require_state(env, model_name, ids, expected, message):
    for rec in env.sudo()[model_name].read(ids, ["state"]):
        if rec["state"] not in expected:
            raise UserError(message)


@expense.action("submit", "Submit")
def expense_submit(env, ids, params):
    _require_state(env, "hr.expense", ids, ("draft",), "Only draft expenses can be submitted.")
    for rec in env.sudo()["hr.expense"].read(ids, ["total_amount"]):
        if rec["total_amount"] <= 0:
            raise UserError("Enter the expense amount first.")
    env.sudo()["hr.expense"].write(ids, {"state": "submitted"})


@expense.action("approve", "Approve", groups=APPROVERS)
def expense_approve(env, ids, params):
    _require_state(env, "hr.expense", ids, ("submitted",), "Only submitted expenses can be approved.")
    for rec in env.sudo()["hr.expense"].read(ids, ["employee_id"]):
        if rec["employee_id"] == _my_employee(env) and not env.user.is_admin:
            raise UserError("You cannot approve your own expense.")
    env.sudo()["hr.expense"].write(ids, {"state": "approved"})


@expense.action("refuse", "Refuse", groups=APPROVERS)
def expense_refuse(env, ids, params):
    env.sudo()["hr.expense"].write(ids, {"state": "refused"})


@expense.action("reset", "Reset to Draft")
def expense_reset(env, ids, params):
    env.sudo()["hr.expense"].write(ids, {"state": "draft"})


@expense.action("post", "Post Journal Entry", groups=ACCOUNTANTS)
def expense_post(env, ids, params):
    env = env.sudo()
    _require_state(env, "hr.expense", ids, ("approved",), "Only approved expenses can be posted.")
    payable = get_account(env, "218000")
    for rec in env["hr.expense"].read(ids):
        cat = env["hr.expense.category"].get(rec["category_id"], ["account_id"])
        move_id = _entry(env, rec["date"], f"Expense: {rec['name']}", "general", [
            (cat["account_id"], rec["total_amount"], 0, rec["name"]), (payable, 0, rec["total_amount"], rec["name"])])
        env["hr.expense"].write([rec["id"]], {"state": "posted", "move_id": move_id})


@expense.action("pay", "Register Payment", groups=ACCOUNTANTS, params=[
    {"name": "journal_id", "label": "Pay From", "type": "many2one", "comodel": "account.journal", "required": True, "domain": [["kind", "in", ["bank", "cash"]]]}])
def expense_pay(env, ids, params):
    env = env.sudo()
    _require_state(env, "hr.expense", ids, ("posted",), "Only posted expenses can be paid.")
    journal_id = params.get("journal_id")
    journal_id = journal_id[0] if isinstance(journal_id, (list, tuple)) else journal_id
    if not journal_id:
        raise UserError("Select a journal.")
    account = env["account.journal"].get(journal_id, ["default_account_id"])["default_account_id"]
    payable = get_account(env, "218000")
    for rec in env["hr.expense"].read(ids):
        move_id = _entry(env, date.today(), f"Reimbursement: {rec['name']}", "general", [
            (payable, rec["total_amount"], 0, rec["name"]), (account, 0, rec["total_amount"], rec["name"])])
        env["hr.expense"].write([rec["id"]], {"state": "paid", "payment_move_id": move_id})


payslip = model("hr.payslip", "Payslip", [
    F.char("name", "Reference", readonly=True, sequence="hr.payslip"), F.m2o("employee_id", "Employee", "hr.employee", required=True, tracked=True),
    F.m2o("contract_id", "Contract", "hr.contract", groups=["hr.manager"]), F.date("date_from", "From", required=True), F.date("date_to", "To", required=True),
    F.money("gross", "Gross", readonly=True, default=0), F.money("deductions", "Deductions", readonly=True, default=0), F.money("net", "Net Pay", readonly=True, default=0),
    F.sel("state", "Status", [("draft", "Draft"), ("done", "Posted"), ("paid", "Paid"), ("cancel", "Cancelled")], readonly=True, default="draft", tracked=True),
    F.m2o("move_id", "Journal Entry", "account.move", readonly=True, ondelete="SET NULL", groups=["hr.manager"]),
    F.m2o("payment_move_id", "Payment Entry", "account.move", readonly=True, ondelete="SET NULL", groups=["hr.manager"]),
], access={"base.user": "r", **PAYROLL}, rule=_own, rule_bypass=["hr.manager"], mail=True, order="date_from DESC, id DESC", locked_when=[["state", "!=", "draft"]], views={
    "list": list_view("name", "employee_id", "date_from", "date_to", {"field": "gross", "sum": True}, {"field": "net", "sum": True}, {"field": "state", "widget": "badge"}),
    "form": form(status="state", chatter=True, buttons=[
        btn("compute", "Compute Sheet", when=[["state", "=", "draft"]]), btn("confirm", "Confirm", primary=True, when=[["state", "=", "draft"]]), btn("pay", "Mark as Paid", primary=True, when=[["state", "=", "done"]]),
    ], sections=[section(col("employee_id", "contract_id", "date_from", "date_to"), col("gross", "deductions", "net", "move_id", "payment_move_id"))]),
    "graph": graph("date_from:month", ["net"]),
    "search": search(fields=["name", "employee_id.name"], filters=[flt("draft", "Draft", [["state", "=", "draft"]])], group_by=["employee_id", "date_from:month", "state"]),
})


def _compute_payslip(env, rec):
    contract_id = rec["contract_id"]
    if not contract_id:
        found = env["hr.contract"].search([["employee_id", "=", rec["employee_id"]], ["state", "=", "open"]], limit=1)
        if not found:
            raise UserError("This employee has no running contract.")
        contract_id = found[0]
    c = env["hr.contract"].get(contract_id, ["wage", "deduction_rate"])
    gross = money(c["wage"])
    deductions = money(gross * dec(c["deduction_rate"]) / 100)
    env["hr.payslip"].write([rec["id"]], {"contract_id": contract_id, "gross": gross, "deductions": deductions, "net": gross - deductions})
    return gross, deductions


@payslip.action("compute", "Compute Sheet", groups=["hr.manager"])
def payslip_compute(env, ids, params):
    env = env.sudo()
    for rec in env["hr.payslip"].read(ids):
        _compute_payslip(env, rec)


@payslip.action("confirm", "Confirm", groups=["hr.manager"])
def payslip_confirm(env, ids, params):
    env = env.sudo()
    _require_state(env, "hr.payslip", ids, ("draft",), "Only draft payslips can be confirmed.")
    for rec in env["hr.payslip"].read(ids):
        gross, deductions = _compute_payslip(env, rec)
        lines = [(get_account(env, "620000"), gross, 0, rec["name"]), (get_account(env, "218100"), 0, gross - deductions, rec["name"])]
        if deductions:
            lines.append((get_account(env, "218200"), 0, deductions, f"Withholding {rec['name']}"))
        env["hr.payslip"].write([rec["id"]], {"state": "done", "move_id": _entry(env, rec["date_to"], rec["name"], "general", lines)})


@payslip.action("pay", "Mark as Paid", groups=["hr.manager"], params=[
    {"name": "journal_id", "label": "Pay From", "type": "many2one", "comodel": "account.journal", "required": True, "domain": [["kind", "in", ["bank", "cash"]]]}])
def payslip_pay(env, ids, params):
    env = env.sudo()
    _require_state(env, "hr.payslip", ids, ("done",), "Only posted payslips can be paid.")
    journal_id = params.get("journal_id")
    journal_id = journal_id[0] if isinstance(journal_id, (list, tuple)) else journal_id
    if not journal_id:
        raise UserError("Select a journal.")
    account = env["account.journal"].get(journal_id, ["default_account_id"])["default_account_id"]
    for rec in env["hr.payslip"].read(ids):
        move_id = _entry(env, date.today(), f"Salary payment {rec['name']}", "general", [(get_account(env, "218100"), rec["net"], 0, rec["name"]), (account, 0, rec["net"], rec["name"])])
        env["hr.payslip"].write([rec["id"]], {"state": "paid", "payment_move_id": move_id})


app_stage = model("hr.recruitment.stage", "Recruitment Stage", [F.char("name", "Stage", required=True), F.int("sequence", "Sequence", default=10)],
                  access={"hr.user": "r", "hr.manager": "rwcd", "base.admin": "rwcd"}, order="sequence ASC",
                  views={"list": list_view("sequence", "name"), "form": form(sections=[section(col("sequence"))])})


def _first_stage(env):
    ids = env.sudo()["hr.recruitment.stage"].search([], limit=1)
    return ids[0] if ids else None


applicant = model("hr.applicant", "Applicant", [
    F.char("name", "Candidate Name", required=True, tracked=True), F.char("email", "Email"), F.char("phone", "Phone"),
    F.m2o("job_id", "Applied Job", "hr.job", tracked=True), F.m2o("stage_id", "Stage", "hr.recruitment.stage", default=_first_stage, tracked=True),
    F.m2o("user_id", "Recruiter", "res.users", default=lambda env: env.user.id or None, ondelete="SET NULL"),
    F.money("salary_expected", "Expected Salary", default=0), F.sel("priority", "Evaluation", [("0", "None"), ("1", "Good"), ("2", "Very Good"), ("3", "Excellent")], default="0"),
    F.text("notes", "Notes"), F.m2o("employee_id", "Employee", "hr.employee", readonly=True, ondelete="SET NULL"), F.bool("active", "Active", default=True),
], access=HR_OFFICER, mail=True, views={
    "kanban": kanban(group_by="stage_id", title="name", subtitle="job_id", priority="priority", user="user_id"),
    "list": list_view("name", "job_id", "stage_id", "email", "phone", "user_id"),
    "form": form(status="stage_id", status_clickable=True, chatter=True, buttons=[btn("hire", "Create Employee", primary=True, when=[["employee_id", "=", False]]), btn("refuse", "Refuse", when=[["active", "=", True]])],
                 sections=[section(col("email", "phone", "job_id"), col("user_id", {"field": "priority", "widget": "priority"}, "salary_expected", "employee_id"), tabs=[tab("Notes", "notes")])]),
    "search": search(fields=["name", "email", "job_id.name"], filters=[flt("refused", "Refused", [["active", "=", False]])], group_by=["job_id", "stage_id"]),
})


@applicant.action("hire", "Create Employee", groups=HR_BYPASS)
def applicant_hire(env, ids, params):
    env = env.sudo()
    a = env["hr.applicant"].get(ids[0])
    if a["employee_id"]:
        raise UserError("An employee already exists for this applicant.")
    job = env["hr.job"].get(a["job_id"], ["department_id"]) if a["job_id"] else {"department_id": None}
    employee_id = env["hr.employee"].create({"name": a["name"], "private_email": a["email"], "private_phone": a["phone"], "job_id": a["job_id"], "department_id": job["department_id"]})
    env["hr.applicant"].write(ids, {"employee_id": employee_id})
    return {"type": "open", "model": "hr.employee", "id": employee_id}


@applicant.action("refuse", "Refuse", groups=HR_BYPASS)
def applicant_refuse(env, ids, params):
    env.sudo()["hr.applicant"].write(ids, {"active": False})
    post_note(env, "hr.applicant", ids[0], "Application refused.")


app("employees", "Employees", "users", "#7a4f8f", [
    menu("employees", "Employees", "hr.employee", ("kanban", "list", "form")),
    menu("departments", "Departments", "hr.department", ("kanban", "list", "form")),
    menu("contracts", "Contracts", "hr.contract", groups=["hr.manager"]),
    menu("config", "Configuration", groups=["hr.user", "hr.manager"], children=[menu("jobs", "Job Positions", "hr.job", ("kanban", "list", "form"))]),
], sequence=100)
app("timeoff", "Time Off", "palmtree", "#3d8c84", [
    menu("mine", "My Time Off", "hr.leave", ("list", "form", "calendar"), [["employee_id.user_id", "=", "$uid"]]),
    menu("overview", "Overview", "hr.leave", ("calendar", "list", "form"), [["state", "=", "validate"]]),
    menu("approvals", "Approvals", groups=["hr.user", "hr.manager"], children=[
        menu("leaves", "Time Off", "hr.leave", ("list", "form", "calendar", "graph"), [["state", "=", "confirm"]]),
        menu("allocations", "Allocations", "hr.leave.allocation"),
    ]),
    menu("config", "Configuration", groups=["hr.manager"], children=[menu("types", "Time Off Types", "hr.leave.type")]),
], sequence=110)
app("attendance", "Attendances", "user-check", "#3a7ca5", [
    menu("kiosk", "Check In / Out", client="attendance"),
    menu("mine", "My Attendances", "hr.attendance", ("list", "form"), [["employee_id.user_id", "=", "$uid"]]),
    menu("all", "All Attendances", "hr.attendance", ("list", "form", "graph"), groups=["hr.user", "hr.manager"]),
], sequence=115)
app("expenses", "Expenses", "receipt", "#997a2c", [
    menu("mine", "My Expenses", "hr.expense", ("list", "form", "graph"), [["employee_id.user_id", "=", "$uid"]]),
    menu("all", "All Expenses", "hr.expense", ("list", "form", "graph", "pivot"), groups=["hr.manager", "account.user", "account.manager"]),
    menu("config", "Configuration", groups=["hr.manager", "account.manager"], children=[menu("categories", "Expense Categories", "hr.expense.category")]),
], sequence=120)
app("recruitment", "Recruitment", "user-plus", "#9c4f6d", [
    menu("applications", "Applications", "hr.applicant", ("kanban", "list", "form")),
    menu("jobs", "Job Positions", "hr.job", ("kanban", "list", "form")),
    menu("config", "Configuration", children=[menu("stages", "Stages", "hr.recruitment.stage")]),
], groups=["hr.user", "hr.manager"], sequence=125)
app("payroll", "Payroll", "banknote", "#4a6b3a", [
    menu("payslips", "Payslips", "hr.payslip", ("list", "form", "graph")),
    menu("contracts", "Contracts", "hr.contract"),
], groups=["hr.manager"], sequence=130)
app("my_payslips", "My Payslips", "wallet", "#5f7f4a", [
    menu("mine", "My Payslips", "hr.payslip", ("list", "form"), [["employee_id.user_id", "=", "$uid"]]),
], sequence=131)
