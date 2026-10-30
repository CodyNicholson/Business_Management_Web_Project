"""Access control, tampering and input handling. This app is exposed to the internet."""
import io
import json

from fastapi.testclient import TestClient

from app.main import app
from conftest import HEADERS, PASSWORD, act, find, new_client


def test_everything_needs_a_session():
    anon = TestClient(app, headers=HEADERS)
    for path in ("/api/meta/bootstrap", "/api/models/res.partner/records", "/api/dashboard", "/api/reports/trial_balance", "/api/discuss/channels", "/api/attachments/1"):
        assert anon.get(path).status_code == 401, path


def test_cookie_flags_and_wrong_password():
    anon = TestClient(app, headers=HEADERS)
    assert anon.post("/api/auth/login", json={"email": "admin@test.example.com", "password": "nope"}).status_code == 401
    res = anon.post("/api/auth/login", json={"email": "admin@test.example.com", "password": "test-admin-password"})
    assert "httponly" in res.headers["set-cookie"].lower() and "samesite=lax" in res.headers["set-cookie"].lower()


def test_login_throttle(make_user):
    target = make_user()
    email = target.get("/api/meta/bootstrap").json()["user"]["email"]
    anon = TestClient(app, headers=HEADERS)
    for _ in range(8):
        assert anon.post("/api/auth/login", json={"email": email, "password": "wrong-password"}).status_code == 401
    assert anon.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 429


def test_logout_revokes_the_session(make_user):
    client = make_user()
    assert client.get("/api/meta/bootstrap").status_code == 200
    stolen = client.cookies.get("session")
    assert client.post("/api/auth/logout").status_code == 200
    other = TestClient(app, headers=HEADERS)
    other.cookies.set("session", stolen)
    assert other.get("/api/meta/bootstrap").status_code == 401


def test_csrf_header_required(admin):
    bare = TestClient(app)
    bare.cookies = admin.cookies
    assert bare.post("/api/models/res.partner/records", json={"name": "x"}).status_code == 403


def test_password_hash_is_unreachable(admin):
    assert "password_hash" not in admin.get("/api/models/res.users/records").json()["records"][0]
    assert admin.get("/api/models/res.users/records", params={"domain": '[["password_hash","ilike","a"]]'}).status_code == 400
    assert admin.get("/api/models/res.users/records", params={"fields": "password_hash"}).json()["records"][0].keys() >= {"id"}
    assert "password_hash" not in admin.get("/api/models/res.users/records", params={"fields": "password_hash"}).json()["records"][0]
    assert admin.post("/api/models/res.users/records", json={"name": "x", "email": "x@x.example.com", "password_hash": "abc"}).status_code == 400


def test_regular_user_cannot_escalate(make_user, group_ids):
    user = make_user()
    assert user.patch(f"/api/models/res.users/records/{user.user_id}", json={"group_ids": [group_ids["base.admin"]]}).status_code == 403
    assert user.post("/api/models/res.users/records", json={"name": "x", "email": "y@x.example.com"}).status_code == 403
    act(user, "res.users", "set_password", user.user_id, {"password": "another-long-password"}, expect=403)


def test_app_access_follows_groups(make_user, customer):
    plain = make_user()
    sales = make_user("sales.user")
    assert plain.get("/api/models/sale.order/records").status_code == 403
    assert plain.get("/api/models/account.move/records").status_code == 403
    assert plain.get("/api/reports/trial_balance").status_code == 403
    assert plain.get("/api/models/hr.contract/records").status_code == 403
    assert sales.get("/api/models/sale.order/records").status_code == 200
    assert sales.get("/api/models/hr.payslip/records").status_code == 200  # own only
    assert sales.post("/api/models/account.move/records", json={"move_type": "entry"}).status_code == 403
    assert plain.post("/api/models/sale.order/records", json={"partner_id": customer}).status_code == 403
    apps = [a["key"] for a in plain.get("/api/meta/bootstrap").json()["apps"]]
    assert "sales" not in apps and "accounting" not in apps and "settings" not in apps and "contacts" in apps


def test_server_owned_fields_cannot_be_set(admin, customer):
    assert admin.post("/api/models/sale.order/records", json={"partner_id": customer, "state": "sale"}).status_code == 400
    order = admin.post("/api/models/sale.order/records", json={"partner_id": customer}).json()
    assert admin.patch(f"/api/models/sale.order/records/{order['id']}", json={"state": "sale"}).status_code == 400
    assert admin.patch(f"/api/models/sale.order/records/{order['id']}", json={"amount_total": 1}).status_code == 400
    assert admin.post("/api/models/account.move/records", json={"move_type": "out_invoice", "state": "posted"}).status_code == 400


