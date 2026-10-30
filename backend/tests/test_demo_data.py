"""Runs only when the demo data is loaded (SEED_DEMO_DATA=true)."""
import os

import pytest

from conftest import find

pytestmark = pytest.mark.skipif(os.environ.get("SEED_DEMO_DATA") != "true", reason="demo data not loaded")


def test_demo_books_balance(admin):
    trial = admin.get("/api/reports/trial_balance").json()
    assert round(trial["totals"]["debit"], 2) == round(trial["totals"]["credit"], 2) > 0
    meta = admin.get("/api/reports/balance_sheet").json()["meta"]
    assert meta["assets"] == meta["liabilities_equity"]
    assert admin.get("/api/reports/profit_loss").json()["totals"]["amount"] != 0


def test_demo_stock_is_never_negative(admin):
    assert not [q for q in find(admin, "stock.quant", [["location_id.usage", "=", "internal"]]) if q["quantity"] < 0]


def test_every_list_view_has_demo_data_and_loads(admin):
    for model, minimum in (("res.partner", 10), ("sale.order", 20), ("account.move", 20), ("purchase.order", 5), ("stock.picking", 20), ("crm.lead", 9), ("project.task", 10), ("hr.employee", 6), ("helpdesk.ticket", 7), ("mrp.production", 3)):
        assert admin.get(f"/api/models/{model}/records").json()["total"] >= minimum, model


def test_dashboard_has_numbers(admin):
    data = admin.get("/api/dashboard").json()
    assert len(data["tiles"]) >= 6 and len(data["charts"]) == 2
    assert sum(data["charts"][0]["series"][0]["values"]) > 0


def test_demo_people_cannot_sign_in(admin):
    assert [u for u in find(admin, "res.users", []) if u["has_login"] and u["email"] != "admin@test.example.com" and not u["email"].endswith("@test.example.com")] == []
