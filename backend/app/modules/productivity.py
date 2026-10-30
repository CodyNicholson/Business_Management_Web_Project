"""Calendar, To-do, Knowledge, Documents and Discuss."""
from datetime import date

from ..orm import F, UserError, app, menu, model
from ..views import col, flt, form, kanban, list_view, search, section, tab

EVERYONE = {"base.user": "rwcd"}


def _event_rule(env):
    return ["|", "|", ["privacy", "=", "public"], ["user_id", "=", env.user.id], ["attendee_ids", "=", env.user.id]]


event = model("calendar.event", "Meeting", [
    F.char("name", "Meeting Subject", required=True, tracked=True), F.datetime("start", "Start", required=True, tracked=True), F.datetime("stop", "End", required=True),
    F.bool("allday", "All Day", default=False), F.char("location", "Location"), F.m2o("user_id", "Organizer", "res.users", default=lambda env: env.user.id or None, ondelete="SET NULL"),
    F.m2m("attendee_ids", "Attendees", "res.users", "calendar_event_users"), F.sel("privacy", "Privacy", [("public", "Everyone"), ("private", "Only attendees")], default="public"), F.text("description", "Description"),
], access=EVERYONE, rule=_event_rule, mail=True, order="start DESC", constraints=[("dates_order", "CHECK (stop >= start)")], views={
    "calendar": {"start": "start", "stop": "stop", "title": "name"}, "list": list_view("name", "start", "stop", "location", "user_id", "attendee_ids"),
    "form": form(chatter=True, sections=[section(col("start", "stop", "allday", "location"), col("user_id", "attendee_ids", "privacy"), tabs=[tab("Description", "description")])]),
    "search": search(fields=["name", "location"], filters=[flt("mine", "My Meetings", [["attendee_ids", "=", "$uid"]])], group_by=["user_id", "start:week"]),
})


def _owner_rule(env):
    return [["user_id", "=", env.user.id]]


todo = model("todo.task", "To-Do", [
    F.char("name", "Title", required=True), F.m2o("user_id", "Owner", "res.users", required=True, readonly=True, default=lambda env: env.user.id or None, ondelete="CASCADE"),
    F.sel("stage", "Stage", [("inbox", "Inbox"), ("today", "Today"), ("later", "Later"), ("done", "Done")], default="inbox"), F.date("date_deadline", "Deadline"), F.sel("priority", "Priority", [("0", "Normal"), ("1", "Important")], default="0"), F.text("description", "Notes"),
], access=EVERYONE, rule=_owner_rule, order="priority DESC, id DESC", views={
    "kanban": kanban(group_by="stage", title="name", priority="priority", date="date_deadline"), "list": list_view("name", "stage", "date_deadline"),
    "form": form(status="stage", status_clickable=True, sections=[section(col({"field": "priority", "widget": "priority"}), col("date_deadline"), tabs=[tab("Notes", "description")])]),
    "search": search(fields=["name"], filters=[flt("open", "Open", [["stage", "!=", "done"]])]),
})


def _article_rule(env):
    return ["|", ["is_private", "=", False], ["create_uid", "=", env.user.id]]


article = model("knowledge.article", "Article", [
    F.char("name", "Title", required=True, tracked=True), F.m2o("parent_id", "Parent Article", "knowledge.article", ondelete="SET NULL"), F.bool("is_private", "Private", default=False),
    F.text("body", "Content", help="Supports Markdown."), F.m2m("tag_ids", "Tags", "knowledge.tag", "knowledge_article_tags"), F.bool("active", "Active", default=True),
], access=EVERYONE, rule=_article_rule, mail=True, order="name ASC", views={
    "kanban": kanban(title="name", subtitle="parent_id", date="write_date"), "list": list_view("name", "parent_id", "is_private", "write_date"),
    "form": form(chatter=True, sections=[section(col("parent_id", "tag_ids"), col("is_private"), tabs=[tab("Content", {"field": "body", "widget": "markdown"})])]),
    "search": search(fields=["name", "body"], filters=[flt("private", "My Private Articles", [["is_private", "=", True]])], group_by=["parent_id"]),
})
model("knowledge.tag", "Article Tag", [F.char("name", "Tag", required=True)], access=EVERYONE, order="name ASC", views={"list": list_view("name"), "form": form()})

