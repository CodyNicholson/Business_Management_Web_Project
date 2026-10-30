"""Company vehicles: drivers, odometer readings and service costs."""
from datetime import date

from ..orm import F, app, menu, model
from ..views import col, flt, form, graph, kanban, list_view, pivot, search, section, smart, tab

FL = {"fleet.user": "rwcd", "base.admin": "rwcd"}
vehicle = model("fleet.vehicle", "Vehicle", [
    F.char("name", "Vehicle", required=True, tracked=True), F.char("license_plate", "License Plate", tracked=True), F.char("vin", "Chassis Number"),
    F.m2o("driver_id", "Driver", "hr.employee", tracked=True, ondelete="SET NULL"), F.date("acquisition_date", "Acquisition Date"), F.money("car_value", "Catalog Value", default=0),
    F.sel("fuel_type", "Fuel", [("gasoline", "Gasoline"), ("diesel", "Diesel"), ("hybrid", "Hybrid"), ("electric", "Electric")], default="gasoline"),
    F.sel("state", "Status", [("new", "New"), ("active", "In Use"), ("repair", "In Repair"), ("sold", "Sold")], default="new", tracked=True),
    F.sql("odometer", "Last Odometer", "float", "SELECT COALESCE(MAX(o.value), 0) FROM fleet_odometer o WHERE o.vehicle_id = {t}.id"),
    F.sql("service_cost", "Service Costs", "money", "SELECT COALESCE(SUM(s.amount), 0) FROM fleet_service s WHERE s.vehicle_id = {t}.id"),
    F.bool("active", "Active", default=True),
], access=FL, mail=True, order="name ASC", views={
    "kanban": kanban(group_by="state", title="name", subtitle="license_plate", user=None, fields=["driver_id", "odometer"]),
    "list": list_view("name", "license_plate", "driver_id", "odometer", "service_cost", "fuel_type", {"field": "state", "widget": "badge"}),
    "form": form(status="state", status_clickable=True, chatter=True, smart_buttons=[smart("Odometer", "gauge", "fleet.odometer", [["vehicle_id", "=", "$id"]]), smart("Services", "wrench", "fleet.service", [["vehicle_id", "=", "$id"]])],
                 sections=[section(col("license_plate", "vin", "driver_id"), col("acquisition_date", "car_value", "fuel_type", "odometer", "service_cost"))]),
    "search": search(fields=["name", "license_plate", "driver_id.name"], group_by=["state", "fuel_type", "driver_id"]),
})
model("fleet.odometer", "Odometer Reading", [
    F.m2o("vehicle_id", "Vehicle", "fleet.vehicle", required=True, ondelete="CASCADE"), F.date("date", "Date", required=True, default=lambda env: date.today()), F.float("value", "Odometer", required=True),
], access=FL, rec_name="id", order="date DESC", views={"list": list_view("date", "vehicle_id", "value"), "form": form(title=None, sections=[section(col("vehicle_id", "date", "value"))]), "graph": graph("date:month", ["value"])})
model("fleet.service", "Service", [
    F.char("name", "Description", required=True), F.m2o("vehicle_id", "Vehicle", "fleet.vehicle", required=True, ondelete="CASCADE"), F.date("date", "Date", required=True, default=lambda env: date.today()),
    F.money("amount", "Cost", default=0), F.m2o("vendor_id", "Vendor", "res.partner", ondelete="SET NULL"),
], access=FL, order="date DESC", views={
    "list": list_view("date", "vehicle_id", "name", "vendor_id", {"field": "amount", "sum": True}), "form": form(sections=[section(col("vehicle_id", "date"), col("amount", "vendor_id"))]),
    "graph": graph("vehicle_id", ["amount"]), "pivot": pivot("vehicle_id", "date:quarter", ["amount"]), "search": search(fields=["name", "vehicle_id.name"], group_by=["vehicle_id", "date:month"]),
})
app("fleet", "Fleet", "car", "#4a6d8c", [
    menu("vehicles", "Vehicles", "fleet.vehicle", ("kanban", "list", "form")),
    menu("odometer", "Odometer", "fleet.odometer", ("list", "form", "graph")),
    menu("services", "Services", "fleet.service", ("list", "form", "graph", "pivot")),
], groups=["fleet.user"], sequence=170)
