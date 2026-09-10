import time
from pathlib import Path

from psycopg_pool import ConnectionPool, PoolTimeout

from app.config import DATABASE_URL

# Conservative pool size - see DECISIONS.md. API is request-driven (a handful of
# concurrent requests at this scale); the worker (added in Phase 4) gets its own,
# smaller pool since it's a single-threaded poll loop.
pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5, open=False)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_schema() -> None:
    """Idempotent - CREATE TABLE IF NOT EXISTS. One plain SQL file is the whole
    migration strategy; see DECISIONS.md for why Alembic isn't used for one table.

    pool.open() retried with wait=True: observed on a real cluster that a
    transient DNS blip on a freshly-started pod (CoreDNS not yet warmed up)
    can leave a non-blocking pool.open() never establishing a working
    connection, even after DNS itself recovers - the pool's own background
    reconnect doesn't reliably self-heal from this. Waiting for and
    confirming a real connection, with retries, fixes it at the source
    instead of leaving a pod that reports healthy but can never do anything.
    See DECISIONS.md.
    """
    last_error: Exception | None = None
    for _ in range(6):
        try:
            pool.open(wait=True, timeout=10)
            break
        except PoolTimeout as exc:
            last_error = exc
            time.sleep(5)
    else:
        raise RuntimeError(f"database pool never became ready: {last_error}")

    with pool.connection() as conn:
        conn.execute(_SCHEMA_PATH.read_text())
