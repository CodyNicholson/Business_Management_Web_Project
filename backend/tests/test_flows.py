"""The cross-module business flows: the numbers must hold across sales, stock and accounting."""
from datetime import date, timedelta

from conftest import act, add_stock, find, on_hand


def _journal(admin, kind="bank"):
    return find(admin, "account.journal", [["kind", "=", kind]])[0]["id"]


def _balance_sheet_ok(admin):
    meta = admin.get("/api/reports/balance_sheet").json()["meta"]
    assert meta["assets"] == meta["liabilities_equity"]


def test_quote_to_cash(admin, customer, make_product, sale_tax):
    product = make_product(price=100)
    add_stock(admin, product, 10)
    order = admin.post("/api/models/sale.order/records", json={"partner_id": customer, "order_line": [[0, 0, {"product_id": product, "name": "Item", "product_uom_qty": 3, "price_unit": 100, "tax_id": sale_tax}]]})
    assert order.status_code == 201, order.text
    assert order.json()["amount_untaxed"] == 300 and order.json()["amount_tax"] == 24 and order.json()["amount_total"] == 324
    oid = order.json()["id"]
    act(admin, "sale.order", "confirm", oid)
    assert admin.get(f"/api/models/sale.order/records/{oid}").json()["state"] == "sale"
    assert on_hand(admin, product) == 10  # nothing moves until the delivery is validated

    picks = find(admin, "stock.picking", [["sale_id", "=", oid]])
    assert len(picks) == 1 and picks[0]["state"] == "assigned"
    act(admin, "stock.picking", "validate", picks[0]["id"])
    assert on_hand(admin, product) == 7
    line = admin.get(f"/api/models/sale.order/records/{oid}").json()["order_line"][0]
    assert line["qty_delivered"] == 3

    result = act(admin, "sale.order", "create_invoice", oid)
    move_id = result["id"]
    act(admin, "account.move", "post", move_id)
    move = admin.get(f"/api/models/account.move/records/{move_id}").json()
    assert move["state"] == "posted" and move["amount_total"] == 324 and move["amount_residual"] == 324 and move["name"].startswith("INV/")
    assert sum(l["debit"] for l in move["line_ids"]) == sum(l["credit"] for l in move["line_ids"]) == 324
    assert admin.get(f"/api/models/sale.order/records/{oid}").json()["invoice_status"] == "invoiced"

    # Partial, then final payment.
    act(admin, "account.move", "register_payment", move_id, {"amount": 100, "journal_id": _journal(admin), "date": date.today().isoformat()})
    move = admin.get(f"/api/models/account.move/records/{move_id}").json()
    assert move["payment_state"] == "partial" and move["amount_residual"] == 224
    act(admin, "account.move", "register_payment", move_id, {"amount": 500, "journal_id": _journal(admin)}, expect=400)  # overpayment
    act(admin, "account.move", "register_payment", move_id, {"amount": 224, "journal_id": _journal(admin)})
    move = admin.get(f"/api/models/account.move/records/{move_id}").json()
    assert move["payment_state"] == "paid" and move["amount_residual"] == 0
    _balance_sheet_ok(admin)


def test_invoice_on_delivery_waits_for_delivery(admin, customer, make_product):
    product = make_product(invoice_policy="delivery")
    add_stock(admin, product, 5)
    oid = admin.post("/api/models/sale.order/records", json={"partner_id": customer, "order_line": [[0, 0, {"product_id": product, "name": "x", "product_uom_qty": 2, "price_unit": 10}]]}).json()["id"]
    act(admin, "sale.order", "confirm", oid)
    act(admin, "sale.order", "create_invoice", oid, expect=400)


def test_backorder_and_insufficient_stock(admin, customer, make_product):
    product = make_product()
    add_stock(admin, product, 2)
    oid = admin.post("/api/models/sale.order/records", json={"partner_id": customer, "order_line": [[0, 0, {"product_id": product, "name": "x", "product_uom_qty": 5, "price_unit": 10}]]}).json()["id"]
    act(admin, "sale.order", "confirm", oid)
    pick = find(admin, "stock.picking", [["sale_id", "=", oid]])[0]["id"]
    # Shipping everything would oversell, and the failure must leave stock untouched.
    act(admin, "stock.picking", "validate", pick, expect=400)
    assert on_hand(admin, product) == 2
    moves = admin.get(f"/api/models/stock.picking/records/{pick}").json()["move_ids"]
    res = admin.patch(f"/api/models/stock.picking/records/{pick}", json={"move_ids": [[1, moves[0]["id"], {"quantity_done": 2}]]})
    assert res.status_code == 200, res.text
    act(admin, "stock.picking", "validate", pick)
    assert on_hand(admin, product) == 0
    picks = find(admin, "stock.picking", [["sale_id", "=", oid]])
    back = [p for p in picks if p["state"] == "assigned"]
    assert len(back) == 1 and back[0]["backorder_id"][0] == pick
    assert admin.get(f"/api/models/stock.picking/records/{back[0]['id']}").json()["move_ids"][0]["quantity"] == 3
    assert admin.get(f"/api/models/sale.order/records/{oid}").json()["order_line"][0]["qty_delivered"] == 2


