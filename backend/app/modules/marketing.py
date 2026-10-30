"""Email campaigns. A queued mailing is sent by the background scheduler, one recipient at a time,
so a failure part-way through resumes without emailing anyone twice."""
import secrets
from datetime import datetime, timezone

from .. import config, mail
from ..orm import F, UserError, app, menu, model
from ..views import btn, col, flt, form, list_view, search, section, smart, tab

MKT = {"marketing.user": "rwcd", "base.admin": "rwcd"}
mlist = model("mailing.list", "Mailing List", [
    F.char("name", "Mailing List", required=True),
    F.sql("contact_count", "Contacts", "int", "SELECT COUNT(*) FROM mailing_contact c WHERE c.list_id = {t}.id AND c.opt_out IS NOT TRUE"),
], access=MKT, order="name ASC", views={
    "list": list_view("name", "contact_count"),
    "form": form(smart_buttons=[smart("Contacts", "users", "mailing.contact", [["list_id", "=", "$id"]])], sections=[section(col("contact_count"))]),
})
contact = model("mailing.contact", "Mailing Contact", [
    F.char("name", "Name"), F.char("email", "Email", required=True), F.m2o("list_id", "Mailing List", "mailing.list", required=True, ondelete="CASCADE"),
    F.bool("opt_out", "Opted Out", default=False), F.char("unsubscribe_token", "Token", readonly=True, groups=["base.admin"]),
], access=MKT, rec_name="email", order="email ASC", constraints=[("uniq", "UNIQUE (email, list_id)"), ("token_uniq", "UNIQUE (unsubscribe_token)")], views={
    "list": list_view("name", "email", "list_id", "opt_out"), "form": form(title="email", sections=[section(col("name", "list_id"), col("opt_out"))]),
    "search": search(fields=["name", "email"], filters=[flt("optout", "Opted Out", [["opt_out", "=", True]])], group_by=["list_id"]),
})


@contact.hook("prepare_create")
def _token(env, vals):
    vals["unsubscribe_token"] = secrets.token_urlsafe(24)
    return vals


mailing = model("mailing.mailing", "Mailing", [
    F.char("name", "Subject", required=True, tracked=True), F.m2o("list_id", "Recipients", "mailing.list", required=True), F.text("body", "Message"),
    F.sel("state", "Status", [("draft", "Draft"), ("queue", "In Queue"), ("done", "Sent")], readonly=True, default="draft", tracked=True),
    F.datetime("sent_at", "Sent on", readonly=True), F.int("sent_count", "Delivered", readonly=True, default=0), F.int("failed_count", "Failed", readonly=True, default=0),
], access=MKT, mail=True, locked_when=[["state", "!=", "draft"]], views={
    "list": list_view("name", "list_id", "sent_at", "sent_count", "failed_count", {"field": "state", "widget": "badge"}),
    "form": form(status="state", chatter=True, buttons=[btn("send", "Send", primary=True, when=[["state", "=", "draft"]])], sections=[section(col("list_id"), col("sent_at", "sent_count", "failed_count"), tabs=[tab("Message", "body")])]),
    "search": search(fields=["name"], group_by=["state", "list_id"]),
})
trace = model("mailing.trace", "Delivery", [
    F.m2o("mailing_id", "Mailing", "mailing.mailing", required=True, ondelete="CASCADE"), F.m2o("contact_id", "Contact", "mailing.contact", required=True, ondelete="CASCADE"),
    F.sel("state", "Status", [("sent", "Sent"), ("failed", "Failed")]), F.char("error", "Error"),
], access={"marketing.user": "r", "base.admin": "r"}, rec_name="id", constraints=[("uniq", "UNIQUE (mailing_id, contact_id)")], views={"list": list_view("mailing_id", "contact_id", "state", "error")})


@mailing.action("send", "Send", groups=["marketing.user"], confirm="Send this mailing to everyone on the list?")
def action_send(env, ids, params):
    if not mail.is_configured():
        raise UserError("Outgoing email is not configured. Set SMTP_HOST and SMTP_FROM in .env.")
    if not config.PUBLIC_URL:
        raise UserError("Set PUBLIC_URL in .env first: every mailing needs a working unsubscribe link.")
    env.sudo()["mailing.mailing"].write(ids, {"state": "queue"})


def process_queue(batch: int = 100):
    """Sends at most one batch per scheduler tick. Each delivery commits on its own, so a crash
    resends at most the one message in flight."""
    from ..db import transaction  # noqa: PLC0415  (db imports the orm, which loads modules)
    with transaction(su=True) as env:
        queued = env["mailing.mailing"].search_read([["state", "=", "queue"]], ["id", "name", "body", "list_id"], limit=5)
    for rec in queued:
        with transaction(su=True) as env:
            env.cur.execute(
                """SELECT c.id, c.email, c.unsubscribe_token FROM mailing_contact c WHERE c.list_id = %s AND c.opt_out IS NOT TRUE
                   AND NOT EXISTS (SELECT 1 FROM mailing_trace t WHERE t.mailing_id = %s AND t.contact_id = c.id) LIMIT %s""", (rec["list_id"], rec["id"], batch))
            pending = env.cur.fetchall()
        for contact_id, email, token in pending:
            footer = f"\n\n--\nUnsubscribe: {config.PUBLIC_URL.rstrip('/')}/api/public/unsubscribe/{token}"
            state, error = "sent", None
            try:
                mail.send(email, rec["name"], (rec["body"] or "") + footer)
            except Exception as exc:  # noqa: BLE001  (a bad address must not stop the campaign)
                state, error = "failed", str(exc)[:300]
            with transaction(su=True) as env:
                env["mailing.trace"].create({"mailing_id": rec["id"], "contact_id": contact_id, "state": state, "error": error})
        with transaction(su=True) as env:
            env.cur.execute("SELECT COUNT(*) FILTER (WHERE state = 'sent'), COUNT(*) FILTER (WHERE state = 'failed') FROM mailing_trace WHERE mailing_id = %s", (rec["id"],))
            sent, failed = env.cur.fetchone()
            vals = {"sent_count": sent, "failed_count": failed}
            if len(pending) < batch:
                vals.update(state="done", sent_at=datetime.now(timezone.utc))
            env["mailing.mailing"].write([rec["id"]], vals)


def unsubscribe(env, token: str) -> bool:
    env.cur.execute("UPDATE mailing_contact SET opt_out = TRUE WHERE unsubscribe_token = %s", (token,))
    return env.cur.rowcount > 0


app("marketing", "Email Marketing", "mail", "#c0533f", [
    menu("mailings", "Mailings", "mailing.mailing"),
    menu("lists", "Mailing Lists", "mailing.list"),
    menu("contacts", "Contacts", "mailing.contact"),
], groups=["marketing.user"], sequence=150)
