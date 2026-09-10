"""Focused test for the explicit requirement: exception messages and stack
traces must never expose secrets or conversation content, even when a
third-party library's exception happens to embed them. Uses synthetic
sensitive markers pushed through the real _process_message code path - not
a code-reading argument, an executable proof."""

import json
import logging
import uuid
from unittest.mock import MagicMock

from psycopg.rows import dict_row

from app.db import repository
from app.db.pool import init_schema
from app import worker as worker_module

_SECRET_KEY_MARKER = "sk-FAKE1234567890TESTMARKER"
_CONTENT_MARKER = "customer SSN 123-45-6789 TESTMARKER"


class _LeakyScorer:
    """Simulates a third-party exception whose own message embeds sensitive
    data - the real risk this guards against (e.g. an OpenAI refusal or a
    library error echoing a request/response body)."""

    def score(self, text: str):
        raise RuntimeError(
            f"upstream failure - leaked key {_SECRET_KEY_MARKER} and "
            f"content: {_CONTENT_MARKER}"
        )


def setup_module():
    init_schema()


def test_scoring_failure_never_leaks_secrets_or_content(monkeypatch, caplog):
    monkeypatch.setattr(worker_module, "get_scorer", lambda: _LeakyScorer())

    conversation_id = uuid.uuid4()
    key = f"incoming/{conversation_id}-leak-test.json"

    s3 = MagicMock()
    s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: json.dumps({"text": "irrelevant test text"}).encode())
    }
    sqs = MagicMock()

    message = {
        "ReceiptHandle": "fake-handle",
        "Attributes": {"ApproximateReceiveCount": "3"},  # final allowed attempt
        "Body": json.dumps(
            {"Records": [{"s3": {"bucket": {"name": "test-bucket"}, "object": {"key": key}}}]}
        ),
    }

    with caplog.at_level(logging.INFO, logger="convoscore.worker"):
        worker_module._process_message(sqs, s3, "fake-queue-url", message)

    # Check every field of every emitted log record, not just the rendered
    # message text - extra={...} fields (where a stack trace or error detail
    # would live) don't appear in caplog.text by default.
    for record in caplog.records:
        record_repr = json.dumps(record.__dict__, default=str)
        assert _SECRET_KEY_MARKER not in record_repr
        assert _CONTENT_MARKER not in record_repr

    # The S3-only ingestion path generates its own fresh id (no row
    # pre-existed for this source_key) - look up by source_key, not by a
    # locally-generated id.
    with repository.pool.connection() as conn:
        conn.row_factory = dict_row
        row = conn.execute(
            "SELECT * FROM conversations WHERE source_key = %s", (key,)
        ).fetchone()

    assert row is not None
    assert row["status"] == "failed"
    assert row["error_category"] == "transient_exhausted"
    assert _SECRET_KEY_MARKER not in (row["error_message"] or "")
    assert _CONTENT_MARKER not in (row["error_message"] or "")
    # error_message is the exception's type name only, by design
    assert row["error_message"] == "RuntimeError"
