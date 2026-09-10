"""Requires a running local Postgres (see README). Verifies the API metrics
middleware labels requests with the route template - not "unmatched" and not
a raw resolved path containing the UUID - on both the normal response path
and the unhandled-exception path (request.scope["route"] must be read after
call_next(), not before - see DECISIONS.md)."""

import uuid

from fastapi.testclient import TestClient

from app import main
from app.db import repository
from app.db.pool import init_schema

ROUTE_TEMPLATE = "/conversations/{conversation_id}"


def setup_module():
    init_schema()


def _metrics_text(client: TestClient) -> str:
    return client.get("/metrics").text


def test_successful_conversation_request_uses_route_template():
    conversation_id = uuid.uuid4()
    repository.create_pending(conversation_id, f"incoming/{conversation_id}.json", "api", "hi")

    client = TestClient(main.app)
    resp = client.get(f"/conversations/{conversation_id}")
    assert resp.status_code == 200

    text = _metrics_text(client)
    assert f'http_requests_total{{method="GET",path="{ROUTE_TEMPLATE}",status="200"}}' in text
    assert str(conversation_id) not in text


def test_unhandled_exception_still_uses_route_template_not_unmatched(monkeypatch):
    conversation_id = uuid.uuid4()

    def _boom(_id):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(main.repository, "get_by_id", _boom)

    client = TestClient(main.app, raise_server_exceptions=False)
    resp = client.get(f"/conversations/{conversation_id}")
    assert resp.status_code == 500

    text = _metrics_text(client)
    # The specific counter series this request incremented, labelled by the
    # matched route template with a 500 status - not "unmatched".
    assert f'http_requests_total{{method="GET",path="{ROUTE_TEMPLATE}",status="500"}}' in text