def test_procure_to_pay(admin, vendor, make_product):
    product = make_product(cost=25, seller_id=vendor)
    oid = admin.post("/api/models/purchase.order/records", json={"partner_id": vendor, "order_line": [[0, 0, {"product_id": product, "name": "x", "product_qty": 4, "price_unit": 25}]]}).json()["id"]
    act(admin, "purchase.order", "confirm", oid)
    act(admin, "purchase.order", "create_bill", oid, expect=400)  # nothing received yet
    pick = find(admin, "stock.picking", [["purchase_id", "=", oid]])[0]
    assert pick["kind"] == "incoming"
    act(admin, "stock.picking", "validate", pick["id"])
    assert on_hand(admin, product) == 4
    bill = act(admin, "purchase.order", "create_bill", oid)["id"]
    act(admin, "account.move", "post", bill)
    move = admin.get(f"/api/models/account.move/records/{bill}").json()
    assert move["name"].startswith("BILL/") and move["amount_total"] == 100
    act(admin, "account.move", "register_payment", bill, {"amount": 100, "journal_id": _journal(admin)})
    assert admin.get(f"/api/models/account.move/records/{bill}").json()["payment_state"] == "paid"
    assert admin.get(f"/api/models/purchase.order/records/{oid}").json()["bill_status"] == "billed"
    _balance_sheet_ok(admin)


def test_credit_note_reduces_invoice(admin, customer):
    move = admin.post("/api/models/account.move/records", json={"move_type": "out_invoice", "partner_id": customer, "invoice_line_ids": [[0, 0, {"name": "Work", "quantity": 1, "price_unit": 200}]]}).json()
    act(admin, "account.move", "post", move["id"])
    refund = act(admin, "account.move", "reverse", move["id"])["id"]
    act(admin, "account.move", "post", refund)
    original = admin.get(f"/api/models/account.move/records/{move['id']}").json()
    assert original["payment_state"] == "paid" and original["amount_residual"] == 0
    assert admin.get(f"/api/models/account.move/records/{refund}").json()["name"].startswith("RINV/")
    _balance_sheet_ok(admin)


def test_unbalanced_entry_cannot_post_and_posted_is_locked(admin):
    accounts = find(admin, "account.account", [["code", "in", ["101000", "600000"]]])
    entry = admin.post("/api/models/account.move/records", json={"move_type": "entry", "line_ids": [[0, 0, {"account_id": accounts[0]["id"], "debit": 50}], [0, 0, {"account_id": accounts[1]["id"], "credit": 40}]]}).json()
    act(admin, "account.move", "post", entry["id"], expect=400)
    line = [l for l in admin.get(f"/api/models/account.move/records/{entry['id']}").json()["line_ids"] if l["credit"] == 40][0]
    assert admin.patch(f"/api/models/account.move/records/{entry['id']}", json={"line_ids": [[1, line["id"], {"credit": 50}]]}).status_code == 200
    act(admin, "account.move", "post", entry["id"])
    assert admin.patch(f"/api/models/account.move/records/{entry['id']}", json={"ref": "changed"}).status_code == 400
    assert admin.patch(f"/api/models/account.move/records/{entry['id']}", json={"line_ids": [[1, line["id"], {"credit": 1}]]}).status_code == 400
    assert admin.patch(f"/api/models/account.move.line/records/{line['id']}", json={"credit": 1}).status_code == 400
    assert admin.delete(f"/api/models/account.move/records/{entry['id']}").status_code == 400
    _balance_sheet_ok(admin)


def test_lock_date(admin):
    long_ago = date.today() - timedelta(days=3650)
    assert admin.patch("/api/models/res.company/records/1", json={"lock_date": (long_ago + timedelta(days=30)).isoformat()}).status_code == 200
    try:
        accounts = find(admin, "account.account", [["code", "in", ["101000", "600000"]]])
        entry = admin.post("/api/models/account.move/records", json={"move_type": "entry", "date": long_ago.isoformat(), "line_ids": [[0, 0, {"account_id": accounts[0]["id"], "debit": 5}], [0, 0, {"account_id": accounts[1]["id"], "credit": 5}]]}).json()
        act(admin, "account.move", "post", entry["id"], expect=400)
    finally:
        admin.patch("/api/models/res.company/records/1", json={"lock_date": None})