def test_private_employee_fields_are_hidden(admin, make_user):
    plain = make_user()
    target = make_user()
    admin.patch(f"/api/models/hr.employee/records/{target.employee_id}", json={"identification_id": "SECRET-ID", "private_phone": "555"})
    seen = plain.get(f"/api/models/hr.employee/records/{target.employee_id}").json()
    assert "identification_id" not in seen and "private_phone" not in seen
    assert plain.get("/api/models/hr.employee/records", params={"domain": '[["identification_id","=","SECRET-ID"]]'}).status_code == 403
    assert plain.get("/api/models/hr.employee/records", params={"order": "identification_id ASC"}).status_code == 400
    assert plain.post("/api/models/hr.employee/read_group", json={"group_by": ["identification_id"]}).status_code == 403
    meta = plain.get("/api/meta/models/hr.employee").json()
    assert "identification_id" not in meta["fields"]
    assert admin.get(f"/api/models/hr.employee/records/{target.employee_id}").json()["identification_id"] == "SECRET-ID"


def test_domain_cannot_reach_unreadable_models(make_user):
    plain = make_user()
    # Filtering employees through a contract wage would leak salaries one comparison at a time.
    res = plain.get("/api/models/hr.payslip/records", params={"domain": '[["contract_id.wage",">",0]]'})
    assert res.status_code == 403


def test_board_columns_respect_access(make_user):
    plain = make_user()
    # An employee can read their expenses, but must not get a list of journal entries through them.
    assert plain.get("/api/models/hr.expense/group_values", params={"field": "move_id"}).json() == []
    assert plain.get("/api/models/hr.leave/group_values", params={"field": "state"}).status_code == 200


def test_employee_sees_only_own_records(admin, make_user):
    alice, bob = make_user(), make_user()
    cat = find(admin, "hr.expense.category", [])[0]["id"]
    exp = alice.post("/api/models/hr.expense/records", json={"name": "Private", "category_id": cat, "total_amount": 5}).json()["id"]
    assert bob.get(f"/api/models/hr.expense/records/{exp}").status_code == 404
    assert exp not in [r["id"] for r in bob.get("/api/models/hr.expense/records").json()["records"]]
    assert bob.patch(f"/api/models/hr.expense/records/{exp}", json={"name": "Hacked"}).status_code == 404
    assert bob.delete(f"/api/models/hr.expense/records/{exp}").status_code == 404
    act(bob, "hr.expense", "submit", exp, expect=403)
    # Nor can Bob file an expense under Alice's name.
    assert bob.post("/api/models/hr.expense/records", json={"name": "x", "category_id": cat, "total_amount": 1, "employee_id": alice.employee_id}).status_code == 403
    assert bob.post(f"/api/mail/hr.expense/{exp}", json={"body": "hi"}).status_code == 404
    assert bob.get(f"/api/mail/hr.expense/{exp}").status_code == 404


def test_payslips_are_private(admin, make_user):
    alice, bob = make_user(), make_user()
    admin.post("/api/models/hr.contract/records", json={"name": "C", "employee_id": alice.employee_id, "wage": 1000, "state": "open"})
    slip = admin.post("/api/models/hr.payslip/records", json={"employee_id": alice.employee_id, "date_from": "2031-01-01", "date_to": "2031-01-31"}).json()["id"]
    assert alice.get(f"/api/models/hr.payslip/records/{slip}").status_code == 200
    assert bob.get(f"/api/models/hr.payslip/records/{slip}").status_code == 404
    assert alice.patch(f"/api/models/hr.payslip/records/{slip}", json={"date_to": "2031-02-01"}).status_code == 403


def test_o2m_commands_cannot_touch_other_records(admin, customer, make_product):
    product = make_product()
    def order():
        return admin.post("/api/models/sale.order/records", json={"partner_id": customer, "order_line": [[0, 0, {"product_id": product, "name": "x", "product_uom_qty": 1, "price_unit": 5}]]}).json()
    a, b = order(), order()
    foreign = b["order_line"][0]["id"]
    assert admin.patch(f"/api/models/sale.order/records/{a['id']}", json={"order_line": [[1, foreign, {"price_unit": 0}]]}).status_code == 404
    assert admin.patch(f"/api/models/sale.order/records/{a['id']}", json={"order_line": [[2, foreign, 0]]}).status_code == 404
    assert admin.get(f"/api/models/sale.order/records/{b['id']}").json()["amount_untaxed"] == 5


