"""Integration tests against a real Postgres, through the real HTTP API, signed in as real users."""
import os
import uuid

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ADMIN_EMAIL", "admin@test.example.com")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("RUN_SCHEDULER", "false")
os.environ.setdefault("SEED_DEMO_DATA", "false")
os.environ.setdefault("ATTACHMENT_DIR", "/tmp/test-attachments")

from app.main import app  # noqa: E402

HEADERS = {"x-requested-with": "business-suite"}
PASSWORD = "a-long-test-password"


@pytest.fixture(scope="session")
def api():
    with TestClient(app, headers=HEADERS) as client:
        yield client


def new_client(email, password=PASSWORD):
    client = TestClient(app, headers=HEADERS)
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return client


@pytest.fixture(scope="session")
def admin(api):
    return new_client("admin@test.example.com", "test-admin-password")


@pytest.fixture(scope="session")
def group_ids(admin):
    rows = admin.get("/api/models/res.group/records", params={"limit": 200}).json()["records"]
    return {r["key"]: r["id"] for r in rows}


@pytest.fixture(scope="session")
def make_user(admin, group_ids):
    """Returns (client, user id). The user is linked to an employee."""
    def make(*groups):
        email = f"{uuid.uuid4().hex[:10]}@test.example.com"
        res = admin.post("/api/models/res.users/records", json={"name": email[:6], "email": email, "group_ids": [group_ids[g] for g in groups]})
        assert res.status_code == 201, res.text
        user_id = res.json()["id"]
        res = admin.post("/api/models/res.users/actions/set_password", json={"ids": [user_id], "params": {"password": PASSWORD}})
        assert res.status_code == 200, res.text
        employee = admin.post("/api/models/hr.employee/records", json={"name": email[:6], "user_id": user_id}).json()["id"]
        client = new_client(email)
        client.user_id, client.employee_id = user_id, employee
        return client
    return make


@pytest.fixture(scope="session")
def customer(admin):
    return admin.post("/api/models/res.partner/records", json={"name": "Test Customer", "is_customer": True}).json()["id"]


@pytest.fixture(scope="session")
def vendor(admin):
    return admin.post("/api/models/res.partner/records", json={"name": "Test Vendor", "is_vendor": True}).json()["id"]


def find(client, model, domain):
    import json
    return client.get(f"/api/models/{model}/records", params={"domain": json.dumps(domain), "limit": 500}).json()["records"]


def act(client, model, key, ids, params=None, expect=200):
    res = client.post(f"/api/models/{model}/actions/{key}", json={"ids": ids if isinstance(ids, list) else [ids], "params": params or {}})
    assert res.status_code == expect, res.text
    return res.json()


@pytest.fixture(scope="session")
def sale_tax(admin):
    return find(admin, "account.tax", [["scope", "=", "sale"]])[0]["id"]


@pytest.fixture
def make_product(admin, sale_tax):
    def make(name=None, price=100, cost=40, ptype="storable", **kw):
        res = admin.post("/api/models/product.product/records", json={"name": name or f"P-{uuid.uuid4().hex[:8]}", "list_price": price, "standard_price": cost, "product_type": ptype, "sale_tax_id": sale_tax, **kw})
        assert res.status_code == 201, res.text
        return res.json()["id"]
    return make


def on_hand(client, product_id):
    return client.get(f"/api/models/product.product/records/{product_id}").json()["qty_available"]


def add_stock(admin, product_id, qty):
    locations = find(admin, "stock.location", [["usage", "=", "internal"]])
    inv = admin.post("/api/models/stock.inventory/records", json={"location_id": locations[0]["id"], "line_ids": [[0, 0, {"product_id": product_id, "counted_qty": qty}]]})
    assert inv.status_code == 201, inv.text
    act(admin, "stock.inventory", "apply", inv.json()["id"])
