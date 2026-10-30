import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, Response

from . import config
from .db import transaction
from .orm import CurrentUser, UserError

COOKIE = "session"
MIN_PASSWORD_LENGTH = 12
THROTTLE_WINDOW_MINUTES = 15
THROTTLE_MAX_FAILURES = 8
_hasher = PasswordHasher()
# Verified when the email is unknown, so response time does not reveal which accounts exist.
_DUMMY_HASH = _hasher.hash(secrets.token_hex(16))


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"Passwords must be at least {MIN_PASSWORD_LENGTH} characters.")
    return _hasher.hash(password)


def verify_password(stored: str | None, password: str) -> bool:
    try:
        return _hasher.verify(stored or _DUMMY_HASH, password) and bool(stored)
    except (VerificationError, InvalidHashError):
        return False


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_ip(request: Request) -> str:
    # nginx is the only route to the API and overwrites this header.
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


def check_throttle(cur, email: str, ip: str):
    cur.execute(
        """SELECT COUNT(*) FROM auth_attempt
           WHERE email = %s AND ip = %s AND at > now() - make_interval(mins => %s)""",
        (email, ip, THROTTLE_WINDOW_MINUTES))
    if cur.fetchone()[0] >= THROTTLE_MAX_FAILURES:
        raise UserError("Too many failed sign-ins. Try again in a few minutes.", 429)


def record_failure(email: str, ip: str):
    # In its own transaction, because the login request itself rolls back when it fails.
    with transaction() as env:
        env.cur.execute("INSERT INTO auth_attempt (email, ip) VALUES (%s, %s)", (email, ip))
        env.cur.execute("DELETE FROM auth_attempt WHERE at < now() - interval '1 day'")


def start_session(cur, response: Response, user_id: int):
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=config.SESSION_HOURS)
    cur.execute("INSERT INTO auth_session (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
                (_digest(token), user_id, expires))
    cur.execute("DELETE FROM auth_session WHERE expires_at < now()")
    response.set_cookie(COOKIE, token, httponly=True, secure=config.COOKIE_SECURE, samesite="lax",
                        max_age=config.SESSION_HOURS * 3600, path="/")


def end_session(cur, response: Response, token: str | None):
    if token:
        cur.execute("DELETE FROM auth_session WHERE token_hash = %s", (_digest(token),))
    response.delete_cookie(COOKIE, path="/")


def end_all_sessions(cur, user_id: int):
    cur.execute("DELETE FROM auth_session WHERE user_id = %s", (user_id,))


def load_user(cur, user_id: int) -> CurrentUser | None:
    cur.execute('SELECT id, name, email FROM res_users WHERE id = %s AND active IS TRUE', (user_id,))
    row = cur.fetchone()
    if not row:
        return None
    cur.execute(
        """SELECT g.key FROM res_group g JOIN res_users_groups r ON r.res_group_id = g.id
           WHERE r.res_users_id = %s""", (user_id,))
    groups = {r[0] for r in cur.fetchall()} | {"base.user"}
    return CurrentUser(row[0], row[1], row[2], groups)


def current_user(request: Request) -> CurrentUser:
    token = request.cookies.get(COOKIE)
    if token:
        with transaction() as env:
            env.cur.execute("SELECT user_id FROM auth_session WHERE token_hash = %s AND expires_at > now()",
                            (_digest(token),))
            row = env.cur.fetchone()
            user = load_user(env.cur, row[0]) if row else None
        if user:
            return user
    raise HTTPException(401, "Sign in to continue.")


AUTH = Depends(current_user)