def test_lines_of_confirmed_order_are_locked(admin, customer, make_product):
    product = make_product(ptype="service")
    o = admin.post("/api/models/sale.order/records", json={"partner_id": customer, "order_line": [[0, 0, {"product_id": product, "name": "x", "product_uom_qty": 1, "price_unit": 5}]]}).json()
    act(admin, "sale.order", "confirm", o["id"])
    line = o["order_line"][0]["id"]
    assert admin.patch(f"/api/models/sale.order.line/records/{line}", json={"price_unit": 0}).status_code == 400
    assert admin.delete(f"/api/models/sale.order.line/records/{line}").status_code == 400
    assert admin.post("/api/models/sale.order.line/records", json={"order_id": o["id"], "product_id": product, "name": "y", "price_unit": 1}).status_code == 400


def test_sql_injection_attempts_are_inert(admin):
    count = len(find(admin, "res.partner", []))
    evil = "x' OR 1=1; DROP TABLE res_partner; --"
    assert admin.get("/api/models/res.partner/records", params={"domain": json.dumps([["name", "ilike", evil]])}).json()["total"] == 0
    assert admin.get("/api/models/res.partner/records", params={"domain": json.dumps([[evil, "=", 1]])}).status_code == 400
    assert admin.get("/api/models/res.partner/records", params={"domain": json.dumps([["name", evil, 1]])}).status_code == 400
    assert admin.get("/api/models/res.partner/records", params={"order": f"name; {evil}"}).status_code == 400
    assert admin.get("/api/models/res.partner/records", params={"order": "name DESC; DROP TABLE x"}).status_code == 400
    assert admin.post("/api/models/res.partner/read_group", json={"group_by": [evil]}).status_code == 400
    assert admin.post("/api/models/res.partner/read_group", json={"group_by": ["create_date:" + evil]}).status_code == 400
    assert admin.post("/api/models/res.partner/records", json={evil: "x", "name": "y"}).status_code == 400
    assert len(find(admin, "res.partner", [])) == count


def test_malformed_values_return_client_errors(admin):
    assert admin.get("/api/models/res.partner/records", params={"domain": "not json"}).status_code == 400
    assert admin.get("/api/models/res.partner/records", params={"domain": '[["id","=","abc"]]'}).status_code == 400
    assert admin.get("/api/models/res.partner/records", params={"domain": '["|"]'}).status_code == 400
    assert admin.post("/api/models/sale.order/records", json={"partner_id": 99999999}).status_code == 409
    assert admin.post("/api/models/res.partner/records", json={"name": ""}).status_code == 400


def test_records_in_use_cannot_be_deleted(admin, customer):
    admin.post("/api/models/sale.order/records", json={"partner_id": customer})
    assert admin.delete(f"/api/models/res.partner/records/{customer}").status_code == 409


def test_private_channels_are_invisible(make_user):
    alice, bob = make_user(), make_user()
    channel = alice.post("/api/discuss/channels", json={"name": "secret", "kind": "group"}).json()["id"]
    assert alice.post(f"/api/discuss/channels/{channel}/messages", json={"body": "hello"}).status_code == 201
    assert bob.get(f"/api/discuss/channels/{channel}/messages").status_code == 404
    assert bob.post(f"/api/discuss/channels/{channel}/messages", json={"body": "x"}).status_code == 404
    assert channel not in [c["id"] for c in bob.get("/api/discuss/channels").json()["channels"]]
    assert bob.patch(f"/api/models/discuss.channel/records/{channel}", json={"member_ids": [bob.user_id]}).status_code == 404
    assert bob.get("/api/models/discuss.message/records").status_code == 403


def test_todos_are_personal(make_user):
    alice, bob = make_user(), make_user()
    todo = alice.post("/api/models/todo.task/records", json={"name": "Mine"}).json()["id"]
    assert bob.get(f"/api/models/todo.task/records/{todo}").status_code == 404
    assert bob.post("/api/models/todo.task/records", json={"name": "x", "user_id": alice.user_id}).status_code == 400


def _upload(client, content=b"hello", name="note.txt", **data):
    return client.post("/api/attachments", files={"file": (name, io.BytesIO(content))}, data=data)


def test_attachments_follow_the_record(admin, make_user):
    alice, bob = make_user(), make_user()
    cat = find(admin, "hr.expense.category", [])[0]["id"]
    exp = alice.post("/api/models/hr.expense/records", json={"name": "E", "category_id": cat, "total_amount": 1}).json()["id"]
    att = _upload(alice, res_model="hr.expense", res_id=exp).json()["id"]
    res = alice.get(f"/api/attachments/{att}")
    assert res.content == b"hello" and "attachment" in res.headers["content-disposition"]
    assert bob.get(f"/api/attachments/{att}").status_code == 404
    assert _upload(bob, res_model="hr.expense", res_id=exp).status_code == 404


