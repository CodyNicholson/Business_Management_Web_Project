"""Events and registrations with seat limits."""
from ..orm import F, UserError, app, menu, model
from ..views import btn, col, flt, form, graph, kanban, list_view, search, section, smart, tab

EV = {"base.user": "r", "events.user": "rwcd", "base.admin": "rwcd"}
event = model("event.event", "Event", [
    F.char("name", "Event", required=True, tracked=True), F.datetime("date_begin", "Start", required=True), F.datetime("date_end", "End", required=True),
    F.char("location", "Venue"), F.m2o("user_id", "Responsible", "res.users", default=lambda env: env.user.id or None, ondelete="SET NULL"),
    F.int("seats_max", "Maximum Attendees", default=0, help="0 means unlimited."), F.money("ticket_price", "Ticket Price", default=0), F.text("description", "Description"),
    F.sel("stage", "Stage", [("new", "New"), ("booked", "Booked"), ("announced", "Announced"), ("ended", "Ended"), ("cancel", "Cancelled")], default="new", tracked=True),
    F.sql("seats_taken", "Attendees", "int", "SELECT COUNT(*) FROM event_registration r WHERE r.event_id = {t}.id AND r.state != 'cancel'"),
], access=EV, mail=True, order="date_begin DESC", views={
    "kanban": kanban(group_by="stage", title="name", subtitle="location", date="date_begin", fields=["seats_taken", "seats_max"]),
    "list": list_view("name", "date_begin", "location", "seats_taken", "seats_max", {"field": "stage", "widget": "badge"}),
    "form": form(status="stage", status_clickable=True, chatter=True, smart_buttons=[smart("Attendees", "users", "event.registration", [["event_id", "=", "$id"]])],
                 sections=[section(col("date_begin", "date_end", "location", "user_id"), col("seats_max", "seats_taken", "ticket_price"), tabs=[tab("Description", "description")])]),
    "calendar": {"start": "date_begin", "stop": "date_end", "title": "name"},
    "search": search(fields=["name", "location"], filters=[flt("upcoming", "Upcoming", [["date_begin", ">=", "$today"]])], group_by=["stage", "user_id", "date_begin:month"]),
})
registration = model("event.registration", "Attendee", [
    F.char("name", "Attendee Name", required=True), F.char("email", "Email"), F.char("phone", "Phone"), F.m2o("event_id", "Event", "event.event", required=True, ondelete="CASCADE"),
    F.m2o("partner_id", "Contact", "res.partner", ondelete="SET NULL"), F.sel("state", "Status", [("open", "Registered"), ("done", "Attended"), ("cancel", "Cancelled")], readonly=True, default="open", tracked=True),
], access={"events.user": "rwcd", "base.admin": "rwcd"}, mail=True, views={
    "list": list_view("name", "email", "phone", "event_id", {"field": "state", "widget": "badge"}),
    "form": form(status="state", chatter=True, buttons=[btn("attend", "Mark Attended", primary=True, when=[["state", "=", "open"]]), btn("cancel", "Cancel Registration", when=[["state", "=", "open"]])], sections=[section(col("email", "phone"), col("event_id", "partner_id"))]),
    "search": search(fields=["name", "email", "event_id.name"], group_by=["event_id", "state"]),
})


@registration.hook("prepare_create")
def _check_seats(env, vals):
    if vals.get("event_id"):
        # The row lock serializes registrations, so concurrent sign-ups cannot oversell an event.
        env.cur.execute("SELECT seats_max FROM event_event WHERE id = %s FOR UPDATE", (vals["event_id"],))
        row = env.cur.fetchone()
        if row and row[0]:
            env.cur.execute("SELECT COUNT(*) FROM event_registration WHERE event_id = %s AND state != 'cancel'", (vals["event_id"],))
            if env.cur.fetchone()[0] >= row[0]:
                raise UserError("This event is full.")
    return vals


@registration.action("attend", "Mark Attended", groups=["events.user"])
def attend(env, ids, params):
    env.sudo()["event.registration"].write(ids, {"state": "done"})


@registration.action("cancel", "Cancel Registration", groups=["events.user"])
def cancel(env, ids, params):
    env.sudo()["event.registration"].write(ids, {"state": "cancel"})


app("events", "Events", "ticket", "#8d5a97", [
    menu("events", "Events", "event.event", ("kanban", "list", "form", "calendar")),
    menu("attendees", "Attendees", "event.registration"),
], groups=["events.user"], sequence=160)