folder = model("document.folder", "Folder", [F.char("name", "Folder", required=True), F.m2o("parent_id", "Parent", "document.folder", ondelete="SET NULL"),
                                             F.sql("doc_count", "Documents", "int", "SELECT COUNT(*) FROM document_document d WHERE d.folder_id = {t}.id")],
               access=EVERYONE, order="name ASC", views={"kanban": kanban(title="name", fields=["doc_count"]), "list": list_view("name", "parent_id", "doc_count"), "form": form(sections=[section(col("parent_id"))])})
document = model("document.document", "Document", [
    F.char("name", "Name", required=True), F.m2o("folder_id", "Folder", "document.folder", ondelete="SET NULL"), F.file("attachment_id", "File", required=True, ondelete="RESTRICT"),
    F.m2o("owner_id", "Owner", "res.users", default=lambda env: env.user.id or None, ondelete="SET NULL"), F.text("description", "Description"), F.m2m("tag_ids", "Tags", "knowledge.tag", "document_tags"),
], access=EVERYONE, mail=True, views={
    "kanban": kanban(title="name", subtitle="folder_id", user="owner_id", date="write_date"), "list": list_view("name", "folder_id", "owner_id", "attachment_id", "write_date"),
    "form": form(chatter=True, sections=[section(col("attachment_id", "folder_id"), col("owner_id", "tag_ids"), tabs=[tab("Description", "description")])]),
    "search": search(fields=["name"], group_by=["folder_id", "owner_id"]),
})


@document.hook("prepare_create")
@document.hook("prepare_write")
def _own_attachment_only(env, *args):
    # Linking a file makes it readable by everyone, so only its uploader may share it.
    vals = args[-1]
    attachment_id = vals.get("attachment_id")
    attachment_id = attachment_id[0] if isinstance(attachment_id, (list, tuple)) else attachment_id
    if attachment_id and not env.su and not env.user.is_admin:
        env.cur.execute("SELECT 1 FROM ir_attachment WHERE id = %s AND create_uid = %s", (attachment_id, env.user.id))
        if not env.cur.fetchone():
            raise UserError("You can only attach files you uploaded.")
    return vals


def _channel_rule(env):
    return ["|", ["kind", "=", "channel"], ["member_ids", "=", env.user.id]]


channel = model("discuss.channel", "Channel", [
    F.char("name", "Name", required=True), F.sel("kind", "Type", [("channel", "Public Channel"), ("group", "Private Group"), ("dm", "Direct Message")], default="channel"),
    F.text("description", "Description"), F.m2m("member_ids", "Members", "res.users", "discuss_channel_users"),
], access={"base.user": "rwc", "base.admin": "rwcd"}, rule=_channel_rule, order="name ASC", views={
    "list": list_view("name", "kind", "description"), "form": form(sections=[section(col("kind", "member_ids"), col("description"))]),
})


@channel.hook("after_create")
def _creator_joins(env, rec_id, vals):
    env.cur.execute("INSERT INTO discuss_channel_users (discuss_channel_id, res_users_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (rec_id, env.user.id)) if env.user.id else None


model("discuss.message", "Message", [
    F.m2o("channel_id", "Channel", "discuss.channel", required=True, ondelete="CASCADE"), F.m2o("author_id", "Author", "res.users", readonly=True, ondelete="SET NULL"), F.text("body", "Body", required=True),
], access={"base.admin": "r"}, rec_name="id", order="id ASC")


app("dashboards", "Dashboards", "layout-dashboard", "#34568b", [menu("main", "My Dashboard", client="dashboard")], sequence=0)
app("discuss", "Discuss", "message-circle", "#4d6fa8", [menu("chat", "Discuss", client="discuss")], sequence=1)
app("calendar", "Calendar", "calendar", "#2e7d6b", [
    menu("calendar", "Calendar", "calendar.event", ("calendar", "list", "form")),
    menu("mine", "My Meetings", "calendar.event", ("list", "calendar", "form"), [["attendee_ids", "=", "$uid"]]),
], sequence=2)
app("todo", "To-do", "check-square", "#7e6a2f", [menu("tasks", "To-do", "todo.task", ("kanban", "list", "form"))], sequence=3)
app("knowledge", "Knowledge", "book-open", "#6e5a8a", [
    menu("articles", "Articles", "knowledge.article", ("kanban", "list", "form")),
    menu("config", "Configuration", children=[menu("tags", "Tags", "knowledge.tag")]),
], sequence=5)
app("documents", "Documents", "folder", "#a8762b", [
    menu("documents", "Documents", "document.document", ("kanban", "list", "form")),
    menu("folders", "Folders", "document.folder", ("kanban", "list", "form")),
], sequence=6)