def test_uploaded_html_is_served_as_a_download_and_paths_are_ignored(make_user):
    user = make_user()
    res = _upload(user, b"<script>alert(1)</script>", "../../etc/evil.html")
    assert res.status_code == 201 and res.json()["name"] == "evil.html"
    got = user.get(f"/api/attachments/{res.json()['id']}")
    assert got.headers["content-type"].startswith("application/octet-stream") and got.headers["x-content-type-options"] == "nosniff"


def test_cannot_share_someone_elses_file_through_documents(make_user):
    alice, bob = make_user(), make_user()
    att = _upload(alice).json()["id"]
    assert bob.get(f"/api/attachments/{att}").status_code == 403
    assert bob.post("/api/models/document.document/records", json={"name": "steal", "attachment_id": att}).status_code == 400
    assert alice.post("/api/models/document.document/records", json={"name": "shared", "attachment_id": att}).status_code == 201
    assert bob.get(f"/api/attachments/{att}").status_code == 200


def test_csv_export_neutralizes_formulas(admin):
    admin.post("/api/models/res.partner/records", json={"name": "=HYPERLINK(\"http://evil\")"})
    res = admin.get("/api/models/res.partner/export.csv", params={"fields": "name", "domain": '[["name","ilike","HYPERLINK"]]'})
    assert "'=HYPERLINK" in res.text


def test_csv_import_is_all_or_nothing(admin):
    good = "name,email\nImp One,one@example.com\nImp Two,two@example.com\n"
    assert admin.post("/api/models/res.partner/import", files={"file": ("a.csv", good)}).json()["created"] == 2
    bad = "name,email\nImp Three,three@example.com\n,missing@example.com\n"
    res = admin.post("/api/models/res.partner/import", files={"file": ("a.csv", bad)})
    assert res.status_code == 400 and "Line 3" in res.json()["detail"]
    assert not find(admin, "res.partner", [["name", "=", "Imp Three"]])
    assert admin.post("/api/models/res.partner/import", files={"file": ("a.csv", "name,not_a_column\nx,y\n")}).status_code == 400


def test_change_password(make_user):
    user = make_user()
    email = user.get("/api/meta/bootstrap").json()["user"]["email"]
    assert user.post("/api/auth/change_password", json={"current": "wrong", "new": "brand-new-password"}).status_code == 400
    assert user.post("/api/auth/change_password", json={"current": PASSWORD, "new": "short"}).status_code == 400
    assert user.post("/api/auth/change_password", json={"current": PASSWORD, "new": "brand-new-password"}).status_code == 200
    assert user.get("/api/meta/bootstrap").status_code == 200
    new_client(email, "brand-new-password")


def test_unsubscribe_needs_a_post_and_a_real_token(admin):
    anon = TestClient(app)
    lst = admin.post("/api/models/mailing.list/records", json={"name": "L"}).json()["id"]
    contact = admin.post("/api/models/mailing.contact/records", json={"email": "u@example.com", "list_id": lst}).json()
    token = contact["unsubscribe_token"]
    assert anon.get(f"/api/public/unsubscribe/{token}").status_code == 200
    assert admin.get(f"/api/models/mailing.contact/records/{contact['id']}").json()["opt_out"] is False
    assert anon.post(f"/api/public/unsubscribe/{token}").status_code == 200
    assert admin.get(f"/api/models/mailing.contact/records/{contact['id']}").json()["opt_out"] is True
    assert "not valid" in anon.post("/api/public/unsubscribe/nope").text


def test_all_views_load_for_every_app(admin):
    boot = admin.get("/api/meta/bootstrap").json()
    assert len(boot["apps"]) >= 20

    def walk(menus):
        for m in menus:
            yield m
            yield from walk(m.get("children") or [])
    for a in boot["apps"]:
        for m in walk(a["menus"]):
            if m["model"]:
                assert admin.get(f"/api/meta/models/{m['model']}").status_code == 200
                domain = json.dumps(json.loads(json.dumps(m["domain"]).replace('"$uid"', "1").replace('"$today"', '"2030-01-01"')))
                res = admin.get(f"/api/models/{m['model']}/records", params={"domain": domain})
                assert res.status_code == 200, (m["model"], res.text)
