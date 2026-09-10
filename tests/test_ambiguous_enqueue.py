"""Item 2: an S3 PutObject exception does not prove S3 never stored the
object - a timeout can be ambiguous (S3 may have accepted the object and
emitted a notification while the client never saw the response). An
unconditional compensating delete would then silently erase evidence of a
conversation the worker is about to process. Requires a running local
Postgres (see README)."""

import uuid
from unittest.mock import MagicMock

from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app import main
from app.db import repository
from app.db.pool import init_schema

client = TestClient(main.app)


def setup_module():
    init_schema()


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, "HeadObject")


def _row(conversation_id: str) -> dict:
    with repository.pool.connection() as conn:
        conn.row_factory = dict_row
        return conn.execute(
            "SELECT * FROM conversations WHERE id = %s", (uuid.UUID(conversation_id),)
        ).fetchone()


def test_successful_put_returns_202(monkeypatch):
    s3 = MagicMock()
    s3.put_object.return_value = {}
    monkeypatch.setattr(main, "s3_client", lambda: s3)

    resp = client.post("/conversations", json={"text": "normal submission"})

    assert resp.status_code == 202
    s3.head_object.assert_not_called()  # no ambiguity - never needed
    assert _row(resp.json()["id"])["status"] == "pending"


def test_put_exception_but_object_actually_landed_is_treated_as_accepted(monkeypatch):
    """The worker must still be able to process this - the row and the S3
    object both genuinely exist, the client just never saw a clean response."""
    s3 = MagicMock()
    s3.put_object.side_effect = EndpointConnectionError(endpoint_url="http://x")
    s3.head_object.return_value = {}  # object exists
    monkeypatch.setattr(main, "s3_client", lambda: s3)

    resp = client.post("/conversations", json={"text": "ambiguous but actually fine"})

    assert resp.status_code == 202
    row = _row(resp.json()["id"])
    assert row["status"] == "pending"  # untouched - not deleted, not marked failed


def test_put_exception_and_definite_404_records_a_visible_failure(monkeypatch):
    s3 = MagicMock()
    s3.put_object.side_effect = EndpointConnectionError(endpoint_url="http://x")
    s3.head_object.side_effect = _client_error("404")
    monkeypatch.setattr(main, "s3_client", lambda: s3)

    resp = client.post("/conversations", json={"text": "definitely never enqueued"})

    assert resp.status_code == 502
    # No id in the error body (only a 202 response carries one) - the row
    # this test just created is found by its distinctive input_text instead.
    with repository.pool.connection() as conn:
        conn.row_factory = dict_row
        row = conn.execute(
            "SELECT * FROM conversations WHERE input_text = %s", ("definitely never enqueued",)
        ).fetchone()
    assert row is not None
    assert row["status"] == "failed"
    assert row["error_category"] == "enqueue_failed"


def test_put_exception_and_inconclusive_head_check_stays_visible_and_retryable(monkeypatch):
    s3 = MagicMock()
    s3.put_object.side_effect = EndpointConnectionError(endpoint_url="http://x")
    s3.head_object.side_effect = EndpointConnectionError(endpoint_url="http://x")
    monkeypatch.setattr(main, "s3_client", lambda: s3)

    resp = client.post("/conversations", json={"text": "truly unknown outcome"})

    assert resp.status_code == 503
    with repository.pool.connection() as conn:
        conn.row_factory = dict_row
        row = conn.execute(
            "SELECT * FROM conversations WHERE input_text = %s", ("truly unknown outcome",)
        ).fetchone()
    assert row is not None
    assert row["status"] == "pending"  # visible, not deleted, not marked terminal
