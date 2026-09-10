from prometheus_client import Counter, Histogram

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
