import os


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


DATABASE_URL = os.environ["DATABASE_URL"]
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@example.com").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
COOKIE_SECURE = _bool("COOKIE_SECURE", True)
SEED_DEMO_DATA = _bool("SEED_DEMO_DATA", True)
RUN_SCHEDULER = _bool("RUN_SCHEDULER", True)
COMPANY_NAME = os.environ.get("COMPANY_NAME", "My Company")
ATTACHMENT_DIR = os.environ.get("ATTACHMENT_DIR", "/data/attachments")
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "25")) * 1024 * 1024
SESSION_HOURS = int(os.environ.get("SESSION_HOURS", "12"))
PUBLIC_URL = os.environ.get("PUBLIC_URL", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT") or "587")
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "")
