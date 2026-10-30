"""First-run data: required defaults always, demo data only when SEED_DEMO_DATA is on."""
import logging
import random
from datetime import date, datetime, timedelta, timezone

from . import config, security
from .db import transaction
from .orm import GROUPS, Env, sync_schema

log = logging.getLogger("app.seed")

CHART = [
    ("101000", "Bank", "bank"), ("101100", "Cash", "bank"), ("121000", "Accounts Receivable", "receivable"), ("131000", "Inventory", "current_asset"),
    ("151000", "Equipment", "fixed_asset"), ("211000", "Accounts Payable", "payable"), ("218000", "Employee Reimbursements Payable", "current_liability"),
    ("218100", "Salaries Payable", "current_liability"), ("218200", "Payroll Withholdings", "current_liability"), ("251000", "Sales Tax Payable", "current_liability"),
    ("131500", "Tax Receivable", "current_asset"), ("301000", "Owner's Equity", "equity"), ("400000", "Product Sales", "income"), ("410000", "Service Revenue", "income"), ("490000", "Other Income", "other_income"),
    ("500000", "Cost of Goods Sold", "cogs"), ("600000", "General Expenses", "expense"), ("610000", "Travel", "expense"), ("615000", "Meals", "expense"), ("620000", "Salaries", "expense"), ("630000", "Office Supplies", "expense"), ("640000", "Software", "expense"),
]


def bootstrap():
    with transaction(su=True) as env:
        sync_schema(env.cur)
        env.cur.execute("ALTER TABLE res_users ADD COLUMN IF NOT EXISTS password_hash TEXT")
        env.cur.execute("ALTER TABLE ir_attachment ALTER COLUMN res_id DROP NOT NULL")
        _sync_groups(env)
    with transaction(su=True) as env:
        if not _flag(env, "base_data"):
            _base_data(env)
            _set_flag(env, "base_data")
    with transaction(su=True) as env:
        _ensure_admin(env)
    if config.SEED_DEMO_DATA:
        with transaction(su=True) as env:
            env.cur.execute("SELECT pg_advisory_xact_lock(727403)")
            if not _flag(env, "demo_data"):
                log.info("Loading demo data")
                demo_data(env)
                _set_flag(env, "demo_data")


def _flag(env, key):
    env.cur.execute("SELECT 1 FROM ir_config WHERE key = %s", (key,))
    return bool(env.cur.fetchone())


def _set_flag(env, key):
    env.cur.execute("INSERT INTO ir_config (key, value) VALUES (%s, '1') ON CONFLICT DO NOTHING", (key,))


def _sync_groups(env):
    for key, label in GROUPS.items():
        env.cur.execute("INSERT INTO res_group (key, name) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name", (key, label))


def _ensure_admin(env):
    env.cur.execute("SELECT COUNT(*) FROM res_users WHERE password_hash IS NOT NULL")
    if env.cur.fetchone()[0]:
        return
    if len(config.ADMIN_PASSWORD) < security.MIN_PASSWORD_LENGTH:
        raise RuntimeError(f"ADMIN_PASSWORD must be set to at least {security.MIN_PASSWORD_LENGTH} characters for the first start.")
    env.cur.execute("SELECT id FROM res_group WHERE key = 'base.admin'")
    admin_group = env.cur.fetchone()[0]
    user_id = env["res.users"].create({"name": "Administrator", "email": config.ADMIN_EMAIL, "group_ids": [admin_group]})
    env.cur.execute("UPDATE res_users SET password_hash = %s WHERE id = %s", (security.hash_password(config.ADMIN_PASSWORD), user_id))
    env["hr.employee"].create({"name": "Administrator", "user_id": user_id, "work_email": config.ADMIN_EMAIL})


