from prometheus_client import Counter, Gauge, Histogram

# outcome: "completed" | "failed"
conversations_processed_total = Counter(
    "conversations_processed_total",
    "Conversations that reached a terminal state, by outcome",
    ["outcome"],
)

processing_duration_seconds = Histogram(
    "processing_duration_seconds",
    "Time from claiming a conversation to storing its result",
)

# direction: "input" | "output" - low-cardinality by design, see DECISIONS.md
openai_tokens_total = Counter(
    "openai_tokens_total", "OpenAI tokens consumed, by direction", ["direction"]
)

openai_estimated_cost_usd_total = Counter(
    "openai_estimated_cost_usd_total", "Estimated cumulative OpenAI spend in USD"
)

dlq_depth = Gauge(
    "convoscore_dlq_depth", "Approximate number of messages currently in the DLQ"
)

# HTTP-level, API only. path label is the route template
# (e.g. "/conversations/{conversation_id}"), never the resolved path - a raw
# resolved path would put a fresh UUID in a label value per request, which
# is exactly the unbounded-cardinality mistake this project has avoided
# elsewhere. See DECISIONS.md.
http_requests_total = Counter(
    "http_requests_total", "HTTP requests handled, by method/path template/status", ["method", "path", "status"]
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds", "HTTP request latency, by method/path template", ["method", "path"]
)