def test_manufacturing_consumes_and_produces(admin, make_product):
    finished, part = make_product(), make_product()
    add_stock(admin, part, 10)
    bom = admin.post("/api/models/mrp.bom/records", json={"name": "B", "product_id": finished, "product_qty": 1, "line_ids": [[0, 0, {"product_id": part, "product_qty": 2}]]}).json()["id"]
    mo = admin.post("/api/models/mrp.production/records", json={"product_id": finished, "bom_id": bom, "product_qty": 3}).json()["id"]
    act(admin, "mrp.production", "confirm", mo)
    assert admin.get(f"/api/models/mrp.production/records/{mo}").json()["component_ids"][0]["required_qty"] == 6
    act(admin, "mrp.production", "produce", mo)
    assert on_hand(admin, finished) == 3 and on_hand(admin, part) == 4
    short = admin.post("/api/models/mrp.production/records", json={"product_id": finished, "bom_id": bom, "product_qty": 5}).json()["id"]
    act(admin, "mrp.production", "confirm", short)
    act(admin, "mrp.production", "produce", short, expect=400)
    assert on_hand(admin, part) == 4 and on_hand(admin, finished) == 3


def test_reordering_creates_rfq_once(admin, vendor, make_product):
    product = make_product(seller_id=vendor)
    location = find(admin, "stock.location", [["usage", "=", "internal"]])[0]["id"]
    rule = admin.post("/api/models/stock.orderpoint/records", json={"product_id": product, "location_id": location, "product_min_qty": 5, "product_max_qty": 20}).json()["id"]
    act(admin, "stock.orderpoint", "replenish", rule)
    lines = find(admin, "purchase.order.line", [["product_id", "=", product]])
    assert len(lines) == 1 and lines[0]["product_qty"] == 20
    act(admin, "stock.orderpoint", "replenish", rule)
    assert len(find(admin, "purchase.order.line", [["product_id", "=", product]])) == 1


def test_pos_prices_come_from_the_server_and_session_posts(admin, make_product, sale_tax):
    product = make_product(price=50, pos_ok=True)
    add_stock(admin, product, 5)
    shop = find(admin, "pos.config", [])[0]["id"]
    session = admin.post("/api/pos/session", json={"config_id": shop}).json()["id"]
    res = admin.post("/api/pos/order", json={"session_id": session, "payment_method": "cash", "amount_paid": 200, "lines": [{"product_id": product, "qty": 2, "price_unit": 0.01}]})
    assert res.status_code == 201, res.text
    order = res.json()
    assert order["amount_total"] == 108 and order["amount_return"] == 92
    assert on_hand(admin, product) == 3
    assert admin.post("/api/pos/order", json={"session_id": session, "payment_method": "cash", "amount_paid": 1, "lines": [{"product_id": product, "qty": 1}]}).status_code == 400
    assert admin.post("/api/pos/order", json={"session_id": session, "payment_method": "card", "lines": [{"product_id": make_product(pos_ok=False), "qty": 1}]}).status_code == 400
    refund = act(admin, "pos.order", "refund", order["id"])
    assert admin.get(f"/api/models/pos.order/records/{refund['id']}").json()["amount_total"] == -108
    assert on_hand(admin, product) == 5
    act(admin, "pos.order", "refund", order["id"], expect=400)
    # Sell again so the session has a non-zero entry to post.
    assert admin.post("/api/pos/order", json={"session_id": session, "payment_method": "card", "lines": [{"product_id": product, "qty": 1}]}).status_code == 201
    assert admin.post(f"/api/pos/session/{session}/close").status_code == 200
    sess = admin.get(f"/api/models/pos.session/records/{session}").json()
    assert sess["state"] == "closed" and sess["move_id"]
    entry = admin.get(f"/api/models/account.move/records/{sess['move_id'][0]}").json()
    assert entry["state"] == "posted" and entry["amount_total"] == 54
    _balance_sheet_ok(admin)


def test_time_off_balance_and_overlap(admin, make_user):
    user = make_user()
    pto = find(admin, "hr.leave.type", [["requires_allocation", "=", True]])[0]["id"]
    alloc = admin.post("/api/models/hr.leave.allocation/records", json={"name": "A", "employee_id": user.employee_id, "leave_type_id": pto, "number_of_days": 3}).json()["id"]
    act(admin, "hr.leave.allocation", "approve", alloc)
    monday = date(2030, 1, 7)
    leave = user.post("/api/models/hr.leave/records", json={"leave_type_id": pto, "date_from": monday.isoformat(), "date_to": (monday + timedelta(days=6)).isoformat()})
    assert leave.status_code == 201, leave.text
    assert leave.json()["number_of_days"] == 5  # weekends do not count
    act(user, "hr.leave", "submit", leave.json()["id"], expect=400)  # only 3 days allocated
    ok = user.post("/api/models/hr.leave/records", json={"leave_type_id": pto, "date_from": monday.isoformat(), "date_to": (monday + timedelta(days=1)).isoformat()}).json()
    act(user, "hr.leave", "submit", ok["id"])
    act(user, "hr.leave", "approve", ok["id"], expect=403)  # employees cannot approve
    act(admin, "hr.leave", "approve", ok["id"])
    overlap = user.post("/api/models/hr.leave/records", json={"leave_type_id": pto, "date_from": monday.isoformat(), "date_to": monday.isoformat()}).json()
    act(user, "hr.leave", "submit", overlap["id"], expect=400)