def _base_data(env):
    env["res.company"].create({"name": config.COMPANY_NAME})
    accounts = {code: env["account.account"].create({"code": code, "name": name, "account_type": t}) for code, name, t in CHART}
    for name, code, kind, account in (("Customer Invoices", "INV", "sale", "400000"), ("Vendor Bills", "BILL", "purchase", "600000"), ("Bank", "BNK", "bank", "101000"), ("Cash", "CSH", "cash", "101100"), ("Miscellaneous", "MISC", "general", None)):
        env["account.journal"].create({"name": name, "code": code, "kind": kind, "default_account_id": accounts[account] if account else None})
    env["account.tax"].create({"name": "Sales Tax 8%", "rate": 8, "scope": "sale", "account_id": accounts["251000"]})
    env["account.tax"].create({"name": "Purchase Tax 8%", "rate": 8, "scope": "purchase", "account_id": accounts["131500"]})
    for name, days in (("Immediate Payment", 0), ("15 Days", 15), ("30 Days", 30), ("60 Days", 60)):
        env["account.payment.term"].create({"name": name, "days": days})
    for name, usage in (("WH/Stock", "internal"), ("Partners/Vendors", "supplier"), ("Partners/Customers", "customer"), ("Virtual/Inventory Adjustment", "inventory"), ("Virtual/Production", "production")):
        env["stock.location"].create({"name": name, "usage": usage})
    for name in ("Units", "Hours", "kg", "Meters", "Boxes"):
        env["uom.uom"].create({"name": name})
    env["product.category"].create({"name": "All"})
    for i, (name, won) in enumerate((("New", False), ("Qualified", False), ("Proposition", False), ("Won", True))):
        env["crm.stage"].create({"name": name, "sequence": i * 10, "is_won": won})
    for name in ("Too expensive", "Chose a competitor", "No budget", "No response"):
        env["crm.lost.reason"].create({"name": name})
    for i, (name, fold) in enumerate((("To Do", False), ("In Progress", False), ("Review", False), ("Done", True))):
        env["project.task.stage"].create({"name": name, "sequence": i * 10, "fold": fold})
    for i, (name, closed) in enumerate((("New", False), ("In Progress", False), ("On Hold", False), ("Solved", True))):
        env["helpdesk.stage"].create({"name": name, "sequence": i * 10, "is_closed": closed})
    for i, (name, done) in enumerate((("New Request", False), ("In Progress", False), ("Repaired", True), ("Scrap", True))):
        env["maintenance.stage"].create({"name": name, "sequence": i * 10, "done": done})
    for i, name in enumerate(("New Applicant", "First Interview", "Second Interview", "Contract Proposal", "Hired")):
        env["hr.recruitment.stage"].create({"name": name, "sequence": i * 10})
    for name, needs in (("Paid Time Off", True), ("Sick Leave", False), ("Unpaid Leave", False)):
        env["hr.leave.type"].create({"name": name, "requires_allocation": needs})
    for name, code in (("Travel", "610000"), ("Meals", "615000"), ("Office Supplies", "630000"), ("Software", "640000")):
        env["hr.expense.category"].create({"name": name, "account_id": accounts[code]})
    env.cur.execute("SELECT id FROM account_journal WHERE kind = 'cash' LIMIT 1")
    cash = env.cur.fetchone()[0]
    env.cur.execute("SELECT id FROM account_journal WHERE kind = 'bank' LIMIT 1")
    bank = env.cur.fetchone()[0]
    env["pos.config"].create({"name": "Main Shop", "cash_journal_id": cash, "card_journal_id": bank})
    env["discuss.channel"].create({"name": "general", "kind": "channel", "description": "Company-wide announcements and chat."})
    env["helpdesk.team"].create({"name": "Customer Support", "sla_hours_normal": 48, "sla_hours_urgent": 8})


