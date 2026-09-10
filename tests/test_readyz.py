"""Item 5: /readyz must execute a real statement, not just check out a
pooled connection - a pooled connection can be idle-but-broken without
psycopg necessarily detecting that until something is actually sent over
it. Requires a running local Postgres (see README)."""

from fastapi.testclient import TestClient

from app import main
from app.db.pool import init_schema

client = TestClient(main.app)


def setup_module():
    init_schema()


def test_readyz_executes_a_real_query_and_succeeds_against_a_healthy_db():
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


def test_readyz_reports_not_ready_when_the_db_is_unreachable(monkeypatch):
    def _broken_connection(*args, **kwargs):
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(main.repository.pool, "connection", _broken_connection)

    resp = client.get("/readyz")

    assert resp.status_code != 200
