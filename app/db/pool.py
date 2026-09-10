from pathlib import Path

from psycopg_pool import ConnectionPool

from app.config import DATABASE_URL

# Conservative pool size - see DECISIONS.md. API is request-driven (a handful of
# concurrent requests at this scale); the worker (added in Phase 4) gets its own,
# smaller pool since it's a single-threaded poll loop.
# timeout=10: how long a single pool.connection() call waits for an
# available connection before raising - tightened from the 30s default so a
# degraded pool fails fast and visibly instead of silently stalling the
# worker's single-threaded loop. See DECISIONS.md.
pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5, open=False, timeout=10)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_schema() -> None:
    """Idempotent - CREATE TABLE IF NOT EXISTS. One plain SQL file is the whole
    migration strategy; see DECISIONS.md for why Alembic isn't used for one table.

    pool.open(wait=True, timeout=30): a single call, not retried - a real bug
    was found retrying this (psycopg_pool.PoolClosed: "pool has already been
    opened/closed and cannot be reused" - open() is a one-shot lifecycle
    transition, calling it again on the same pool after a failed attempt is
    invalid, not a retry). A generous 30s wait gives a transient startup DNS
    blip (CoreDNS not yet warmed up on a fresh pod) real room to resolve on
    its own within this one call. If it still fails, this raises and the
    process exits - the startupProbe's grace period plus Kubernetes'
    restartPolicy is the actual recovery mechanism from there, the same
    self-healing already relied on elsewhere in this design. See
    DECISIONS.md.
    """
    pool.open(wait=True, timeout=30)
    with pool.connection() as conn:
        conn.execute(_SCHEMA_PATH.read_text())
