import os

from dotenv import load_dotenv

# Local-only, gitignored .env - never committed, never printed. In Kubernetes
# (Phase 7) this is unused; real env vars come from Secrets instead.
load_dotenv()

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://convoscore:convoscore@localhost:5432/convoscore"
)

# Scoring
SCORER_PROVIDER = os.environ.get("SCORER_PROVIDER", "fake")  # "fake" | "openai"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TIMEOUT_SECONDS = float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "20"))
OPENAI_MAX_RETRIES = int(os.environ.get("OPENAI_MAX_RETRIES", "3"))

# AWS (LocalStack) - these defaults match terraform/variables.tf exactly.
# Terraform is the source of truth for the resources themselves; these are
# just how the app addresses them.
AWS_ENDPOINT_URL = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME", "convoscore-conversations")
SQS_QUEUE_NAME = os.environ.get("SQS_QUEUE_NAME", "convoscore-processing")
SQS_DLQ_NAME = os.environ.get("SQS_DLQ_NAME", "convoscore-processing-dlq")

# Must equal terraform's visibility_timeout_seconds - same number, two places,
# see DECISIONS.md (this is what makes stale-reclaim correct).
VISIBILITY_TIMEOUT_SECONDS = int(os.environ.get("VISIBILITY_TIMEOUT_SECONDS", "60"))
MAX_RECEIVE_COUNT = int(os.environ.get("MAX_RECEIVE_COUNT", "3"))

METRICS_PORT = int(os.environ.get("METRICS_PORT", "9000"))
