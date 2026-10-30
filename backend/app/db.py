from contextlib import contextmanager

from psycopg_pool import ConnectionPool

from . import config
from .orm import Env, SYSTEM_USER, CurrentUser

pool = ConnectionPool(config.DATABASE_URL, min_size=1, max_size=10, open=False, timeout=30)


@contextmanager
def transaction(user: CurrentUser = SYSTEM_USER, su: bool = False):
    """One database transaction. It commits before the caller builds its response, so a failed
    commit can never be reported to the client as a success."""
    with pool.connection() as conn:
        with conn.cursor() as cur:
            yield Env(cur, user, su)
