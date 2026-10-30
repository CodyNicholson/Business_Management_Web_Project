"""A small metadata-driven ORM: models declared in Python produce the database schema, the
generic REST API and the views the frontend renders. All SQL identifiers come from the registry
and all values are bound parameters, so client input never reaches SQL text."""
from __future__ import annotations

import json
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable


class UserError(Exception):
    """A business rule violation shown to the user as-is."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


class AccessDenied(UserError):
    def __init__(self, message: str = "You do not have access to this."):
        super().__init__(message, 403)


class NotFound(UserError):
    def __init__(self, message: str = "Record not found."):
        super().__init__(message, 404)


def money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def dec(value: Any) -> Decimal:
    return Decimal(str(value or 0))


# ---------------------------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------------------------
COLUMN_TYPES = {
    "char": "TEXT", "text": "TEXT", "int": "INTEGER", "float": "NUMERIC(16,4)",
    "money": "NUMERIC(16,2)", "bool": "BOOLEAN", "date": "DATE", "datetime": "TIMESTAMPTZ",
    "selection": "TEXT", "many2one": "INTEGER", "file": "INTEGER",
}
VIRTUAL_TYPES = {"one2many", "many2many"}


@dataclass
class Field:
    name: str
    type: str
    label: str = ""
    required: bool = False
    readonly: bool = False
    default: Any = None
    comodel: str | None = None
    inverse: str | None = None  # one2many: the many2one on the comodel
    relation: str | None = None  # many2many: join table name
    selection: list[tuple[str, str]] | None = None
    tracked: bool = False
    sql: str | None = None  # read-only computed value: SQL expression over alias t
    result_type: str | None = None  # the display type of a sql field
    ondelete: str = "RESTRICT"
    index: bool = False
    domain: list | None = None  # many2one: restricts the choices offered
    groups: list[str] | None = None  # field visible to these groups only
    onchange: bool = False
    help: str = ""
    columns: list[str] | None = None  # one2many: columns of the inline table
    sequence: str | None = None  # char: filled from this sequence code on create

    @property
    def stored(self) -> bool:
        return self.sql is None and self.type not in VIRTUAL_TYPES

    @property
    def effective_type(self) -> str:
        return self.result_type or self.type


class F:
    """Field shorthands: F.char("name", "Name", required=True)."""

    @staticmethod
    def char(name, label="", **kw): return Field(name, "char", label, **kw)
    @staticmethod
    def text(name, label="", **kw): return Field(name, "text", label, **kw)
    @staticmethod
    def int(name, label="", **kw): return Field(name, "int", label, **kw)
    @staticmethod
    def float(name, label="", **kw): return Field(name, "float", label, **kw)
    @staticmethod
    def money(name, label="", **kw): return Field(name, "money", label, **kw)
    @staticmethod
    def bool(name, label="", **kw): return Field(name, "bool", label, **kw)
    @staticmethod
    def date(name, label="", **kw): return Field(name, "date", label, **kw)
    @staticmethod
    def datetime(name, label="", **kw): return Field(name, "datetime", label, **kw)
    @staticmethod
    def file(name, label="", **kw): return Field(name, "file", label, comodel="ir.attachment", **kw)

    @staticmethod
    def sel(name, label, options, **kw):
        return Field(name, "selection", label, selection=list(options), **kw)

    @staticmethod
    def m2o(name, label, comodel, **kw):
        return Field(name, "many2one", label, comodel=comodel, index=True, **kw)

    @staticmethod
    def o2m(name, label, comodel, inverse, **kw):
        return Field(name, "one2many", label, comodel=comodel, inverse=inverse, **kw)

    @staticmethod
    def m2m(name, label, comodel, relation, **kw):
        return Field(name, "many2many", label, comodel=comodel, relation=relation, **kw)

    @staticmethod
    def sql(name, label, result_type, expr, **kw):
        return Field(name, "sql", label, sql=expr, result_type=result_type, readonly=True, **kw)


# ---------------------------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------------------------
@dataclass
class Action:
    key: str
    label: str
    fn: Callable
    groups: list[str] | None = None
    params: list[dict] | None = None  # shown in a dialog before running
    confirm: str | None = None


@dataclass
class Model:
    name: str
    label: str
    fields: list[Field]
    table: str = ""
    rec_name: str = "name"
    order: str = "id DESC"
    access: dict[str, str] = dc_field(default_factory=dict)  # group -> subset of "rwcd"
    rule: Callable | None = None  # env -> domain, applied unless the user has a bypass group
    rule_bypass: list[str] = dc_field(default_factory=list)
    mail: bool = False  # chatter + field tracking
    locked_when: list | None = None  # record cannot be edited through the API under this domain
    views: dict = dc_field(default_factory=dict)
    constraints: list[tuple[str, str]] = dc_field(default_factory=list)
    search_fields: list[str] = dc_field(default_factory=list)
    parent: str | None = None  # the many2one whose record is recomputed when this one changes
    hooks: dict[str, Callable] = dc_field(default_factory=dict)
    actions: dict[str, Action] = dc_field(default_factory=dict)
    onchange: Callable | None = None

    def __post_init__(self):
        self.table = self.table or self.name.replace(".", "_")
        self.by_name = {f.name: f for f in self.fields}
        for name, label, ftype in (("create_date", "Created on", "datetime"),
                                   ("write_date", "Last updated", "datetime")):
            self.by_name.setdefault(name, Field(name, ftype, label, readonly=True))
        self.by_name.setdefault("create_uid", Field("create_uid", "many2one", "Created by",
                                                     comodel="res.users", readonly=True, ondelete="SET NULL"))
        self.by_name["id"] = Field("id", "int", "ID", readonly=True)
        self.has_active = "active" in self.by_name

    def hook(self, name: str):
        def deco(fn):
            self.hooks[name] = fn
            return fn
        return deco

    def action(self, key, label, groups=None, params=None, confirm=None):
        def deco(fn):
            self.actions[key] = Action(key, label, fn, groups, params, confirm)
            return fn
        return deco

    def set_onchange(self, fn):
        self.onchange = fn
        return fn


REGISTRY: dict[str, Model] = {}
GROUPS: dict[str, str] = {}  # key -> label
APPS: list[dict] = []


def define_groups(*items: tuple[str, str]):
    for key, label in items:
        GROUPS[key] = label


def model(name: str, label: str, fields: list[Field], **kw) -> Model:
    m = Model(name, label, fields, **kw)
    REGISTRY[name] = m
    return m


def app(key: str, name: str, icon: str, color: str, menus: list[dict], groups: list[str] | None = None,
        sequence: int = 100):
    APPS.append({"key": key, "name": name, "icon": icon, "color": color, "menus": menus,
                 "groups": groups or ["base.user"], "sequence": sequence})


def menu(key, label, model=None, views=("list", "form"), domain=None, context=None, client=None,
         groups=None, children=None):
    return {"key": key, "label": label, "model": model, "views": list(views), "domain": domain or [],
            "context": context or {}, "client": client, "groups": groups, "children": children}


# ---------------------------------------------------------------------------------------------
# Domains -> SQL
# ---------------------------------------------------------------------------------------------
OPERATORS = {"=", "!=", ">", ">=", "<", "<=", "in", "not in", "ilike", "not ilike"}


def _like(value: Any) -> str:
    escaped = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _is_empty(value: Any) -> bool:
    return value is None or value is False


class DomainCompiler:
    def __init__(self, env: "Env"):
        self.env = env
        self.alias_n = 0

    def alias(self) -> str:
        self.alias_n += 1
        return f"d{self.alias_n}"

    def compile(self, m: Model, domain: list, alias: str) -> tuple[str, list]:
        if not domain:
            return "TRUE", []
        if not isinstance(domain, list):
            raise UserError("Invalid domain.")
        stack: list[tuple[str, list]] = []
        items = list(domain)
        # Odoo-style Polish notation; anything left at the end is AND-ed.
        for item in reversed(items):
            if item in ("&", "|"):
                if len(stack) < 2:
                    raise UserError("Invalid domain.")
                a, b = stack.pop(), stack.pop()
                joiner = "AND" if item == "&" else "OR"
                stack.append((f"({a[0]} {joiner} {b[0]})", a[1] + b[1]))
            elif item == "!":
                if not stack:
                    raise UserError("Invalid domain.")
                a = stack.pop()
                stack.append((f"(NOT {a[0]})", a[1]))
            elif isinstance(item, (list, tuple)) and len(item) == 3:
                stack.append(self.leaf(m, item[0], item[1], item[2], alias))
            else:
                raise UserError("Invalid domain.")
        stack.reverse()
        return " AND ".join(s for s, _ in stack), [p for _, ps in stack for p in ps]

    def leaf(self, m: Model, path: Any, op: Any, value: Any, alias: str) -> tuple[str, list]:
        if not isinstance(path, str) or op not in OPERATORS:
            raise UserError("Invalid domain.")
        head, _, rest = path.partition(".")
        f = m.by_name.get(head)
        if not f:
            raise UserError(f"Unknown field {head} on {m.name}.")
        self.env.check_field(m, f)
        if f.type in ("one2many", "many2many"):
            return self.relational_leaf(m, f, rest or "id", op, value, alias)
        if rest:
            if f.type != "many2one":
                raise UserError(f"Cannot traverse {head}.")
            co = REGISTRY[f.comodel]
            self.env.check_access(co, "r")
            sub = self.alias()
            where, params = self.leaf(co, rest, op, value, sub)
            return f'{alias}."{head}" IN (SELECT {sub}.id FROM "{co.table}" {sub} WHERE {where})', params
        expr = f"({f.sql.replace('{t}', alias)})" if f.sql else f'{alias}."{head}"'
        return self.scalar(f, expr, op, value)

    def relational_leaf(self, m, f, rest, op, value, alias):
        co = REGISTRY[f.comodel]
        self.env.check_access(co, "r")
        sub = self.alias()
        negate = op in ("!=", "not in", "not ilike")
        positive = {"!=": "=", "not in": "in", "not ilike": "ilike"}.get(op, op)
        where, params = self.leaf(co, rest, positive, value, sub)
        if f.type == "one2many":
            exists = f'EXISTS (SELECT 1 FROM "{co.table}" {sub} WHERE {sub}."{f.inverse}" = {alias}.id AND {where})'
        else:
            left, right = m2m_columns(m, f)
            exists = (f'EXISTS (SELECT 1 FROM "{f.relation}" r JOIN "{co.table}" {sub} ON {sub}.id = r."{right}" '
                      f'WHERE r."{left}" = {alias}.id AND {where})')
        return (f"NOT {exists}" if negate else exists), params

    @staticmethod
    def scalar(f: Field, expr: str, op: str, value: Any) -> tuple[str, list]:
        if op in ("in", "not in"):
            if not isinstance(value, (list, tuple)):
                raise UserError("Invalid domain.")
            pg = {"many2one": "int[]", "int": "int[]", "file": "int[]"}.get(f.effective_type, "text[]")
            text_expr = expr if pg == "int[]" else f"{expr}::text"
            vals = [int(v) for v in value] if pg == "int[]" else [str(v) for v in value]
            clause = f"{text_expr} = ANY(%s::{pg})"
            return (clause if op == "in" else f"NOT COALESCE({clause}, FALSE)"), [vals]
        if op in ("ilike", "not ilike"):
            sql_op = "ILIKE" if op == "ilike" else "NOT ILIKE"
            return f"{expr}::text {sql_op} %s", [_like(value)]
        if f.effective_type == "bool":
            truthy = bool(value)
            if op == "=":
                return (f"{expr} IS TRUE" if truthy else f"{expr} IS NOT TRUE"), []
            if op == "!=":
                return (f"{expr} IS NOT TRUE" if truthy else f"{expr} IS TRUE"), []
            raise UserError("Invalid domain.")
        if _is_empty(value):
            if op == "=":
                return f"{expr} IS NULL", []
            if op == "!=":
                return f"{expr} IS NOT NULL", []
            raise UserError("Invalid domain.")
        if op == "!=":
            return f"{expr} IS DISTINCT FROM %s", [value]
        return f"{expr} {op} %s", [value]


def m2m_columns(m: Model, f: Field) -> tuple[str, str]:
    left = f"{m.table}_id"
    right = f"{REGISTRY[f.comodel].table}_id"
    if left == right:  # self-referencing relation
        right = f"other_{right}"
    return left, right


# ---------------------------------------------------------------------------------------------
# Environment and recordset operations
# ---------------------------------------------------------------------------------------------
@dataclass
class CurrentUser:
    id: int
    name: str
    email: str
    groups: set[str]

    @property
    def is_admin(self) -> bool:
        return "base.admin" in self.groups


SYSTEM_USER = CurrentUser(0, "System", "", {"base.admin", "base.user"})


class Env:
    def __init__(self, cur, user: CurrentUser, su: bool = False):
        self.cur = cur
        self.user = user
        self.su = su

    def sudo(self) -> "Env":
        return Env(self.cur, self.user, True)

    def __getitem__(self, name: str) -> "Records":
        m = REGISTRY.get(name)
        if not m:
            raise NotFound(f"Unknown model {name}.")
        return Records(self, m)

    def has_group(self, *groups: str) -> bool:
        return self.su or self.user.is_admin or bool(self.user.groups.intersection(groups))

    def can(self, m: Model, op: str) -> bool:
        if self.su or self.user.is_admin:
            return True
        return any(op in ops and g in self.user.groups for g, ops in m.access.items())

    def check_access(self, m: Model, op: str):
        if not self.can(m, op):
            verb = {"r": "read", "w": "edit", "c": "create", "d": "delete"}[op]
            raise AccessDenied(f"You are not allowed to {verb} {m.label} records.")

    def can_field(self, f: Field) -> bool:
        return not f.groups or self.has_group(*f.groups)

    def check_field(self, m: Model, f: Field):
        if not self.can_field(f):
            raise AccessDenied(f"You are not allowed to use the field {f.label or f.name}.")

    def rule_domain(self, m: Model) -> list:
        if self.su or self.user.is_admin or not m.rule:
            return []
        if self.user.groups.intersection(m.rule_bypass):
            return []
        return m.rule(self)

    def next_sequence(self, code: str, prefix: str, padding: int = 5) -> str:
        self.cur.execute(
            """INSERT INTO ir_sequence (code, number) VALUES (%s, 1)
               ON CONFLICT (code) DO UPDATE SET number = ir_sequence.number + 1 RETURNING number""",
            (code,))
        return f"{prefix}{self.cur.fetchone()[0]:0{padding}d}"

    def company(self) -> dict:
        return self.sudo()["res.company"].read([1])[0]


class Records:
    def __init__(self, env: Env, m: Model):
        self.env = env
        self.m = m

    # -- search -----------------------------------------------------------------------------
    def _where(self, domain: list | None, alias: str = "t") -> tuple[str, list]:
        domain = list(domain or [])
        compiler = DomainCompiler(self.env)
        where, params = compiler.compile(self.m, domain, alias)
        if self.m.has_active and not _mentions(domain, "active"):
            where = f"({where}) AND {alias}.active IS TRUE"
        rule = self.env.rule_domain(self.m)
        if rule:
            # Rules reference models the user may not be able to read, so they compile as sudo.
            rule_sql, rule_params = DomainCompiler(self.env.sudo()).compile(self.m, rule, alias)
            where = f"({where}) AND ({rule_sql})"
            params = params + rule_params
        return where, params

    def _order(self, order: str | None) -> str:
        parts = []
        for chunk in (order or self.m.order).split(","):
            bits = chunk.strip().split()
            if not bits:
                continue
            name, direction = bits[0], (bits[1].upper() if len(bits) > 1 else "ASC")
            f = self.m.by_name.get(name)
            if not f or f.type in VIRTUAL_TYPES or direction not in ("ASC", "DESC") or not self.env.can_field(f):
                raise UserError(f"Invalid sort order {chunk.strip()}.")
            if f.type == "many2one":
                co = REGISTRY[f.comodel]
                expr = f'(SELECT x."{co.rec_name}"::text FROM "{co.table}" x WHERE x.id = t."{name}")'
            else:
                expr = f"({f.sql.replace('{t}', 't')})" if f.sql else f't."{name}"'
            parts.append(f"{expr} {direction} NULLS LAST")
        return ", ".join(parts + ["t.id DESC"])

    def search(self, domain=None, order=None, limit=None, offset=0) -> list[int]:
        self.env.check_access(self.m, "r")
        where, params = self._where(domain)
        q = f'SELECT t.id FROM "{self.m.table}" t WHERE {where} ORDER BY {self._order(order)}'
        if limit is not None:
            q += " LIMIT %s OFFSET %s"
            params = params + [int(limit), int(offset)]
        self.env.cur.execute(q, params)
        return [r[0] for r in self.env.cur.fetchall()]

    def count(self, domain=None) -> int:
        self.env.check_access(self.m, "r")
        where, params = self._where(domain)
        self.env.cur.execute(f'SELECT COUNT(*) FROM "{self.m.table}" t WHERE {where}', params)
        return self.env.cur.fetchone()[0]

    def read_group(self, domain, group_by: list[str], measures: list[str]) -> list[dict]:
        self.env.check_access(self.m, "r")
        where, params = self._where(domain)
        selects, groups, keys = [], [], []
        for spec in group_by:
            name, _, gran = spec.partition(":")
            f = self.m.by_name.get(name)
            if not f or f.type in VIRTUAL_TYPES:
                raise UserError(f"Cannot group by {name}.")
            self.env.check_field(self.m, f)
            expr = f"({f.sql.replace('{t}', 't')})" if f.sql else f't."{name}"'
            if f.effective_type in ("date", "datetime"):
                gran = gran or "month"
                if gran not in ("day", "week", "month", "quarter", "year"):
                    raise UserError("Invalid date grouping.")
                expr = f"date_trunc('{gran}', {expr})::date"
            selects.append(expr)
            groups.append(expr)
            keys.append((spec, f))
        agg = []
        for spec in measures:
            name, _, fn = spec.partition(":")
            fn = fn or "sum"
            f = self.m.by_name.get(name)
            if not f or f.effective_type not in ("int", "float", "money") or fn not in ("sum", "avg", "min", "max"):
                raise UserError(f"Invalid measure {spec}.")
            self.env.check_field(self.m, f)
            expr = f"({f.sql.replace('{t}', 't')})" if f.sql else f't."{name}"'
            agg.append((name, f"{fn.upper()}({expr})"))
        cols = selects + ["COUNT(*)"] + [a[1] for a in agg]
        q = f'SELECT {", ".join(cols)} FROM "{self.m.table}" t WHERE {where}'
        if groups:
            q += " GROUP BY " + ", ".join(str(i + 1) for i in range(len(groups)))
            q += " ORDER BY " + ", ".join(str(i + 1) for i in range(len(groups)))
        self.env.cur.execute(q, params)
        rows = []
        for row in self.env.cur.fetchall():
            out: dict[str, Any] = {}
            for i, (spec, f) in enumerate(keys):
                out[spec] = self._group_key(f, row[i])
            out["__count"] = row[len(keys)]
            for j, (name, _) in enumerate(agg):
                val = row[len(keys) + 1 + j]
                out[name] = float(val) if val is not None else 0
            rows.append(out)
        return rows

    def _group_key(self, f: Field, value):
        if f.effective_type == "many2one" and value is not None:
            return [value, self._names(REGISTRY[f.comodel], [value]).get(value, "")]
        if f.effective_type == "selection":
            return [value, dict(f.selection or []).get(value, value)] if value is not None else None
        return _jsonable(value)

    # -- read -------------------------------------------------------------------------------
    def read(self, ids: list[int], fields: list[str] | None = None) -> list[dict]:
        """Raw values for internal use: many2one as an id, numbers as Decimal."""
        if not ids:
            return []
        self.env.check_access(self.m, "r")
        names = fields or [f.name for f in self.m.by_name.values()
                           if f.type not in VIRTUAL_TYPES and self.env.can_field(f)]
        cols = []
        for n in names:
            f = self.m.by_name.get(n)
            if not f:
                raise UserError(f"Unknown field {n}.")
            self.env.check_field(self.m, f)
            if f.type in VIRTUAL_TYPES:
                continue
            cols.append(f"({f.sql.replace('{t}', 't')}) AS \"{n}\"" if f.sql else f't."{n}"')
        if "id" not in names:
            cols.append("t.id")
        where, params = self._where([["id", "in", list(ids)]] + (
            [["active", "in", ["true", "false"]]] if self.m.has_active else []))
        self.env.cur.execute(f'SELECT {", ".join(cols)} FROM "{self.m.table}" t WHERE {where}', params)
        desc = [d.name for d in self.env.cur.description]
        by_id = {}
        for row in self.env.cur.fetchall():
            rec = dict(zip(desc, row))
            by_id[rec["id"]] = rec
        return [by_id[i] for i in ids if i in by_id]

    def get(self, rec_id: int, fields=None) -> dict:
        rows = self.read([rec_id], fields)
        if not rows:
            raise NotFound(f"{self.m.label} {rec_id} not found.")
        return rows[0]

    def search_read(self, domain=None, fields=None, order=None, limit=None, offset=0) -> list[dict]:
        return self.read(self.search(domain, order, limit, offset), fields)

    def _names(self, co: Model, ids: list[int]) -> dict[int, str]:
        if not ids:
            return {}
        self.env.cur.execute(
            f'SELECT id, "{co.rec_name}"::text FROM "{co.table}" WHERE id = ANY(%s)', (list(set(ids)),))
        return dict(self.env.cur.fetchall())

    def to_api(self, ids: list[int], fields: list[str] | None = None) -> list[dict]:
        """JSON-ready values: many2one as [id, name], relations expanded, display_name added."""
        if not ids:
            return []
        self.env.check_access(self.m, "r")
        names = fields or [f.name for f in self.m.by_name.values() if self.env.can_field(f)]
        names = [n for n in names if self.m.by_name.get(n) and self.env.can_field(self.m.by_name[n])]
        scalar = [n for n in names if self.m.by_name[n].type not in VIRTUAL_TYPES]
        rec_name = self.m.rec_name
        rows = self.read(ids, list(dict.fromkeys(scalar + [rec_name, "id"])))
        m2o_values: dict[str, dict[int, str]] = {}
        for n in scalar:
            f = self.m.by_name[n]
            if f.type == "many2one":
                m2o_values[n] = self._names(REGISTRY[f.comodel], [r[n] for r in rows if r[n]])
            elif f.type == "file":
                vals = [r[n] for r in rows if r[n]]
                m2o_values[n] = self._names(REGISTRY["ir.attachment"], vals)
        out = []
        for r in rows:
            item: dict[str, Any] = {"id": r["id"], "display_name": str(r[rec_name] or "")}
            for n in scalar:
                f = self.m.by_name[n]
                v = r.get(n)
                if f.type in ("many2one", "file"):
                    item[n] = [v, m2o_values[n].get(v, "")] if v else None
                else:
                    item[n] = _jsonable(v)
            out.append(item)
        for n in names:
            f = self.m.by_name[n]
            if f.type == "one2many":
                self._expand_o2m(f, out, n)
            elif f.type == "many2many":
                self._expand_m2m(f, out, n)
        return out

    def _expand_o2m(self, f: Field, out: list[dict], name: str):
        co = REGISTRY[f.comodel]
        if not self.env.can(co, "r"):
            for item in out:
                item[name] = []
            return
        for item in out:
            child = Records(self.env, co)
            ids = child.search([[f.inverse, "=", item["id"]]])
            item[name] = child.to_api(ids, f.columns + ["id"] if f.columns else ["id", co.rec_name])

    def _expand_m2m(self, f: Field, out: list[dict], name: str):
        co = REGISTRY[f.comodel]
        left, right = m2m_columns(self.m, f)
        for item in out:
            self.env.cur.execute(
                f'SELECT x.id, x."{co.rec_name}"::text FROM "{f.relation}" r JOIN "{co.table}" x '
                f'ON x.id = r."{right}" WHERE r."{left}" = %s ORDER BY 2', (item["id"],))
            item[name] = [[i, n] for i, n in self.env.cur.fetchall()]

    # -- write ------------------------------------------------------------------------------
    def _clean(self, vals: dict, creating: bool) -> tuple[dict, dict]:
        """Validate values. Returns stored columns and relational commands separately."""
        stored, relational = {}, {}
        for key, value in vals.items():
            f = self.m.by_name.get(key)
            if not f:
                raise UserError(f"Unknown field {key} on {self.m.label}.")
            if not self.env.su:
                self.env.check_field(self.m, f)
                if f.readonly or f.sql:
                    raise UserError(f"The field {f.label or key} is read-only.")
            if f.sql or key in ("id",):
                raise UserError(f"The field {f.label or key} cannot be set.")
            if f.type in VIRTUAL_TYPES:
                relational[key] = value
                continue
            stored[key] = self._coerce(f, value)
        return stored, relational

    def _coerce(self, f: Field, value: Any) -> Any:
        if value is False and f.type != "bool":
            value = None
        if value == "" and f.type not in ("char", "text"):
            value = None
        if value is None or value == "":
            if f.required and not (f.sequence and value in (None, "")):
                raise UserError(f"{f.label or f.name} is required.")
            return None
        if f.type in ("many2one", "file"):
            if isinstance(value, (list, tuple)):
                value = value[0] if value else None
            if isinstance(value, bool) or not isinstance(value, int):
                raise UserError(f"{f.label or f.name} must reference a record.")
            return value
        if f.type == "selection":
            if value not in dict(f.selection or []):
                raise UserError(f"{value} is not a valid value for {f.label or f.name}.")
            return value
        if f.type == "int":
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise UserError(f"{f.label or f.name} must be a whole number.") from exc
        if f.type in ("float", "money"):
            try:
                d = Decimal(str(value))
            except Exception as exc:
                raise UserError(f"{f.label or f.name} must be a number.") from exc
            if not d.is_finite():
                raise UserError(f"{f.label or f.name} must be a number.")
            return d
        if f.type == "bool":
            return bool(value)
        if f.type in ("char", "text"):
            return str(value)
        return value

    def create(self, vals: dict) -> int:
        env = self.env
        env.check_access(self.m, "c")
        vals = dict(vals)
        # Checked before defaults and hooks fill in server-owned fields such as state.
        if not env.su:
            for key in vals:
                f = self.m.by_name.get(key)
                if f:
                    env.check_field(self.m, f)
                    if f.readonly or f.sql:
                        raise UserError(f"The field {f.label or key} is read-only.")
        self._check_parent_locked(vals.get(self.m.parent) if self.m.parent else None)
        for f in self.m.by_name.values():
            if f.name not in vals and f.default is not None and f.stored:
                vals[f.name] = f.default(env) if callable(f.default) else f.default
        if "prepare_create" in self.m.hooks:
            vals = self.m.hooks["prepare_create"](env, vals) or vals
        stored, relational = Records(env.sudo(), self.m)._clean(vals, True)
        for f in self.m.by_name.values():
            if f.sequence and not stored.get(f.name):
                stored[f.name] = env.next_sequence(f.sequence, _seq_prefix(f.sequence))
            if f.required and f.stored and stored.get(f.name) in (None, ""):
                raise UserError(f"{f.label or f.name} is required.")
        if env.user.id:
            stored["create_uid"] = env.user.id
        cols = list(stored)
        placeholders = ", ".join(["%s"] * len(cols))
        col_sql = ", ".join(f'"{c}"' for c in cols)
        q = (f'INSERT INTO "{self.m.table}" ({col_sql}) VALUES ({placeholders}) RETURNING id' if cols
             else f'INSERT INTO "{self.m.table}" DEFAULT VALUES RETURNING id')
        env.cur.execute(q, [stored[c] for c in cols])
        rec_id = env.cur.fetchone()[0]
        self._write_relational(rec_id, relational)
        self._check_rule([rec_id])
        if "after_create" in self.m.hooks:
            self.m.hooks["after_create"](env, rec_id, vals)
        self._recompute([rec_id])
        if self.m.mail:
            post_tracking(env, self.m, rec_id, f"{self.m.label} created", [])
        return rec_id

    def write(self, ids: list[int], vals: dict) -> None:
        if not ids:
            return
        env = self.env
        env.check_access(self.m, "w")
        self._check_rule(ids)
        if not env.su and self.m.locked_when:
            locked = Records(env.sudo(), self.m).search(
                [["id", "in", list(ids)]] + self.m.locked_when)
            if locked:
                raise UserError(f"This {self.m.label} is locked and can no longer be edited.")
        if self.m.parent:
            for parent_id in self._parent_ids(ids) + [vals.get(self.m.parent)]:
                self._check_parent_locked(parent_id)
        if "prepare_write" in self.m.hooks:
            vals = self.m.hooks["prepare_write"](env, ids, dict(vals)) or vals
        stored, relational = self._clean(vals, False)
        tracked = [f for f in self.m.fields if f.tracked and f.name in stored] if self.m.mail else []
        before = {r["id"]: r for r in Records(env.sudo(), self.m).read(ids, [f.name for f in tracked] + ["id"])} if tracked else {}
        if stored:
            sets = ", ".join(f'"{c}" = %s' for c in stored)
            params = list(stored.values())
            if env.user.id:
                sets += ", write_uid = %s"
                params.append(env.user.id)
            env.cur.execute(
                f'UPDATE "{self.m.table}" SET {sets}, write_date = now() WHERE id = ANY(%s)', params + [list(ids)])
        for rec_id in ids:
            self._write_relational(rec_id, relational)
        if not env.su or stored:
            self._check_rule(ids)
        if "after_write" in self.m.hooks:
            self.m.hooks["after_write"](env, ids, vals)
        self._recompute(ids)
        for rec_id in ids:
            changes = []
            for f in tracked:
                old, new = before[rec_id][f.name], stored[f.name]
                if _comparable(old) != _comparable(new):
                    changes.append({"field": f.label or f.name, "old": self._display(f, old),
                                    "new": self._display(f, new)})
            if changes:
                post_tracking(env, self.m, rec_id, "", changes)

    def _display(self, f: Field, value) -> str:
        if value is None:
            return ""
        if f.type == "many2one":
            return self._names(REGISTRY[f.comodel], [value]).get(value, "")
        if f.type == "selection":
            return dict(f.selection or []).get(value, value)
        return str(_jsonable(value))

    def _check_rule(self, ids: list[int]):
        if self.env.su or self.env.user.is_admin or not self.m.rule:
            return
        where, params = self._where(
            [["id", "in", list(ids)]] + ([["active", "in", ["true", "false"]]] if self.m.has_active else []))
        self.env.cur.execute(f'SELECT COUNT(*) FROM "{self.m.table}" t WHERE {where}', params)
        if self.env.cur.fetchone()[0] != len(set(ids)):
            raise AccessDenied(f"You do not have access to this {self.m.label}.")

    def _write_relational(self, rec_id: int, relational: dict):
        for name, commands in relational.items():
            f = self.m.by_name[name]
            if f.type == "many2many":
                self._write_m2m(rec_id, f, commands)
            else:
                self._write_o2m(rec_id, f, commands)

    def _write_m2m(self, rec_id: int, f: Field, value):
        # Accepts a plain list of ids, or [id, name] pairs as returned by the API.
        ids = [(v[0] if isinstance(v, (list, tuple)) else v) for v in (value or [])]
        if any(isinstance(i, bool) or not isinstance(i, int) for i in ids):
            raise UserError(f"{f.label or f.name} must be a list of record ids.")
        left, right = m2m_columns(self.m, f)
        self.env.cur.execute(f'DELETE FROM "{f.relation}" WHERE "{left}" = %s', (rec_id,))
        for i in dict.fromkeys(ids):
            self.env.cur.execute(
                f'INSERT INTO "{f.relation}" ("{left}", "{right}") VALUES (%s, %s)', (rec_id, i))

    def _write_o2m(self, rec_id: int, f: Field, commands):
        """Commands: [0, 0, vals] create, [1, id, vals] update, [2, id, 0] delete."""
        child = Records(self.env, REGISTRY[f.comodel])
        if not isinstance(commands, list):
            raise UserError(f"Invalid value for {f.label or f.name}.")
        # Line rights follow the parent: if the parent is editable, so are its lines.
        for cmd in commands:
            if not isinstance(cmd, (list, tuple)) or len(cmd) != 3:
                raise UserError(f"Invalid command for {f.label or f.name}.")
            op, line_id, vals = cmd
            if op == 0:
                if not isinstance(vals, dict):
                    raise UserError(f"Invalid line for {f.label or f.name}.")
                child.create({**vals, f.inverse: rec_id})
                continue
            if op not in (1, 2):
                raise UserError(f"Invalid command for {f.label or f.name}.")
            # The line must belong to this parent, or a client could edit another record's lines.
            self.env.cur.execute(
                f'SELECT 1 FROM "{child.m.table}" WHERE id = %s AND "{f.inverse}" = %s', (line_id, rec_id))
            if not self.env.cur.fetchone():
                raise NotFound("That line does not belong to this record.")
            if op == 1:
                if not isinstance(vals, dict) or f.inverse in vals:
                    raise UserError(f"Invalid line for {f.label or f.name}.")
                child.write([line_id], vals)
            else:
                child.unlink([line_id])

    def _parent_ids(self, ids: list[int]) -> list[int]:
        self.env.cur.execute(
            f'SELECT DISTINCT "{self.m.parent}" FROM "{self.m.table}" WHERE id = ANY(%s) AND "{self.m.parent}" IS NOT NULL',
            (list(ids),))
        return [r[0] for r in self.env.cur.fetchall()]

    def _check_parent_locked(self, parent_id):
        """Lines of a locked document are locked too, whichever endpoint they are edited through."""
        if self.env.su or not parent_id or not self.m.parent:
            return
        parent = REGISTRY[self.m.by_name[self.m.parent].comodel]
        if parent.locked_when and Records(self.env.sudo(), parent).search(
                [["id", "=", parent_id]] + parent.locked_when):
            raise UserError(f"This {parent.label} is locked and can no longer be edited.")

    def _recompute(self, ids: list[int]):
        env = self.env.sudo()
        if "recompute" in self.m.hooks:
            self.m.hooks["recompute"](env, ids)
        if self.m.parent:
            parent = REGISTRY[self.m.by_name[self.m.parent].comodel]
            parents = self._parent_ids(ids)
            if parents and "recompute" in parent.hooks:
                parent.hooks["recompute"](env, parents)

    def unlink(self, ids: list[int]) -> None:
        if not ids:
            return
        env = self.env
        env.check_access(self.m, "d")
        self._check_rule(ids)
        if not env.su and self.m.locked_when:
            locked = Records(env.sudo(), self.m).search([["id", "in", list(ids)]] + self.m.locked_when)
            if locked:
                raise UserError(f"This {self.m.label} is locked and cannot be deleted.")
        parents = self._parent_ids(ids) if self.m.parent else []
        for parent_id in parents:
            self._check_parent_locked(parent_id)
        if "before_unlink" in self.m.hooks:
            self.m.hooks["before_unlink"](env, ids)
        env.cur.execute(f'DELETE FROM "{self.m.table}" WHERE id = ANY(%s)', (list(ids),))
        if parents:
            parent = REGISTRY[self.m.by_name[self.m.parent].comodel]
            if "recompute" in parent.hooks:
                parent.hooks["recompute"](env.sudo(), parents)

    def run_action(self, key: str, ids: list[int], params: dict | None = None) -> Any:
        action = self.m.actions.get(key)
        if not action:
            raise NotFound(f"Unknown action {key}.")
        if action.groups and not self.env.has_group(*action.groups):
            raise AccessDenied(f"You are not allowed to run {action.label}.")
        # Business actions change server-owned state, so they need edit rights on these records
        # (including record rules) before running with elevated rights inside the action.
        self.env.check_access(self.m, "w")
        self._check_rule(ids)
        visible = Records(self.env, self.m).search(
            [["id", "in", list(ids)]] + ([["active", "in", ["true", "false"]]] if self.m.has_active else []))
        if len(visible) != len(set(ids)):
            raise NotFound()
        return action.fn(self.env, ids, params or {})


def _seq_prefix(code: str) -> str:
    return SEQUENCE_PREFIXES.get(code, code.upper() + "/")


SEQUENCE_PREFIXES: dict[str, str] = {}


def define_sequence(code: str, prefix: str) -> str:
    SEQUENCE_PREFIXES[code] = prefix
    return code


def post_tracking(env: Env, m: Model, rec_id: int, body: str, changes: list[dict]):
    env.cur.execute(
        """INSERT INTO mail_message (res_model, res_id, author_id, kind, body, tracking)
           VALUES (%s, %s, %s, 'tracking', %s, %s)""",
        (m.name, rec_id, env.user.id or None, body, json.dumps(changes)))


def post_note(env: Env, model_name: str, rec_id: int, body: str):
    env.cur.execute(
        """INSERT INTO mail_message (res_model, res_id, author_id, kind, body)
           VALUES (%s, %s, %s, 'tracking', %s)""", (model_name, rec_id, env.user.id or None, body))


def _mentions(domain: list, name: str) -> bool:
    return any(isinstance(d, (list, tuple)) and len(d) == 3 and d[0] == name for d in domain)


def _comparable(v):
    return float(v) if isinstance(v, Decimal) else v


def _jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return v


# ---------------------------------------------------------------------------------------------
# Schema sync (Odoo-style: the registry is the source of truth, columns are only ever added)
# ---------------------------------------------------------------------------------------------
CORE_SQL = """
CREATE TABLE IF NOT EXISTS ir_sequence (code TEXT PRIMARY KEY, number INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ir_config (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS auth_session (
    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS auth_attempt (
    id BIGSERIAL PRIMARY KEY, email TEXT NOT NULL, ip TEXT NOT NULL, at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS auth_attempt_lookup ON auth_attempt (email, ip, at);
CREATE TABLE IF NOT EXISTS mail_message (
    id BIGSERIAL PRIMARY KEY, res_model TEXT NOT NULL, res_id INTEGER NOT NULL, author_id INTEGER,
    kind TEXT NOT NULL DEFAULT 'comment', body TEXT NOT NULL DEFAULT '', tracking JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS mail_message_record ON mail_message (res_model, res_id, id);
"""


def sync_schema(cur):
    cur.execute("SELECT pg_advisory_xact_lock(727401)")
    cur.execute(CORE_SQL)
    for m in REGISTRY.values():
        cur.execute(
            f'''CREATE TABLE IF NOT EXISTS "{m.table}" (
                id SERIAL PRIMARY KEY, create_date TIMESTAMPTZ NOT NULL DEFAULT now(),
                write_date TIMESTAMPTZ NOT NULL DEFAULT now(), create_uid INTEGER, write_uid INTEGER)''')
    for m in REGISTRY.values():
        for f in m.fields:
            if f.stored:
                cur.execute(f'ALTER TABLE "{m.table}" ADD COLUMN IF NOT EXISTS "{f.name}" {COLUMN_TYPES[f.type]}')
    for m in REGISTRY.values():
        for f in m.fields:
            if f.type in ("many2one", "file"):
                co = REGISTRY[f.comodel]
                _add_constraint(cur, m.table, f"{m.table}_{f.name}_fk"[:63],
                                f'FOREIGN KEY ("{f.name}") REFERENCES "{co.table}" (id) ON DELETE {f.ondelete}')
            if f.index and f.stored:
                cur.execute(f'CREATE INDEX IF NOT EXISTS "{(m.table + "_" + f.name + "_idx")[:63]}" ON "{m.table}" ("{f.name}")')
            if f.type == "many2many":
                left, right = m2m_columns(m, f)
                co = REGISTRY[f.comodel]
                cur.execute(
                    f'''CREATE TABLE IF NOT EXISTS "{f.relation}" (
                        "{left}" INTEGER NOT NULL REFERENCES "{m.table}" (id) ON DELETE CASCADE,
                        "{right}" INTEGER NOT NULL REFERENCES "{co.table}" (id) ON DELETE CASCADE,
                        PRIMARY KEY ("{left}", "{right}"))''')
        for name, definition in m.constraints:
            _add_constraint(cur, m.table, f"{m.table}_{name}"[:63], definition)
    for m in REGISTRY.values():
        for f in m.fields:
            if f.type == "many2many":
                _check_m2m_tables(m, f)


def _check_m2m_tables(m: Model, f: Field):
    if not f.relation:
        raise RuntimeError(f"{m.name}.{f.name} needs a relation table")


def _add_constraint(cur, table: str, name: str, definition: str):
    cur.execute("SELECT 1 FROM pg_constraint WHERE conname = %s", (name,))
    if not cur.fetchone():
        cur.execute(f'ALTER TABLE "{table}" ADD CONSTRAINT "{name}" {definition}')


def validate_registry():
    """Catches view and relation typos at startup rather than at first click."""
    errors = []
    for m in REGISTRY.values():
        for f in m.fields:
            if f.comodel and f.comodel not in REGISTRY:
                errors.append(f"{m.name}.{f.name}: unknown comodel {f.comodel}")
            if f.type == "one2many" and f.comodel in REGISTRY and f.inverse not in REGISTRY[f.comodel].by_name:
                errors.append(f"{m.name}.{f.name}: unknown inverse {f.inverse}")
            for col in f.columns or []:
                if f.comodel in REGISTRY and col not in REGISTRY[f.comodel].by_name:
                    errors.append(f"{m.name}.{f.name}: unknown column {col}")
            if f.type == "many2one" and f.domain:
                _check_paths(errors, f"{m.name}.{f.name}", REGISTRY.get(f.comodel), f.domain)
        if m.rec_name not in m.by_name:
            errors.append(f"{m.name}: unknown rec_name {m.rec_name}")
        if m.parent and m.parent not in m.by_name:
            errors.append(f"{m.name}: unknown parent {m.parent}")
        for ref in _view_fields(m.views):
            if ref.split(":")[0].split(".")[0] not in m.by_name:
                errors.append(f"{m.name}: view references unknown field {ref}")
        for key in _view_actions(m.views):
            if key not in m.actions:
                errors.append(f"{m.name}: view references unknown action {key}")
    for a in APPS:
        for mi in _walk_menus(a["menus"]):
            if mi["model"] and mi["model"] not in REGISTRY:
                errors.append(f"app {a['key']}: menu {mi['key']} unknown model {mi['model']}")
            if mi["model"] in REGISTRY:
                for v in mi["views"]:
                    if v != "form" and v not in REGISTRY[mi["model"]].views:
                        errors.append(f"app {a['key']}: {mi['model']} has no {v} view")
    if errors:
        raise RuntimeError("Invalid registry:\n" + "\n".join(errors))


def _check_paths(errors, where, m, domain):
    if not m:
        return
    for leaf in domain:
        if isinstance(leaf, (list, tuple)) and leaf[0].split(".")[0] not in m.by_name:
            errors.append(f"{where}: domain references unknown field {leaf[0]}")


def _walk_menus(menus):
    for mi in menus:
        yield mi
        yield from _walk_menus(mi.get("children") or [])


def _view_fields(views: dict):
    for col in views.get("list", {}).get("columns", []):
        yield col if isinstance(col, str) else col["field"]
    form = views.get("form", {})
    if form.get("status"):
        yield form["status"]
    if form.get("title"):
        yield form["title"]
    for section in form.get("sections", []):
        for column in section.get("columns", []):
            for item in column:
                yield item if isinstance(item, str) else item["field"]
        for tab in section.get("tabs", []):
            for item in tab["fields"]:
                yield item if isinstance(item, str) else item["field"]
    kanban = views.get("kanban", {})
    for key in ("group_by", "title", "subtitle", "badge", "amount", "user", "priority", "date"):
        if kanban.get(key):
            yield kanban[key]
    for name in kanban.get("fields", []):
        yield name
    cal = views.get("calendar", {})
    for key in ("start", "stop", "title"):
        if cal.get(key):
            yield cal[key]
    graph = views.get("graph", {})
    if graph.get("group_by"):
        yield graph["group_by"]
    for name in graph.get("measures", []):
        yield name
    pivot = views.get("pivot", {})
    for key in ("row", "col"):
        if pivot.get(key):
            yield pivot[key]
    for name in pivot.get("measures", []):
        yield name
    search = views.get("search", {})
    for name in search.get("fields", []) + search.get("group_by", []):
        yield name


def _view_actions(views: dict):
    for btn in views.get("form", {}).get("buttons", []):
        yield btn["action"]
    for btn in views.get("list", {}).get("buttons", []):
        yield btn["action"]
