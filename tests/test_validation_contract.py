"""Item 3: both ingestion paths share exactly one validation contract
(ConversationIn) - see DECISIONS.md. Before this fix, the S3 path used ad
hoc checks that raised an unhandled TypeError on non-string JSON values
(null, a number, a list) and never enforced the 20,000-character limit."""

import json
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from pydantic import ValidationError

from app import main
from app import worker as worker_module
from app.db import repository
from app.db.pool import init_schema
from app.models import ConversationIn

client = TestClient(main.app)

_TOO_LONG = "x" * 20_001

_INVALID_PAYLOADS = [
    {},  # missing key
    {"text": None},  # null
    {"text": 42},  # number
    {"text": ["not", "a", "string"]},  # list
    {"text": ""},  # empty string
    {"text": "   \n\t  "},  # whitespace-only
    {"text": _TOO_LONG},  # too long
    None,  # top-level null
    42,  # top-level number
    ["a", "list"],  # top-level list
]


def setup_module():
    init_schema()


@pytest.mark.parametrize("payload", _INVALID_PAYLOADS)
def test_conversation_in_rejects_every_invalid_shape(payload):
    with pytest.raises(ValidationError):
        ConversationIn.model_validate(payload)


def test_conversation_in_accepts_valid_text():
    parsed = ConversationIn.model_validate({"text": "a perfectly normal conversation"})
    assert parsed.text == "a perfectly normal conversation"


@pytest.mark.parametrize("payload", [p for p in _INVALID_PAYLOADS if isinstance(p, dict)])
def test_api_rejects_every_invalid_shape(payload):
    resp = client.post("/conversations", json=payload)
    assert resp.status_code == 422


@pytest.mark.parametrize("payload", [{"text": None}, {"text": 42}, {"text": "   "}])
def test_worker_s3_path_rejects_the_same_invalid_shapes_as_the_api(payload):
    """The S3 path used to check `isinstance(text, str)` by hand and could
    raise an unhandled TypeError for these exact shapes - now it goes
    through the same ConversationIn contract as the API."""
    key = f"incoming/{uuid.uuid4()}-invalid-shape.json"
    s3 = MagicMock()
    s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: json.dumps(payload).encode())
    }
    sqs = MagicMock()
    message = {
        "ReceiptHandle": "fake-handle",
        "Attributes": {"ApproximateReceiveCount": "1"},
        "Body": json.dumps(
            {"Records": [{"s3": {"bucket": {"name": "test-bucket"}, "object": {"key": key}}}]}
        ),
    }

    worker_module._process_message(sqs, s3, "fake-queue-url", message)

    sqs.delete_message.assert_called_once()  # terminal, never retried
    with repository.pool.connection() as conn:
        conn.row_factory = dict_row
        row = conn.execute(
            "SELECT status, error_category FROM conversations WHERE source_key = %s", (key,)
        ).fetchone()
    assert row is not None
    assert row["status"] == "failed"
    assert row["error_category"] == "validation"