def test_expense_to_reimbursement(admin, make_user):
    user = make_user()
    cat = find(admin, "hr.expense.category", [])[0]["id"]
    exp = user.post("/api/models/hr.expense/records", json={"name": "Taxi", "category_id": cat, "total_amount": 30}).json()
    assert exp["employee_id"][0] == user.employee_id
    act(user, "hr.expense", "submit", exp["id"])
    assert user.patch(f"/api/models/hr.expense/records/{exp['id']}", json={"total_amount": 3000}).status_code == 400
    act(user, "hr.expense", "approve", exp["id"], expect=403)
    act(admin, "hr.expense", "approve", exp["id"])
    act(admin, "hr.expense", "post", exp["id"])
    act(admin, "hr.expense", "pay", exp["id"], {"journal_id": _journal(admin)})
    rec = admin.get(f"/api/models/hr.expense/records/{exp['id']}").json()
    assert rec["state"] == "paid" and rec["move_id"] and rec["payment_move_id"]
    _balance_sheet_ok(admin)


def test_payroll_posts_entry(admin, make_user):
    user = make_user()
    contract = admin.post("/api/models/hr.contract/records", json={"name": "C", "employee_id": user.employee_id, "wage": 1000, "deduction_rate": 20, "state": "open"}).json()["id"]
    slip = admin.post("/api/models/hr.payslip/records", json={"employee_id": user.employee_id, "date_from": "2030-01-01", "date_to": "2030-01-31"}).json()["id"]
    act(admin, "hr.payslip", "confirm", slip)
    rec = admin.get(f"/api/models/hr.payslip/records/{slip}").json()
    assert rec["gross"] == 1000 and rec["deductions"] == 200 and rec["net"] == 800 and rec["contract_id"][0] == contract
    act(admin, "hr.payslip", "pay", slip, {"journal_id": _journal(admin)})
    _balance_sheet_ok(admin)


def test_subscription_invoices_and_advances(admin, customer, make_product):
    product = make_product(ptype="service", price=10)
    sub = admin.post("/api/models/subscription.subscription/records", json={"partner_id": customer, "period": "quarter", "next_invoice_date": "2030-01-31", "line_ids": [[0, 0, {"product_id": product, "name": "Plan", "quantity": 2, "price_unit": 10}]]}).json()
    assert sub["recurring_total"] == 20 and sub["mrr"] == 6.67
    act(admin, "subscription.subscription", "start", sub["id"])
    move = act(admin, "subscription.subscription", "invoice", sub["id"])["id"]
    assert admin.get(f"/api/models/account.move/records/{move}").json()["amount_untaxed"] == 20
    assert admin.get(f"/api/models/subscription.subscription/records/{sub['id']}").json()["next_invoice_date"] == "2030-04-30"


def test_event_seat_limit(admin):
    event = admin.post("/api/models/event.event/records", json={"name": "E", "date_begin": "2030-01-01T10:00:00Z", "date_end": "2030-01-01T12:00:00Z", "seats_max": 1}).json()["id"]
    assert admin.post("/api/models/event.registration/records", json={"name": "A", "event_id": event}).status_code == 201
    assert admin.post("/api/models/event.registration/records", json={"name": "B", "event_id": event}).status_code == 400


def test_helpdesk_sla_and_closing(admin):
    team = find(admin, "helpdesk.team", [])[0]["id"]
    ticket = admin.post("/api/models/helpdesk.ticket/records", json={"name": "Broken", "team_id": team, "priority": "3"}).json()
    assert ticket["number"].startswith("TCK") and ticket["sla_deadline"]
    closed = find(admin, "helpdesk.stage", [["is_closed", "=", True]])[0]["id"]
    assert admin.patch(f"/api/models/helpdesk.ticket/records/{ticket['id']}", json={"stage_id": closed}).json()["closed_at"]


def test_tracking_appears_in_chatter(admin):
    partner = admin.post("/api/models/res.partner/records", json={"name": "Tracked", "email": "a@example.com"}).json()["id"]
    admin.patch(f"/api/models/res.partner/records/{partner}", json={"email": "b@example.com"})
    messages = admin.get(f"/api/mail/res.partner/{partner}").json()["messages"]
    assert any(c["old"] == "a@example.com" and c["new"] == "b@example.com" for m in messages for c in m["tracking"])
