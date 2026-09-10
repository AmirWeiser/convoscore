from functools import lru_cache

from app import config
from app.scoring.base import Scorer
from app.scoring.fake import FakeScorer


@lru_cache
def get_scorer() -> Scorer:
    if config.SCORER_PROVIDER == "openai":
        from app.scoring.openai_scorer import OpenAIScorer

        return OpenAIScorer()
    if config.SCORER_PROVIDER == "fake":
        return FakeScorer()
    raise ValueError(
        f"Unsupported SCORER_PROVIDER: {config.SCORER_PROVIDER!r} "
        "(expected 'fake' or 'openai') - never silently falls back"
    )
