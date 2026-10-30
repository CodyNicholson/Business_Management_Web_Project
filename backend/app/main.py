import csv
import io
import json
import logging
import mimetypes
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal

import psycopg
from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import config, modules, security  # noqa: F401  (importing modules fills the registry)
from .db import pool, transaction
from .modules import account as account_module, hr, marketing, pos, stock, subscription
from .orm import APPS, GROUPS, REGISTRY, AccessDenied, CurrentUser, Env, Model, NotFound, Records, UserError, validate_registry
from .security import AUTH
from .seed import bootstrap

log = logging.getLogger("app")
MAX_PAGE = 2000


@asynccontextmanager
async def lifespan(_app):
    validate_registry()
    pool.open(wait=True, timeout=60)
    bootstrap()
    if config.RUN_SCHEDULER:
        threading.Thread(target=_scheduler, daemon=True, name="scheduler").start()
    yield
    pool.close()


app = FastAPI(title="Business Suite API", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.exception_handler(UserError)
def _user_error(request, exc: UserError):
    return JSONResponse({"detail": exc.message}, status_code=exc.status)


@app.exception_handler(psycopg.errors.ForeignKeyViolation)
def _fk_error(request, exc):
    return JSONResponse({"detail": "This record is linked to other records, or references one that does not exist."}, status_code=409)


@app.exception_handler(psycopg.errors.UniqueViolation)
def _unique_error(request, exc):
    return JSONResponse({"detail": "A record with these values already exists."}, status_code=409)


@app.exception_handler(psycopg.errors.CheckViolation)
def _check_error(request, exc):
    return JSONResponse({"detail": "A value is outside the allowed range."}, status_code=400)


@app.exception_handler(psycopg.errors.NotNullViolation)
def _null_error(request, exc):
    return JSONResponse({"detail": "A required value is missing."}, status_code=400)


@app.exception_handler(psycopg.errors.DataError)
def _data_error(request, exc):
    return JSONResponse({"detail": "A value has the wrong format."}, status_code=400)


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    # Browsers cannot add a custom header cross-site without a CORS preflight, which is never
    # granted here, so requiring it blocks cross-site form posts on top of SameSite cookies.
    public = request.url.path.startswith("/api/public/") or request.url.path == "/api/auth/login"
    if request.method not in ("GET", "HEAD", "OPTIONS") and not public and request.headers.get("x-requested-with") != "business-suite":
        return JSONResponse({"detail": "Missing request header."}, status_code=403)
    return await call_next(request)


def _scheduler():
    last_hourly = 0.0
    while True:
        time.sleep(60)
        try:
            marketing.process_queue()
            if time.time() - last_hourly > 3600:
                last_hourly = time.time()
                with transaction(su=True) as env:
                    env.cur.execute("SELECT pg_try_advisory_xact_lock(727402)")
                    if env.cur.fetchone()[0]:
                        subscription.invoice_due(env)
                        stock.replenish(env)
        except Exception:  # noqa: BLE001  (one failed run must not stop future runs)
            log.exception("Scheduled job failed")


@app.get("/api/health")
def health():
    with transaction() as env:
        env.cur.execute("SELECT 1")
    return {"status": "ok"}


# -- auth -----------------------------------------------------------------------------------------
@app.post("/api/auth/login")
def login(request: Request, response: Response, payload: dict = Body(...)):
    email = str(payload.get("email") or "").strip().lower()
    password = str(payload.get("password") or "")
    ip = security.client_ip(request)
    # Argon2 is slow on purpose: reject absurd input before spending CPU on it.
    if len(password) > 1024:
        raise UserError("Wrong email or password.", 401)
    with transaction() as env:
        security.check_throttle(env.cur, email, ip)
        env.cur.execute("SELECT id, password_hash FROM res_users WHERE email = %s AND active IS TRUE", (email,))
        row = env.cur.fetchone()
    if not security.verify_password(row[1] if row else None, password):
        security.record_failure(email, ip)
        raise UserError("Wrong email or password.", 401)
    with transaction() as env:
        security.start_session(env.cur, response, row[0])
        user = security.load_user(env.cur, row[0])
    return {"id": user.id, "name": user.name, "email": user.email}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    with transaction() as env:
        security.end_session(env.cur, response, request.cookies.get(security.COOKIE))
    return {"ok": True}


@app.post("/api/auth/change_password")
def change_password(request: Request, response: Response, payload: dict = Body(...), user: CurrentUser = AUTH):
    with transaction() as env:
        env.cur.execute("SELECT password_hash FROM res_users WHERE id = %s", (user.id,))
        stored = env.cur.fetchone()[0]
        if not security.verify_password(stored, str(payload.get("current") or "")):
            raise UserError("Your current password is not correct.")
        hashed = security.hash_password(str(payload.get("new") or ""))
        env.cur.execute("UPDATE res_users SET password_hash = %s WHERE id = %s", (hashed, user.id))
        security.end_all_sessions(env.cur, user.id)
        security.start_session(env.cur, response, user.id)
    return {"ok": True}


# -- metadata -------------------------------------------------------------------------------------
def _visible_menu(env: Env, items: list[dict]) -> list[dict]:
    out = []
    for mi in items:
        if mi.get("groups") and not env.has_group(*mi["groups"]):
            continue
        children = _visible_menu(env, mi["children"]) if mi.get("children") else None
        if mi.get("children") and not children:
            continue
        if mi.get("model") and not env.can(REGISTRY[mi["model"]], "r"):
            continue
        out.append({**mi, "children": children})
    return out


@app.get("/api/meta/bootstrap")
def meta_bootstrap(user: CurrentUser = AUTH):
    with transaction(user) as env:
        apps = []
        for a in sorted(APPS, key=lambda x: x["sequence"]):
            if not env.has_group(*a["groups"]):
                continue
            menus = _visible_menu(env, a["menus"])
            if menus:
                apps.append({**a, "menus": menus})
        company = env.company()
        env.cur.execute("SELECT COUNT(*) FROM mail_activity WHERE user_id = %s AND done IS NOT TRUE AND date_deadline <= CURRENT_DATE", (user.id,))
        due = env.cur.fetchone()[0]
    return {"user": {"id": user.id, "name": user.name, "email": user.email, "is_admin": user.is_admin, "groups": sorted(user.groups)},
            "apps": apps, "activities_due": due,
            "company": {"name": company["name"], "currency_symbol": company["currency_symbol"], "currency_code": company["currency_code"], "email": company["email"], "phone": company["phone"],
                        "street": company["street"], "city": company["city"], "zip": company["zip"], "country": company["country"], "tax_id": company["tax_id"], "website": company["website"]}}


def _field_meta(f):
    return {"name": f.name, "type": f.effective_type, "label": f.label or f.name, "required": f.required, "readonly": f.readonly, "selection": f.selection, "comodel": f.comodel,
            "domain": f.domain, "onchange": f.onchange, "help": f.help, "columns": f.columns}


def _filter_item(env, m, item) -> bool:
    name = item if isinstance(item, str) else item["field"]
    f = m.by_name.get(name.split(":")[0])
    return bool(f) and env.can_field(f)


def _filtered_views(env: Env, m: Model) -> dict:
    views = json.loads(json.dumps(m.views))
    if "list" in views:
        views["list"]["columns"] = [c for c in views["list"]["columns"] if _filter_item(env, m, c)]
    form = views.get("form")
    if form:
        form["smart"] = [s for s in form["smart"] if env.can(REGISTRY[s["model"]], "r")]
        form["buttons"] = [b for b in form["buttons"] if _action_allowed(env, m, b["action"])]
        for section in form["sections"]:
            section["columns"] = [[i for i in column if _filter_item(env, m, i)] for column in section.get("columns", [])]
            tabs = []
            for tab in section.get("tabs", []):
                tab["fields"] = [i for i in tab["fields"] if _filter_item(env, m, i)]
                if tab["fields"]:
                    tabs.append(tab)
            section["tabs"] = tabs
    return views


def _action_allowed(env: Env, m: Model, key: str) -> bool:
    action = m.actions.get(key)
    return bool(action) and env.can(m, "w") and (not action.groups or env.has_group(*action.groups))


@app.get("/api/meta/models/{model_name}")
def meta_model(model_name: str, user: CurrentUser = AUTH):
    with transaction(user) as env:
        m = _model(env, model_name)
        return {"name": m.name, "label": m.label, "rec_name": m.rec_name, "mail": m.mail, "locked_when": m.locked_when, "order": m.order,
                "access": {op: env.can(m, op) for op in "rwcd"},
                "fields": {f.name: _field_meta(f) for f in m.by_name.values() if env.can_field(f)},
                "views": _filtered_views(env, m),
                "actions": {k: {"key": k, "label": a.label, "params": a.params, "confirm": a.confirm} for k, a in m.actions.items() if _action_allowed(env, m, k)}}


def _model(env: Env, name: str) -> Model:
    m = REGISTRY.get(name)
    if not m:
        raise NotFound("Unknown model.")
    env.check_access(m, "r")
    return m


def _parse_domain(domain: str | None) -> list:
    if not domain:
        return []
    try:
        parsed = json.loads(domain)
    except ValueError as exc:
        raise UserError("Invalid domain.") from exc
    if not isinstance(parsed, list):
        raise UserError("Invalid domain.")
    return parsed


# -- generic records ------------------------------------------------------------------------------
@app.get("/api/models/{model_name}/records")
def list_records(model_name: str, domain: str | None = None, fields: str | None = None, order: str | None = None,
                 limit: int = Query(80, ge=1, le=MAX_PAGE), offset: int = Query(0, ge=0), user: CurrentUser = AUTH):
    with transaction(user) as env:
        recs = env[model_name]
        parsed = _parse_domain(domain)
        names = [n for n in fields.split(",") if n] if fields else None
        ids = recs.search(parsed, order, limit, offset)
        return {"records": recs.to_api(ids, names), "total": recs.count(parsed)}


@app.get("/api/models/{model_name}/records/{rec_id}")
def read_record(model_name: str, rec_id: int, user: CurrentUser = AUTH):
    with transaction(user) as env:
        rows = env[model_name].to_api([rec_id])
        if not rows:
            raise NotFound()
        return rows[0]


@app.post("/api/models/{model_name}/records", status_code=201)
def create_record(model_name: str, vals: dict = Body(...), user: CurrentUser = AUTH):
    with transaction(user) as env:
        rec_id = env[model_name].create(vals)
        created = env[model_name].to_api([rec_id])[0]
    return created


@app.patch("/api/models/{model_name}/records/{rec_id}")
def update_record(model_name: str, rec_id: int, vals: dict = Body(...), user: CurrentUser = AUTH):
    with transaction(user) as env:
        env[model_name].get(rec_id, ["id"])
        env[model_name].write([rec_id], vals)
        updated = env[model_name].to_api([rec_id])
    if not updated:
        raise NotFound()
    return updated[0]


@app.delete("/api/models/{model_name}/records/{rec_id}", status_code=204)
def delete_record(model_name: str, rec_id: int, user: CurrentUser = AUTH):
    with transaction(user) as env:
        env[model_name].get(rec_id, ["id"])
        env[model_name].unlink([rec_id])
    return Response(status_code=204)


@app.post("/api/models/{model_name}/actions/{key}")
def run_action(model_name: str, key: str, payload: dict = Body(default={}), user: CurrentUser = AUTH):
    ids = payload.get("ids") or []
    if not ids or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise UserError("Select at least one record.")
    with transaction(user) as env:
        result = env[model_name].run_action(key, ids, payload.get("params") or {})
    return result or {"type": "reload"}


@app.post("/api/models/{model_name}/read_group")
def read_group(model_name: str, payload: dict = Body(...), user: CurrentUser = AUTH):
    with transaction(user) as env:
        return {"groups": env[model_name].read_group(payload.get("domain") or [], payload.get("group_by") or [], payload.get("measures") or [])}


@app.get("/api/models/{model_name}/name_search")
def name_search(model_name: str, q: str = "", domain: str | None = None, limit: int = Query(12, ge=1, le=100), user: CurrentUser = AUTH):
    with transaction(user) as env:
        recs = env[model_name]
        parsed = _parse_domain(domain)
        if q:
            names = recs.m.search_fields or [recs.m.rec_name]
            parsed = ["|"] * (len(names) - 1) + [[n, "ilike", q] for n in names] + parsed
        ids = recs.search(parsed, None, limit)
        return [[r["id"], r["display_name"]] for r in recs.to_api(ids, ["id"])]


@app.get("/api/models/{model_name}/group_values")
def group_values(model_name: str, field: str, user: CurrentUser = AUTH):
    """All the columns of a kanban, including the empty ones."""
    with transaction(user) as env:
        m = _model(env, model_name)
        f = m.by_name.get(field)
        if not f or not env.can_field(f):
            raise UserError("Unknown field.")
        if f.selection:
            return [[k, label] for k, label in f.selection]
        if f.effective_type == "many2one":
            if not env.can(REGISTRY[f.comodel], "r"):
                return []
            co = env[f.comodel]
            return [[r["id"], r["display_name"]] for r in co.to_api(co.search([], None, 100), ["id"])]
        return []


@app.get("/api/models/{model_name}/defaults")
def defaults(model_name: str, user: CurrentUser = AUTH):
    """Starting values for a new-record form, in the same shape the API reads records."""
    with transaction(user) as env:
        m = _model(env, model_name)
        out = {}
        for f in m.fields:
            if f.default is None or not f.stored or not env.can_field(f):
                continue
            value = f.default(env) if callable(f.default) else f.default
            if value is None:
                continue
            if f.type == "many2one":
                rows = env.sudo()[f.comodel].to_api([value], ["id"])
                value = [value, rows[0]["display_name"]] if rows else None
            elif hasattr(value, "isoformat"):
                value = value.isoformat()
            elif isinstance(value, Decimal):
                value = float(value)
            out[f.name] = value
        return out


@app.post("/api/models/{model_name}/onchange")
def onchange(model_name: str, payload: dict = Body(...), user: CurrentUser = AUTH):
    with transaction(user) as env:
        m = _model(env, model_name)
        if not m.onchange:
            return {}
        return m.onchange(env, payload.get("values") or {}, payload.get("field"), payload.get("parent"))


def _csv_safe(value) -> str:
    """Stops spreadsheet apps from running an exported cell as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") and not _is_number(text) else text


def _is_number(text: str) -> bool:
    try:
        Decimal(text)
        return True
    except ArithmeticError:
        return False


@app.get("/api/models/{model_name}/export.csv")
def export_csv(model_name: str, domain: str | None = None, fields: str = "", order: str | None = None, user: CurrentUser = AUTH):
    with transaction(user) as env:
        recs = env[model_name]
        names = [n for n in fields.split(",") if n and n in recs.m.by_name and recs.m.by_name[n].type not in ("one2many",)]
        if not names:
            raise UserError("Choose the fields to export.")
        rows = recs.to_api(recs.search(_parse_domain(domain), order, 50000), names)
        labels = [recs.m.by_name[n].label or n for n in names]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(labels)
    for row in rows:
        out = []
        for n in names:
            v = row.get(n)
            if isinstance(v, list):
                v = ", ".join(str(x[1]) for x in v) if v and isinstance(v[0], list) else (v[1] if v else "")
            out.append(_csv_safe(v))
        writer.writerow(out)
    filename = f"{model_name.replace('.', '_')}_{date.today().isoformat()}.csv"
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.post("/api/models/{model_name}/import")
def import_csv(model_name: str, file: UploadFile = File(...), user: CurrentUser = AUTH):
    """Headers are field names or labels. Any bad row rejects the whole file, so imports are all or nothing."""
    raw = file.file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(raw) > config.MAX_UPLOAD_BYTES:
        raise UserError("The file is too large.", 413)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UserError("The file must be UTF-8 encoded CSV.") from exc
    with transaction(user) as env:
        recs = env[model_name]
        by_label = {(f.label or f.name).lower(): f for f in recs.m.fields}
        count = 0
        for number, row in enumerate(csv.DictReader(io.StringIO(text)), start=2):
            vals = {}
            for header, value in row.items():
                f = recs.m.by_name.get(header or "") or by_label.get((header or "").strip().lower())
                if not f:
                    raise UserError(f"Unknown column: {header}.")
                if value in (None, ""):
                    continue
                if f.type == "many2one":
                    co = env[f.comodel]
                    found = co.search([[co.m.rec_name, "=", value]], limit=2)
                    if len(found) != 1:
                        raise UserError(f"Line {number}: cannot match {value} for {f.label}.")
                    value = found[0]
                elif f.type == "bool":
                    value = value.strip().lower() in ("1", "true", "yes")
                vals[f.name] = value
            try:
                recs.create(vals)
            except UserError as exc:
                raise UserError(f"Line {number}: {exc.message}", exc.status) from exc
            count += 1
    return {"created": count}


# -- chatter and activities -----------------------------------------------------------------------
def _check_record(env: Env, model_name: str, rec_id: int) -> Model:
    m = _model(env, model_name)
    env[model_name].get(rec_id, ["id"])
    return m


@app.get("/api/mail/{model_name}/{rec_id}")
def chatter(model_name: str, rec_id: int, user: CurrentUser = AUTH):
    with transaction(user) as env:
        _check_record(env, model_name, rec_id)
        env.cur.execute(
            """SELECT m.id, m.kind, m.body, m.tracking, m.created_at, u.name FROM mail_message m LEFT JOIN res_users u ON u.id = m.author_id
               WHERE m.res_model = %s AND m.res_id = %s ORDER BY m.id DESC LIMIT 200""", (model_name, rec_id))
        messages = [{"id": i, "kind": k, "body": b, "tracking": t or [], "date": d.isoformat(), "author": a or "System"} for i, k, b, t, d, a in env.cur.fetchall()]
        env.cur.execute(
            """SELECT a.id, a.name, a.kind, a.date_deadline, u.name, a.user_id FROM mail_activity a LEFT JOIN res_users u ON u.id = a.user_id
               WHERE a.res_model = %s AND a.res_id = %s AND a.done IS NOT TRUE ORDER BY a.date_deadline""", (model_name, rec_id))
        activities = [{"id": i, "name": n, "kind": k, "date_deadline": d.isoformat(), "user": u, "user_id": uid} for i, n, k, d, u, uid in env.cur.fetchall()]
        env.cur.execute("SELECT id, name, size, mimetype FROM ir_attachment WHERE res_model = %s AND res_id = %s ORDER BY id DESC", (model_name, rec_id))
        attachments = [{"id": i, "name": n, "size": s, "mimetype": mt} for i, n, s, mt in env.cur.fetchall()]
    return {"messages": messages, "activities": activities, "attachments": attachments}


@app.post("/api/mail/{model_name}/{rec_id}")
def post_message(model_name: str, rec_id: int, payload: dict = Body(...), user: CurrentUser = AUTH):
    body = str(payload.get("body") or "").strip()
    if not body:
        raise UserError("Write a message first.")
    if len(body) > 20000:
        raise UserError("That message is too long.")
    with transaction(user) as env:
        if not _check_record(env, model_name, rec_id).mail:
            raise UserError("This record has no conversation.")
        env.cur.execute("INSERT INTO mail_message (res_model, res_id, author_id, kind, body) VALUES (%s, %s, %s, 'comment', %s)", (model_name, rec_id, user.id, body))
    return {"ok": True}


@app.post("/api/mail/{model_name}/{rec_id}/activities", status_code=201)
def schedule_activity(model_name: str, rec_id: int, vals: dict = Body(...), user: CurrentUser = AUTH):
    with transaction(user) as env:
        _check_record(env, model_name, rec_id)
        su = env.sudo()
        allowed = {k: vals[k] for k in ("name", "kind", "date_deadline", "user_id", "note") if k in vals}
        return {"id": su["mail.activity"].create({**allowed, "res_model": model_name, "res_id": rec_id, "user_id": allowed.get("user_id") or user.id})}


def _activity_for_user(env: Env, activity_id: int, user: CurrentUser) -> dict:
    su = env.sudo()
    a = su["mail.activity"].get(activity_id)
    if a["user_id"] == user.id:
        return a
    if a["res_model"] and a["res_id"]:
        _check_record(env, a["res_model"], a["res_id"])
        return a
    raise AccessDenied()


@app.post("/api/activities/{activity_id}/done")
def activity_done(activity_id: int, user: CurrentUser = AUTH):
    with transaction(user) as env:
        a = _activity_for_user(env, activity_id, user)
        env.sudo()["mail.activity"].write([activity_id], {"done": True})
        if a["res_model"] and a["res_id"]:
            env.cur.execute("INSERT INTO mail_message (res_model, res_id, author_id, kind, body) VALUES (%s, %s, %s, 'tracking', %s)",
                            (a["res_model"], a["res_id"], user.id, f"Activity done: {a['name']}"))
    return {"ok": True}


@app.get("/api/activities/mine")
def my_activities(user: CurrentUser = AUTH):
    with transaction(user) as env:
        env.cur.execute(
            """SELECT id, name, kind, date_deadline, res_model, res_id FROM mail_activity WHERE user_id = %s AND done IS NOT TRUE ORDER BY date_deadline LIMIT 50""", (user.id,))
        return [{"id": i, "name": n, "kind": k, "date_deadline": d.isoformat(), "res_model": rm, "res_id": rid,
                 "model_label": REGISTRY[rm].label if rm in REGISTRY else ""} for i, n, k, d, rm, rid in env.cur.fetchall()]


# -- attachments ----------------------------------------------------------------------------------
@app.post("/api/attachments", status_code=201)
def upload(file: UploadFile = File(...), res_model: str | None = Form(None), res_id: int | None = Form(None), user: CurrentUser = AUTH):
    raw = file.file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(raw) > config.MAX_UPLOAD_BYTES:
        raise UserError(f"Files can be at most {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.", 413)
    if not raw:
        raise UserError("The file is empty.")
    name = os.path.basename((file.filename or "file").replace("\\", "/"))[:200] or "file"
    with transaction(user) as env:
        if res_model:
            if res_id is None:
                raise UserError("Missing record.")
            _check_record(env, res_model, res_id)
        key = uuid.uuid4().hex
        os.makedirs(config.ATTACHMENT_DIR, exist_ok=True)
        path = os.path.join(config.ATTACHMENT_DIR, key)
        with open(path, "wb") as out:
            out.write(raw)
        try:
            rec_id = env.sudo()["ir.attachment"].create({"name": name, "mimetype": mimetypes.guess_type(name)[0] or "application/octet-stream", "size": len(raw), "storage_key": key, "res_model": res_model, "res_id": res_id})
        except Exception:
            os.unlink(path)
            raise
    return {"id": rec_id, "name": name, "size": len(raw)}


@app.get("/api/attachments/{attachment_id}")
def download(attachment_id: int, user: CurrentUser = AUTH):
    with transaction(user) as env:
        a = env.sudo()["ir.attachment"].get(attachment_id)
        if a["res_model"] and a["res_id"] is not None:
            _check_record(env, a["res_model"], a["res_id"])
        elif a["create_uid"] != user.id and not user.is_admin:
            docs = env["document.document"].search([["attachment_id", "=", attachment_id]], limit=1)
            if not docs:
                raise AccessDenied()
    # Always a download with a fixed type: an uploaded HTML file must never render on this origin.
    return FileResponse(os.path.join(config.ATTACHMENT_DIR, a["storage_key"]), filename=a["name"], media_type="application/octet-stream",
                        headers={"X-Content-Type-Options": "nosniff"})


# -- reports and dashboard ------------------------------------------------------------------------
@app.get("/api/reports/{key}")
def report(key: str, date_from: date | None = None, date_to: date | None = None, user: CurrentUser = AUTH):
    fn = account_module.REPORTS.get(key)
    if not fn:
        raise NotFound("Unknown report.")
    with transaction(user) as env:
        if not env.has_group("account.user", "account.manager"):
            raise AccessDenied("You need accounting access to see this report.")
        return fn(env.sudo(), date_from, date_to)


@app.get("/api/dashboard")
def dashboard(user: CurrentUser = AUTH):
    """KPIs for the apps this user can see. Each one honours that app's access rights."""
    tiles, charts = [], []
    with transaction(user) as env:
        cur = env.cur
        if env.has_group("account.user", "account.manager"):
            cur.execute("""SELECT COALESCE(SUM(amount_untaxed * CASE WHEN move_type = 'out_refund' THEN -1 ELSE 1 END), 0) FROM account_move
                           WHERE state = 'posted' AND move_type IN ('out_invoice', 'out_refund') AND date >= date_trunc('month', CURRENT_DATE)""")
            tiles.append({"label": "Invoiced this month", "value": float(cur.fetchone()[0]), "money": True, "app": "accounting", "action": "invoices"})
            cur.execute("SELECT COALESCE(SUM(amount_residual), 0) FROM account_move WHERE state = 'posted' AND move_type = 'out_invoice'")
            tiles.append({"label": "Receivables", "value": float(cur.fetchone()[0]), "money": True, "app": "accounting", "action": "invoices"})
            cur.execute("SELECT COALESCE(SUM(amount_residual), 0) FROM account_move WHERE state = 'posted' AND move_type = 'in_invoice'")
            tiles.append({"label": "Payables", "value": float(cur.fetchone()[0]), "money": True, "app": "accounting", "action": "bills"})
            cur.execute("""SELECT to_char(m, 'YYYY-MM'),
                  COALESCE((SELECT SUM(amount_untaxed * CASE WHEN move_type = 'out_refund' THEN -1 ELSE 1 END) FROM account_move a WHERE a.state = 'posted' AND a.move_type IN ('out_invoice', 'out_refund') AND date_trunc('month', a.date) = m), 0),
                  COALESCE((SELECT SUM(amount_untaxed * CASE WHEN move_type = 'in_refund' THEN -1 ELSE 1 END) FROM account_move a WHERE a.state = 'posted' AND a.move_type IN ('in_invoice', 'in_refund') AND date_trunc('month', a.date) = m), 0)
                  FROM generate_series(date_trunc('month', CURRENT_DATE) - interval '5 months', date_trunc('month', CURRENT_DATE), interval '1 month') m ORDER BY m""")
            rows = cur.fetchall()
            charts.append({"key": "revenue", "title": "Invoiced and billed, last 6 months", "kind": "bar", "money": True, "labels": [r[0] for r in rows],
                           "series": [{"name": "Invoiced", "values": [float(r[1]) for r in rows]}, {"name": "Billed", "values": [float(r[2]) for r in rows]}]})
        if env.has_group("sales.user", "sales.manager"):
            cur.execute("SELECT COUNT(*), COALESCE(SUM(amount_total), 0) FROM sale_order WHERE state IN ('draft', 'sent')")
            n, total = cur.fetchone()
            tiles.append({"label": "Open quotations", "value": n, "sub": float(total), "app": "sales", "action": "quotations"})
            cur.execute("SELECT COUNT(*) FROM sale_order WHERE invoice_status = 'to_invoice'")
            tiles.append({"label": "Orders to invoice", "value": cur.fetchone()[0], "app": "sales", "action": "to_invoice"})
        if env.has_group("crm.user", "crm.manager"):
            cur.execute("SELECT COUNT(*), COALESCE(SUM(expected_revenue), 0) FROM crm_lead WHERE status = 'open' AND active IS TRUE")
            n, total = cur.fetchone()
            tiles.append({"label": "Open opportunities", "value": n, "sub": float(total), "app": "crm", "action": "pipeline"})
            cur.execute("""SELECT s.name, COALESCE(SUM(l.expected_revenue), 0) FROM crm_stage s LEFT JOIN crm_lead l ON l.stage_id = s.id AND l.status = 'open' AND l.active IS TRUE
                           WHERE s.is_won IS NOT TRUE GROUP BY s.name, s.sequence ORDER BY s.sequence""")
            rows = cur.fetchall()
            charts.append({"key": "pipeline", "title": "Pipeline value by stage", "kind": "hbar", "money": True, "labels": [r[0] for r in rows], "series": [{"name": "Expected revenue", "values": [float(r[1]) for r in rows]}]})
        if env.has_group("stock.user", "stock.manager"):
            cur.execute("SELECT COUNT(*) FROM stock_picking WHERE state = 'assigned'")
            tiles.append({"label": "Transfers ready", "value": cur.fetchone()[0], "app": "inventory", "action": "overview"})
        if env.has_group("purchase.user", "purchase.manager"):
            cur.execute("SELECT COUNT(*) FROM purchase_order WHERE state IN ('draft', 'sent')")
            tiles.append({"label": "Open RFQs", "value": cur.fetchone()[0], "app": "purchase", "action": "rfq"})
        if env.has_group("helpdesk.user", "helpdesk.manager"):
            cur.execute("SELECT COUNT(*) FROM helpdesk_ticket k LEFT JOIN helpdesk_stage s ON s.id = k.stage_id WHERE s.is_closed IS NOT TRUE")
            tiles.append({"label": "Open tickets", "value": cur.fetchone()[0], "app": "helpdesk", "action": "tickets"})
        cur.execute("""SELECT COUNT(*) FROM project_task k JOIN project_task_users u ON u.project_task_id = k.id LEFT JOIN project_task_stage s ON s.id = k.stage_id
                       WHERE u.res_users_id = %s AND s.fold IS NOT TRUE AND k.active IS TRUE""", (user.id,))
        tiles.append({"label": "My open tasks", "value": cur.fetchone()[0], "app": "project", "action": "my_tasks"})
    return {"tiles": tiles, "charts": charts}


# -- point of sale --------------------------------------------------------------------------------
def _pos_env_check(env: Env):
    if not env.has_group("pos.user", "pos.manager"):
        raise AccessDenied("You need point of sale access.")


@app.get("/api/pos/bootstrap")
def pos_bootstrap(user: CurrentUser = AUTH):
    with transaction(user) as env:
        _pos_env_check(env)
        su = env.sudo()
        configs = su["pos.config"].to_api(su["pos.config"].search([]), ["id", "name"])
        sessions = su["pos.session"].to_api(su["pos.session"].search([["state", "=", "opened"]]), ["id", "name", "config_id", "total_sales", "order_count"])
        prods = su["product.product"]
        products = prods.to_api(prods.search([["pos_ok", "=", True]], "name ASC", 500), ["id", "name", "list_price", "category_id", "default_code", "barcode", "sale_tax_id", "qty_available", "product_type"])
        env.cur.execute("SELECT id, rate FROM account_tax")
        taxes = {str(i): float(r) for i, r in env.cur.fetchall()}
    return {"configs": configs, "sessions": sessions, "products": products, "taxes": taxes}


@app.post("/api/pos/session")
def pos_open(payload: dict = Body(...), user: CurrentUser = AUTH):
    with transaction(user) as env:
        _pos_env_check(env)
        return {"id": pos.open_session(env, int(payload["config_id"]), payload.get("opening_cash") or 0)}


@app.post("/api/pos/session/{session_id}/close")
def pos_close(session_id: int, user: CurrentUser = AUTH):
    with transaction(user) as env:
        _pos_env_check(env)
        pos.close_session(env, session_id)
    return {"ok": True}


@app.post("/api/pos/order", status_code=201)
def pos_order(payload: dict = Body(...), user: CurrentUser = AUTH):
    with transaction(user) as env:
        _pos_env_check(env)
        order_id = pos.create_order(env, int(payload["session_id"]), payload.get("lines") or [], str(payload.get("payment_method")), payload.get("amount_paid"), payload.get("partner_id"))
        su = env.sudo()
        created = su["pos.order"].to_api([order_id], ["id", "name", "date_order", "amount_untaxed", "amount_tax", "amount_total", "amount_paid", "amount_return", "payment_method", "line_ids"])[0]
    return created


# -- attendance -----------------------------------------------------------------------------------
@app.get("/api/attendance")
def attendance_get(user: CurrentUser = AUTH):
    with transaction(user) as env:
        return hr.attendance_status(env)


@app.post("/api/attendance/toggle")
def attendance_toggle(user: CurrentUser = AUTH):
    with transaction(user) as env:
        return hr.toggle_attendance(env)


# -- discuss --------------------------------------------------------------------------------------
def _channel_access(env: Env, channel_id: int) -> dict:
    # Goes through the channel record rule: private channels exist only for their members.
    return env["discuss.channel"].get(channel_id, ["id", "name", "kind"])


@app.get("/api/discuss/channels")
def discuss_channels(user: CurrentUser = AUTH):
    with transaction(user) as env:
        env.cur.execute(
            """SELECT c.id, c.name, c.kind, c.description,
                 EXISTS (SELECT 1 FROM discuss_channel_users u WHERE u.discuss_channel_id = c.id AND u.res_users_id = %(u)s),
                 (SELECT COUNT(*) FROM discuss_channel_users u WHERE u.discuss_channel_id = c.id),
                 (SELECT string_agg(x.name, ', ') FROM discuss_channel_users u JOIN res_users x ON x.id = u.res_users_id WHERE u.discuss_channel_id = c.id AND x.id != %(u)s)
               FROM discuss_channel c
               WHERE c.kind = 'channel' OR EXISTS (SELECT 1 FROM discuss_channel_users u WHERE u.discuss_channel_id = c.id AND u.res_users_id = %(u)s)
               ORDER BY c.kind, c.name""", {"u": user.id})
        rows = []
        for i, n, k, d, member, count, others in env.cur.fetchall():
            rows.append({"id": i, "name": (others or "Just you") if k == "dm" else n, "kind": k, "description": d, "member": member, "members": count})
        users = env["res.users"].to_api(env["res.users"].search([["id", "!=", user.id]], None, 200), ["id", "name"])
    return {"channels": rows, "users": [[u["id"], u["name"]] for u in users]}


@app.post("/api/discuss/channels", status_code=201)
def discuss_create(payload: dict = Body(...), user: CurrentUser = AUTH):
    kind = payload.get("kind") or "channel"
    with transaction(user) as env:
        su = env.sudo()
        if kind == "dm":
            other = int(payload["user_id"])
            su["res.users"].get(other, ["id"])
            env.cur.execute(
                """SELECT c.id FROM discuss_channel c WHERE c.kind = 'dm'
                   AND EXISTS (SELECT 1 FROM discuss_channel_users u WHERE u.discuss_channel_id = c.id AND u.res_users_id = %s)
                   AND EXISTS (SELECT 1 FROM discuss_channel_users u WHERE u.discuss_channel_id = c.id AND u.res_users_id = %s) LIMIT 1""", (user.id, other))
            row = env.cur.fetchone()
            if row:
                return {"id": row[0]}
            return {"id": su["discuss.channel"].create({"name": "Direct Message", "kind": "dm", "member_ids": [user.id, other]})}
        if kind not in ("channel", "group"):
            raise UserError("Unknown channel type.")
        name = str(payload.get("name") or "").strip()
        if not name:
            raise UserError("Name the channel first.")
        return {"id": su["discuss.channel"].create({"name": name, "kind": kind, "description": payload.get("description"), "member_ids": [user.id]})}


@app.get("/api/discuss/channels/{channel_id}/messages")
def discuss_messages(channel_id: int, after: int = 0, user: CurrentUser = AUTH):
    with transaction(user) as env:
        _channel_access(env, channel_id)
        env.cur.execute(
            """SELECT * FROM (SELECT m.id, m.body, m.create_date, m.author_id, u.name FROM discuss_message m LEFT JOIN res_users u ON u.id = m.author_id
               WHERE m.channel_id = %s AND m.id > %s ORDER BY m.id DESC LIMIT 200) x ORDER BY id""", (channel_id, after))
        return [{"id": i, "body": b, "date": d.isoformat(), "author_id": aid, "author": a or "Former user"} for i, b, d, aid, a in env.cur.fetchall()]


@app.post("/api/discuss/channels/{channel_id}/messages", status_code=201)
def discuss_post(channel_id: int, payload: dict = Body(...), user: CurrentUser = AUTH):
    body = str(payload.get("body") or "").strip()
    if not body or len(body) > 10000:
        raise UserError("Messages must be between 1 and 10000 characters.")
    with transaction(user) as env:
        _channel_access(env, channel_id)
        env.cur.execute("INSERT INTO discuss_channel_users (discuss_channel_id, res_users_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (channel_id, user.id))
        return {"id": env.sudo()["discuss.message"].create({"channel_id": channel_id, "author_id": user.id, "body": body})}


# -- public ---------------------------------------------------------------------------------------
def _unsubscribe_page(message: str, form: str = "") -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>Unsubscribe</title><body><p>{message}</p>{form}</body>")


@app.get("/api/public/unsubscribe/{token}")
def unsubscribe_page(token: str):
    # A page with a button, not an action on GET: mail scanners follow every link they see.
    if not token.replace("-", "").replace("_", "").isalnum():
        raise NotFound()
    return _unsubscribe_page("Stop receiving these emails?", f"<form method=post action='/api/public/unsubscribe/{token}'><button>Unsubscribe</button></form>")


@app.post("/api/public/unsubscribe/{token}")
def unsubscribe_post(token: str):
    with transaction() as env:
        found = marketing.unsubscribe(env, token)
    return _unsubscribe_page("You have been unsubscribed." if found else "This link is not valid.")
