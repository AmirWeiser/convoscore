import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from prometheus_client import make_asgi_app
from starlette.middleware.base import BaseHTTPMiddleware

from app import config
from app.aws_clients import s3_client
from app.db import repository
from app.db.pool import init_schema
from app.logging_setup import setup_logging
from app.metrics import http_request_duration_seconds, http_requests_total
from app.models import ConversationAccepted, ConversationIn

setup_logging()

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema()
    yield


app = FastAPI(title="ConvoScore", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


class _MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        # request.scope["route"] is only populated once Starlette's router
        # has matched the request - which happens *inside* call_next (this
        # middleware sits outside the router in the ASGI chain). Reading it
        # before call_next() always sees an unmatched scope, so every
        # request - including normal, successfully-routed ones - was mislabeled
        # "unmatched". It must be read after call_next() returns or raises,
        # by which point the router has already mutated the shared scope
        # dict (mutated in place, even on a downstream exception, as long as
        # routing itself succeeded). See DECISIONS.md.
        try:
            response = await call_next(request)
        except Exception:
            # An unhandled exception must still be counted (as 500) and
            # timed, not silently drop out of the metrics entirely - and the
            # exception itself must propagate unchanged so Starlette's own
            # error handling still produces the real response. See
            # DECISIONS.md.
            duration = time.monotonic() - start
            route = request.scope.get("route")
            path = route.path if route else "unmatched"
            http_requests_total.labels(request.method, path, "500").inc()
            http_request_duration_seconds.labels(request.method, path).observe(duration)
            raise
        duration = time.monotonic() - start
        route = request.scope.get("route")
        path = route.path if route else "unmatched"
        http_requests_total.labels(request.method, path, str(response.status_code)).inc()
        http_request_duration_seconds.labels(request.method, path).observe(duration)
        return response


app.add_middleware(_MetricsMiddleware)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    # DB reachability only - never OpenAI. A transient OpenAI outage must not
    # pull this pod out of rotation. See DECISIONS.md.
    with repository.pool.connection():
        pass
    return {"status": "ready"}


@app.post("/conversations", response_model=ConversationAccepted, status_code=202)
def submit_conversation(body: ConversationIn):
    conversation_id = uuid.uuid4()
    source_key = f"incoming/{conversation_id}.json"
    repository.create_pending(conversation_id, source_key, "api", body.text)

    try:
        s3_client().put_object(
            Bucket=config.S3_BUCKET_NAME,
            Key=source_key,
            Body=json.dumps({"text": body.text}),
        )
    except (BotoCoreError, ClientError) as exc:
        # Compensating delete: nothing downstream has seen this row yet (no S3
        # object means no notification means no SQS message was ever
        # created), so a hard delete is safe. See DECISIONS.md.
        repository.delete(conversation_id)
        raise HTTPException(
            status_code=502, detail="Failed to enqueue conversation for scoring"
        ) from exc

    return ConversationAccepted(
        id=str(conversation_id),
        status="pending",
        status_url=f"/conversations/{conversation_id}",
    )


@app.get("/conversations", response_class=HTMLResponse)
def list_conversations_page(request: Request):
    conversations = repository.list_recent()
    return templates.TemplateResponse(
        request, "list.html", {"conversations": conversations}
    )


@app.get("/conversations/{conversation_id}", response_class=HTMLResponse)
def conversation_detail_page(request: Request, conversation_id: uuid.UUID):
    conversation = repository.get_by_id(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return templates.TemplateResponse(request, "detail.html", {"c": conversation})
