import pytest

from app import config
from app.scoring.factory import get_scorer


def test_unsupported_provider_raises(monkeypatch):
    monkeypatch.setattr(config, "SCORER_PROVIDER", "not-a-real-provider")
    get_scorer.cache_clear()
    with pytest.raises(ValueError):
        get_scorer()
    get_scorer.cache_clear()
