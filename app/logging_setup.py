import json
import logging
import sys

from app.safe_errors import safe_stack_trace

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()) | {
    "message",
    "asctime",
}


class _JsonFormatter(logging.Formatter):
    """Structured JSON logs. Only ever include low-cardinality, non-secret
    context (ids, categories, counts) - never conversation content, the
    OpenAI key, or DB credentials. See DECISIONS.md.

    Deliberately never calls logging.Formatter.formatException(): that calls
    traceback.format_exception(), which includes the exception's own str()
    (and any chained/args) - a synthetic exception carrying a secret or
    conversation content would reproduce it verbatim in every unhandled-
    exception log, even from code paths (e.g. a bare `raise` reaching
    FastAPI's default handler, or a third-party library logging its own
    caught exception) that never call this project's explicit safe-logging
    helpers. Every exc_info is rendered through the same safe helper used
    everywhere else in this project: exception type name plus stack frames
    only, never the message or args."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                payload[key] = value
        if record.exc_info:
            exc_type, _exc_value, exc_tb = record.exc_info
            payload["exc_type"] = exc_type.__name__ if exc_type else None
            payload["exc_stack_trace"] = safe_stack_trace(exc_tb) if exc_tb else ""
        return json.dumps(payload, default=str)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
