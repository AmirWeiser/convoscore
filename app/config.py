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
