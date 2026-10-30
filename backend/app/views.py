"""Shorthands for declaring the views the frontend renders from metadata."""


def col(*fields):
    return list(fields)


def section(*columns, tabs=None, title=None):
    out = {"columns": list(columns)}
    if tabs:
        out["tabs"] = tabs
    if title:
        out["title"] = title
    return out


def tab(label, *fields):
    return {"label": label, "fields": list(fields)}


def btn(action, label, primary=False, when=None, confirm=None):
    """when: domain-like list of [field, op, value] leaves, all must match to show the button."""
    return {"action": action, "label": label, "primary": primary, "when": when or []}


def smart(label, icon, model, domain):
    """A stat button on a form. "$id" in the domain is replaced by the record id."""
    return {"label": label, "icon": icon, "model": model, "domain": domain}


def form(title="name", status=None, buttons=None, sections=None, smart_buttons=None, chatter=False,
         subtitle=None, printable=None, status_clickable=False):
    return {"title": title, "status": status, "buttons": buttons or [], "sections": sections or [],
            "smart": smart_buttons or [], "chatter": chatter, "subtitle": subtitle,
            "printable": printable, "status_clickable": status_clickable}


def list_view(*columns, editable=False, buttons=None, decorations=None):
    return {"columns": list(columns), "editable": editable, "buttons": buttons or [],
            "decorations": decorations or {}}


def kanban(group_by=None, title="name", subtitle=None, fields=None, badge=None, amount=None, user=None,
           priority=None, date=None):
    return {"group_by": group_by, "title": title, "subtitle": subtitle, "fields": fields or [],
            "badge": badge, "amount": amount, "user": user, "priority": priority, "date": date}


def calendar(start, stop=None, title="name"):
    return {"start": start, "stop": stop, "title": title}


def graph(group_by, measures=None, kind="bar"):
    return {"group_by": group_by, "measures": measures or [], "kind": kind}


def pivot(row, col_=None, measures=None):
    return {"row": row, "col": col_, "measures": measures or []}


def search(fields=None, filters=None, group_by=None):
    return {"fields": fields or [], "filters": filters or [], "group_by": group_by or []}


def flt(key, label, domain):
    return {"key": key, "label": label, "domain": domain}