def demo_data(env: Env):
    """A believable small company. Everything goes through the real business actions, so the
    ledger, stock and order statuses are consistent. Demo users have no password and cannot sign in."""
    from .modules import account, mrp, purchase, sales, stock  # noqa: PLC0415
    rnd = random.Random(42)
    today = date.today()
    # Everything is attributed to the administrator, who is the first user.
    env.cur.execute("SELECT id FROM res_users ORDER BY id LIMIT 1")
    admin_id = env.cur.fetchone()[0]
    env.user = type(env.user)(admin_id, "Administrator", config.ADMIN_EMAIL, env.user.groups)
    env.cur.execute("UPDATE res_company SET name = %s, city = 'Springfield', country = 'United States', email = 'hello@example.com', phone = '+1 555 0100', street = '100 Main Street', zip = '62701', website = 'https://example.com' WHERE id = 1", (config.COMPANY_NAME,))

    def one(model, name, field="name"):
        return env[model].search([[field, "=", name]], limit=1)[0]

    tags = {n: env["res.partner.tag"].create({"name": n}) for n in ("VIP", "Wholesale", "Retail", "Prospect")}
    customers = []
    for name, city, country, tag in (("Northwind Outfitters", "Portland", "United States", "VIP"), ("Harbor & Pine Interiors", "Seattle", "United States", "Retail"), ("Lumen Works Studio", "Austin", "United States", "Retail"),
                                     ("Bluefield Hospitality", "Denver", "United States", "Wholesale"), ("Cedar Ridge Schools", "Boise", "United States", "Wholesale"), ("Marlowe Coworking", "Chicago", "United States", "VIP"),
                                     ("Tidewater Clinics", "Norfolk", "United States", "Prospect"), ("Fennel Street Cafe", "Madison", "United States", "Retail")):
        slug = name.split()[0].lower()
        customers.append(env["res.partner"].create({"name": name, "kind": "company", "city": city, "country": country, "email": f"office@{slug}.example.com", "phone": f"+1 555 01{rnd.randint(10, 99)}", "is_customer": True, "tag_ids": [tags[tag]], "salesperson_id": admin_id}))
        env["res.partner"].create({"name": rnd.choice(("Jordan", "Taylor", "Morgan", "Riley", "Casey", "Avery")) + " " + rnd.choice(("Reyes", "Okafor", "Lindqvist", "Tran", "Haddad", "Novak")), "kind": "person", "parent_id": customers[-1], "job_title": rnd.choice(("Office Manager", "Purchasing Lead", "Director")), "email": f"contact@{slug}.example.com"})
    vendors = [env["res.partner"].create({"name": n, "kind": "company", "is_vendor": True, "is_customer": False, "city": c, "email": f"sales@{n.split()[0].lower()}.example.com"}) for n, c in (("Oakline Timber Supply", "Eugene"), ("Steelhead Hardware", "Pittsburgh"), ("Brightside Fabrics", "Charlotte"))]

    furniture = env["product.category"].create({"name": "Furniture"})
    parts = env["product.category"].create({"name": "Components"})
    services = env["product.category"].create({"name": "Services"})
    accessories = env["product.category"].create({"name": "Accessories"})
    sale_tax = one("account.tax", "Sales Tax 8%")
    purchase_tax = one("account.tax", "Purchase Tax 8%")
    units, hours = one("uom.uom", "Units"), one("uom.uom", "Hours")
    income, service_income, cogs = one("account.account", "400000", "code"), one("account.account", "410000", "code"), one("account.account", "500000", "code")

    def product(name, code, price, cost, cat, ptype="storable", vendor=None, pos=False, policy="order"):
        return env["product.product"].create({
            "name": name, "default_code": code, "list_price": price, "standard_price": cost, "category_id": cat, "product_type": ptype, "seller_id": vendor, "pos_ok": pos, "invoice_policy": policy,
            "sale_tax_id": sale_tax, "purchase_tax_id": purchase_tax, "income_account_id": service_income if ptype == "service" else income, "expense_account_id": cogs, "uom_id": hours if ptype == "service" else units,
            "sale_ok": cat != parts, "description": f"{name} from the demo catalog."})

    desk = product("Oak Standing Desk", "FURN-001", 649, 0, furniture, policy="delivery", pos=True)
    chair = product("Ergonomic Task Chair", "FURN-002", 289, 142, furniture, vendor=vendors[2], pos=True)
    shelf = product("Walnut Bookshelf", "FURN-003", 379, 165, furniture, vendor=vendors[0], pos=True)
    table = product("Conference Table", "FURN-004", 1249, 610, furniture, vendor=vendors[0], policy="delivery")
    lamp = product("Brass Desk Lamp", "ACC-001", 79, 31, accessories, vendor=vendors[1], pos=True)
    mat = product("Felt Desk Mat", "ACC-002", 39, 12, accessories, vendor=vendors[2], pos=True)
    tray = product("Cable Tray", "ACC-003", 24, 8, accessories, vendor=vendors[1], pos=True)
    top = product("Oak Desk Top", "PART-001", 0, 118, parts, vendor=vendors[0])
    frame = product("Steel Lift Frame", "PART-002", 0, 164, parts, vendor=vendors[1])
    screws = product("Screw Kit", "PART-003", 0, 4, parts, vendor=vendors[1])
    install = product("Office Installation", "SVC-001", 95, 0, services, "service")
    design = product("Workspace Design Consult", "SVC-002", 140, 0, services, "service")
    care = product("Furniture Care Plan", "SVC-003", 49, 0, services, "service")
    products = [chair, shelf, table, lamp, mat, tray, install, design]

    def po(vendor, lines, days_ago):
        order = env["purchase.order"].create({"partner_id": vendor, "date_order": today - timedelta(days=days_ago), "date_planned": today - timedelta(days=days_ago - 5)})
        for product_id, qty in lines:
            p = env["product.product"].get(product_id, ["name", "standard_price", "purchase_tax_id"])
            env["purchase.order.line"].create({"order_id": order, "product_id": product_id, "name": p["name"], "product_qty": qty, "price_unit": p["standard_price"], "tax_id": p["purchase_tax_id"]})
        return order

    def backdate(move_id, d, days=30):
        env["account.move"].write([move_id], {"date": d, "invoice_date_due": d + timedelta(days=days)})

    def receive(order):
        for pick in env["stock.picking"].search([["purchase_id", "=", order], ["state", "=", "assigned"]]):
            stock.validate_picking(env, pick)

    bank = one("account.journal", "bank", "kind")
    # The business opens with capital and a counted stock, so recent purchases stay realistic.
    capital = env["account.move"].create({"move_type": "entry", "date": today - timedelta(days=200), "ref": "Opening capital"})
    env["account.move.line"].create({"move_id": capital, "account_id": one("account.account", "101000", "code"), "debit": 120000, "name": "Owner investment"})
    env["account.move.line"].create({"move_id": capital, "account_id": one("account.account", "301000", "code"), "credit": 120000, "name": "Owner investment"})
    account.post_move(env, capital)
    opening = env["stock.inventory"].create({"name": "Opening stock", "location_id": stock.main_stock(env)})
    for product_id, qty in ((top, 40), (frame, 40), (screws, 100), (chair, 60), (shelf, 40), (table, 25), (lamp, 70), (mat, 80), (tray, 90)):
        env["stock.inventory.line"].create({"inventory_id": opening, "product_id": product_id, "counted_qty": qty})
    env["stock.inventory"].run_action("apply", [opening])
    for vendor, lines, ago in ((vendors[0], [(top, 8), (shelf, 6)], 120), (vendors[1], [(frame, 8), (lamp, 10), (tray, 12)], 95), (vendors[2], [(chair, 8), (mat, 15)], 70), (vendors[0], [(top, 6), (table, 3)], 45), (vendors[2], [(chair, 6)], 30)):
        order = po(vendor, lines, ago)
        purchase.confirm_order(env, order)
        receive(order)
        bill = purchase.create_bill(env, order)
        bill_date = today - timedelta(days=ago - 3)
        backdate(bill, bill_date)
        account.post_move(env, bill)
        if ago > 40:
            total = env["account.move"].get(bill, ["amount_total"])["amount_total"]
            env["account.move"].run_action("register_payment", [bill], {"amount": total, "date": bill_date + timedelta(days=20), "journal_id": bank})
    open_po = po(vendors[1], [(lamp, 30), (frame, 15)], 2)
    purchase.confirm_order(env, open_po)
    po(vendors[0], [(top, 15)], 1)

    bom = env["mrp.bom"].create({"name": "Oak Standing Desk BoM", "product_id": desk, "product_qty": 1})
    for product_id, qty in ((top, 1), (frame, 1), (screws, 1)):
        env["mrp.bom.line"].create({"bom_id": bom, "product_id": product_id, "product_qty": qty})
    assembly = env["mrp.workcenter"].create({"name": "Assembly Line", "code": "ASM", "costs_hour": 45})
    finishing = env["mrp.workcenter"].create({"name": "Finishing Station", "code": "FIN", "costs_hour": 38})
    env["mrp.bom.operation"].create({"bom_id": bom, "name": "Assemble frame and top", "workcenter_id": assembly, "duration_minutes": 35})
    env["mrp.bom.operation"].create({"bom_id": bom, "name": "Finish and inspect", "workcenter_id": finishing, "duration_minutes": 15})
    done_mo = env["mrp.production"].create({"product_id": desk, "bom_id": bom, "product_qty": 25, "date_start": today - timedelta(days=120)})
    env["mrp.production"].run_action("confirm", [done_mo])
    env["mrp.production"].run_action("produce", [done_mo])
    next_mo = env["mrp.production"].create({"product_id": desk, "bom_id": bom, "product_qty": 8, "date_start": today + timedelta(days=2)})
    env["mrp.production"].run_action("confirm", [next_mo])
    env["mrp.production"].create({"product_id": desk, "bom_id": bom, "product_qty": 5, "date_start": today + timedelta(days=9)})
    stock_loc = stock.main_stock(env)
    for product_id, low, high in ((lamp, 15, 60), (chair, 10, 40), (tray, 20, 80)):
        env["stock.orderpoint"].create({"product_id": product_id, "location_id": stock_loc, "product_min_qty": low, "product_max_qty": high})

    terms = env["account.payment.term"].search([])
    days_list = [150, 138, 122, 118, 101, 96, 85, 74, 66, 58, 47, 41, 33, 26, 21, 14, 11, 7, 4, 2]
    for i, ago in enumerate(days_list):
        partner = customers[i % len(customers)]
        d = today - timedelta(days=ago)
        order = env["sale.order"].create({"partner_id": partner, "date_order": d, "payment_term_id": rnd.choice(terms), "user_id": admin_id})
        for product_id in rnd.sample(products + [desk], rnd.randint(1, 3)):
            p = env["product.product"].get(product_id, ["name", "list_price", "sale_tax_id"])
            env["sale.order.line"].create({"order_id": order, "product_id": product_id, "name": p["name"], "product_uom_qty": rnd.randint(2, 6), "price_unit": p["list_price"], "tax_id": p["sale_tax_id"], "discount": rnd.choice((0, 0, 0, 5, 10))})
        sales.confirm_order(env, order)
        if ago > 5:
            for pick in env["stock.picking"].search([["sale_id", "=", order], ["state", "=", "assigned"]]):
                stock.validate_picking(env, pick)
        if ago > 8:
            invoice = sales.create_invoice(env, order)
            backdate(invoice, d + timedelta(days=2))
            account.post_move(env, invoice)
            total = env["account.move"].get(invoice, ["amount_total"])["amount_total"]
            if ago > 45:
                env["account.move"].run_action("register_payment", [invoice], {"amount": total, "date": d + timedelta(days=25), "journal_id": bank})
            elif ago > 25:
                env["account.move"].run_action("register_payment", [invoice], {"amount": total / 2, "date": d + timedelta(days=15), "journal_id": bank})
    for i in range(5):
        order = env["sale.order"].create({"partner_id": customers[(i * 3) % len(customers)], "user_id": admin_id, "payment_term_id": rnd.choice(terms)})
        p = env["product.product"].get(table if i % 2 else chair, ["id", "name", "list_price", "sale_tax_id"])
        env["sale.order.line"].create({"order_id": order, "product_id": p["id"], "name": p["name"], "product_uom_qty": i + 2, "price_unit": p["list_price"], "tax_id": p["sale_tax_id"]})
        if i % 2:
            env["sale.order"].run_action("mark_sent", [order])

    stages = env["crm.stage"].search([])
    for i, (name, revenue) in enumerate((("Headquarters refit", 48000), ("Ten-seat meeting room", 9200), ("Campus classroom desks", 31500), ("Coworking phase two", 22400), ("Clinic waiting areas", 14800), ("Hotel lobby lounge", 27600), ("Cafe seating refresh", 5400), ("Standing desk trial", 3900), ("Annual care plan", 2400))):
        env["crm.lead"].create({"name": name, "partner_id": customers[i % len(customers)], "expected_revenue": revenue, "probability": (10, 30, 60)[i % 3], "stage_id": stages[i % 3], "user_id": admin_id, "priority": str(i % 3), "date_deadline": today + timedelta(days=(i - 2) * 9)})
    won = env["crm.lead"].create({"name": "Reception desk order", "partner_id": customers[1], "expected_revenue": 6200, "stage_id": stages[2], "user_id": admin_id})
    env["crm.lead"].run_action("won", [won])
    lost = env["crm.lead"].create({"name": "Warehouse shelving", "partner_id": customers[3], "expected_revenue": 7100, "stage_id": stages[1], "user_id": admin_id})
    env["crm.lead"].run_action("lost", [lost], {"lost_reason_id": one("crm.lost.reason", "Chose a competitor")})

    deps = {n: env["hr.department"].create({"name": n}) for n in ("Sales", "Operations", "Finance", "Workshop")}
    jobs = {n: env["hr.job"].create({"name": n, "department_id": d, "no_of_recruitment": k}) for n, d, k in (("Account Executive", deps["Sales"], 2), ("Operations Coordinator", deps["Operations"], 1), ("Bookkeeper", deps["Finance"], 0), ("Furniture Assembler", deps["Workshop"], 2))}
    staff = []
    for name, job, dep, wage in (("Sam Whitfield", "Account Executive", "Sales", 5400), ("Priya Natarajan", "Operations Coordinator", "Operations", 4800), ("Luis Herrera", "Furniture Assembler", "Workshop", 3900), ("Hannah Berg", "Bookkeeper", "Finance", 4600), ("Tomas Ivanov", "Furniture Assembler", "Workshop", 3800), ("Grace Adeyemi", "Account Executive", "Sales", 5200)):
        slug = name.split()[0].lower()
        emp = env["hr.employee"].create({"name": name, "job_id": jobs[job], "department_id": deps[dep], "work_email": f"{slug}@example.com", "work_phone": f"+1 555 02{rnd.randint(10, 99)}", "work_location": "Main Office"})
        contract = env["hr.contract"].create({"name": f"{name} contract", "employee_id": emp, "wage": wage, "deduction_rate": 18, "date_start": today - timedelta(days=400), "state": "open"})
        staff.append((emp, contract))
    env.cur.execute("SELECT id FROM hr_employee WHERE user_id = %s", (admin_id,))
    admin_emp = env.cur.fetchone()[0]
    env["hr.employee"].write([admin_emp], {"department_id": deps["Operations"], "work_location": "Main Office"})
    env["hr.department"].write([deps["Sales"]], {"manager_id": staff[0][0]})
    env["hr.department"].write([deps["Workshop"]], {"manager_id": staff[2][0]})
    pto = one("hr.leave.type", "Paid Time Off")
    sick = one("hr.leave.type", "Sick Leave")
    for emp, _ in staff + [(admin_emp, None)]:
        alloc = env["hr.leave.allocation"].create({"name": f"{today.year} allocation", "employee_id": emp, "leave_type_id": pto, "number_of_days": 20})
        env["hr.leave.allocation"].run_action("approve", [alloc])
    for i, (emp, _) in enumerate(staff[:5]):
        start = today + timedelta(days=(i * 6) - 8)
        leave = env["hr.leave"].create({"name": ("Family trip", "Dentist", "Long weekend", "Flu", "Moving house")[i], "employee_id": emp, "leave_type_id": sick if i == 3 else pto, "date_from": start, "date_to": start + timedelta(days=i % 3)})
        env["hr.leave"].run_action("submit", [leave])
        if i < 3:
            env["hr.leave"].run_action("approve", [leave])
    for emp, _ in staff:
        for back in (3, 2, 1):
            start = datetime.now(timezone.utc).replace(hour=14, minute=rnd.randint(0, 40), second=0, microsecond=0) - timedelta(days=back)
            env["hr.attendance"].create({"employee_id": emp, "check_in": start, "check_out": start + timedelta(hours=8, minutes=rnd.randint(0, 45))})
    cats = env["hr.expense.category"].search([])
    bank_id = bank
    for i, (name, amount) in enumerate((("Client visit train fare", 86.4), ("Team lunch", 142.75), ("Printer paper", 39.99), ("Design software renewal", 240), ("Airport parking", 58))):
        expense = env["hr.expense"].create({"name": name, "employee_id": staff[i % len(staff)][0], "category_id": cats[i % len(cats)], "total_amount": amount, "date": today - timedelta(days=4 * i + 1)})
        if i != 4:
            env["hr.expense"].run_action("submit", [expense])
        if i < 3:
            env["hr.expense"].run_action("approve", [expense])
        if i < 2:
            env["hr.expense"].run_action("post", [expense])
        if i == 0:
            env["hr.expense"].run_action("pay", [expense], {"journal_id": bank_id})
    month_start = today.replace(day=1)
    for months_back in (2, 1):
        end = month_start - timedelta(days=1) if months_back == 1 else (month_start - timedelta(days=1)).replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
        for emp, contract in staff:
            slip = env["hr.payslip"].create({"employee_id": emp, "contract_id": contract, "date_from": start, "date_to": end})
            env["hr.payslip"].run_action("confirm", [slip])
            if months_back == 2:
                env["hr.payslip"].run_action("pay", [slip], {"journal_id": bank_id})
    for i, (name, stage_name) in enumerate((("Morgan Ellis", "New Applicant"), ("Dana Whitaker", "First Interview"), ("Chris Oyelaran", "First Interview"), ("Alex Petrov", "Second Interview"), ("Jamie Sato", "Contract Proposal"))):
        env["hr.applicant"].create({"name": name, "job_id": jobs["Furniture Assembler" if i % 2 else "Account Executive"], "stage_id": one("hr.recruitment.stage", stage_name), "email": f"{name.split()[0].lower()}@example.net", "salary_expected": 3800 + i * 300, "priority": str(i % 4)})

    todo, doing, review, done = env["project.task.stage"].search([])
    for name, partner, tasks in (("Marlowe Coworking Fit-out", customers[5], ("Site survey", "Floor plan draft", "Order custom tables", "Install phase one", "Client walkthrough")), ("Website Catalog Refresh", None, ("Photograph new range", "Write product copy", "Update price list")), ("Workshop Safety Review", None, ("Inspect saw guards", "Update safety training", "Replace first aid kits", "Log findings"))):
        project = env["project.project"].create({"name": name, "partner_id": partner, "user_id": admin_id, "date_start": today - timedelta(days=30), "date": today + timedelta(days=45)})
        for i, title in enumerate(tasks):
            stage_id = (done, done, doing, todo, review)[i % 5]
            task = env["project.task"].create({"name": title, "project_id": project, "stage_id": stage_id, "user_ids": [admin_id], "date_deadline": today + timedelta(days=(i * 5) - 6), "allocated_hours": 4 + i * 2, "priority": "1" if i == 2 else "0"})
            if stage_id in (done, doing):
                emp = staff[i % len(staff)][0]
                for back in (1, 3):
                    env["hr.timesheet"].create({"employee_id": emp, "project_id": project, "task_id": task, "date": today - timedelta(days=back + i), "name": f"Work on {title.lower()}", "unit_amount": rnd.choice((1.5, 2, 3, 4))})

    team = env["helpdesk.team"].search([], limit=1)[0]
    hd = env["helpdesk.stage"].search([])
    for i, (subject, priority) in enumerate((("Chair arm rest arrived scratched", "1"), ("Desk motor makes a grinding noise", "3"), ("Missing screws in the bookshelf box", "1"), ("Request a copy of last invoice", "0"), ("Delivery window change", "2"), ("Lamp switch stopped working", "1"), ("How do I oil the oak finish?", "0"))):
        ticket = env["helpdesk.ticket"].create({"name": subject, "team_id": team, "priority": priority, "partner_id": customers[i % len(customers)], "user_id": admin_id if i % 2 else None, "description": subject + ". Reported by the customer by email."})
        env["helpdesk.ticket"].write([ticket], {"stage_id": hd[i % 4]})

    shop = env["pos.config"].search([], limit=1)[0]
    from .modules import pos  # noqa: PLC0415
    session = pos.open_session(env, shop)
    for lines, method in (([(lamp, 1), (mat, 2)], "card"), ([(tray, 3)], "cash"), ([(chair, 1)], "card"), ([(mat, 1), (tray, 1)], "cash")):
        pos.create_order(env, session, [{"product_id": p, "qty": q} for p, q in lines], method)
    pos.close_session(env, session)

    mlist = env["mailing.list"].create({"name": "Newsletter"})
    for i, cid in enumerate(customers):
        rec = env["res.partner"].get(cid, ["name", "email"])
        env["mailing.contact"].create({"name": rec["name"], "email": rec["email"], "list_id": mlist})
    env["mailing.mailing"].create({"name": "Spring workspace collection", "list_id": mlist, "body": "Our new oak range is here.\n\nVisit the showroom to try the standing desks."})

    event = env["event.event"].create({"name": "Workspace Design Open Day", "date_begin": datetime.now(timezone.utc) + timedelta(days=18), "date_end": datetime.now(timezone.utc) + timedelta(days=18, hours=6), "location": "Main Showroom", "seats_max": 40, "stage": "announced", "ticket_price": 0, "description": "Talks and hands-on demos."})
    env["event.event"].create({"name": "Trade Partners Breakfast", "date_begin": datetime.now(timezone.utc) + timedelta(days=40), "date_end": datetime.now(timezone.utc) + timedelta(days=40, hours=2), "location": "Marlowe Coworking", "seats_max": 15, "stage": "booked"})
    for i in range(6):
        rec = env["res.partner"].get(customers[i], ["name", "email"])
        env["event.registration"].create({"name": rec["name"], "email": rec["email"], "event_id": event, "partner_id": customers[i]})

    van = env["fleet.vehicle"].create({"name": "Delivery Van 1", "license_plate": "DV-1042", "driver_id": staff[1][0], "state": "active", "fuel_type": "diesel", "car_value": 38500, "acquisition_date": today - timedelta(days=700)})
    env["fleet.vehicle"].create({"name": "Delivery Van 2", "license_plate": "DV-2087", "driver_id": staff[4][0], "state": "repair", "fuel_type": "diesel", "car_value": 41200, "acquisition_date": today - timedelta(days=320)})
    env["fleet.vehicle"].create({"name": "Sales Hatchback", "license_plate": "SH-3310", "driver_id": staff[0][0], "state": "active", "fuel_type": "hybrid", "car_value": 27900, "acquisition_date": today - timedelta(days=210)})
    for i, value in enumerate((41200, 43850, 46210)):
        env["fleet.odometer"].create({"vehicle_id": van, "date": today - timedelta(days=(2 - i) * 40), "value": value})
    env["fleet.service"].create({"name": "Oil and filter change", "vehicle_id": van, "amount": 186, "date": today - timedelta(days=40), "vendor_id": vendors[1]})
    env["fleet.service"].create({"name": "Front brake pads", "vehicle_id": van, "amount": 412, "date": today - timedelta(days=12), "vendor_id": vendors[1]})

    saw = env["maintenance.equipment"].create({"name": "Panel Saw", "serial_no": "PS-7781", "location": "Workshop", "cost": 8400, "maintenance_days": 90})
    press = env["maintenance.equipment"].create({"name": "Drill Press", "serial_no": "DP-2210", "location": "Workshop", "cost": 2100, "employee_id": staff[2][0]})
    env["maintenance.equipment"].create({"name": "Office Printer", "serial_no": "OP-0419", "location": "Main Office", "cost": 640})
    ms = env["maintenance.stage"].search([])
    env["maintenance.request"].create({"name": "Replace saw blade", "equipment_id": saw, "kind": "preventive", "schedule_date": today + timedelta(days=3), "stage_id": ms[0], "priority": "1", "user_id": admin_id})
    env["maintenance.request"].create({"name": "Drill press belt slipping", "equipment_id": press, "kind": "corrective", "stage_id": ms[1], "priority": "2", "schedule_date": today})

    for i, cid in enumerate(customers[:4]):
        sub = env["subscription.subscription"].create({"partner_id": cid, "period": ("month", "quarter", "year", "month")[i], "user_id": admin_id, "next_invoice_date": today + timedelta(days=10 + i * 7)})
        p = env["product.product"].get(care, ["name", "list_price", "sale_tax_id"])
        env["subscription.line"].create({"subscription_id": sub, "product_id": care, "name": p["name"], "quantity": 1 + i * 2, "price_unit": p["list_price"], "tax_id": p["sale_tax_id"]})
        env["subscription.subscription"].run_action("start", [sub])

    for name, offset, hour, length in (("Team stand-up", 1, 9, 1), ("Showroom walkthrough", 2, 11, 2), ("Quarterly review", 5, 14, 2), ("Supplier call", 8, 10, 1), ("Hiring panel", 12, 15, 2)):
        day = today + timedelta(days=offset)
        while day.weekday() > 4:
            day += timedelta(days=1)
        start = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
        env["calendar.event"].create({"name": name, "start": start, "stop": start + timedelta(hours=length), "user_id": admin_id, "attendee_ids": [admin_id], "location": "Main Office"})
    for name, stage in (("Review the supplier contract", "today"), ("Book the van service", "inbox"), ("Plan the summer sale", "later")):
        env["todo.task"].create({"name": name, "stage": stage, "user_id": admin_id})
    intro = env["knowledge.article"].create({"name": "Company Handbook", "body": "# Welcome\n\nThis handbook covers how we work.\n\n## Hours\n\nThe office is open **9 to 5**, Monday to Friday.\n\n## Time off\n\nRequest time off in the *Time Off* app."})
    env["knowledge.article"].create({"name": "Assembling the Standing Desk", "parent_id": intro, "body": "## Steps\n\n1. Fit the frame to the top.\n2. Fasten with the screw kit.\n3. Test the lift through its full range."})
    env["document.folder"].create({"name": "Contracts"})
    env["document.folder"].create({"name": "Marketing"})
    general = env["discuss.channel"].search([["name", "=", "general"]], limit=1)[0]
    env["discuss.message"].create({"channel_id": general, "author_id": admin_id, "body": "Welcome to the team chat."})
    for mname, rid, name, days in (("crm.lead", env["crm.lead"].search([], limit=1)[0], "Call to confirm the budget", 0), ("sale.order", env["sale.order"].search([["state", "=", "draft"]], limit=1)[0], "Follow up on this quotation", 2)):
        env["mail.activity"].create({"name": name, "kind": "call", "date_deadline": today + timedelta(days=days), "user_id": admin_id, "res_model": mname, "res_id": rid})
