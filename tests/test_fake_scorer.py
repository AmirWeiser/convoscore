import pytest

from app.scoring.fake import FakeScorer, TRANSIENT_FAIL_MARKER


def test_same_input_produces_same_output():
    scorer = FakeScorer()
    text = "Customer is upset about a late delivery."
    first = scorer.score(text)
    second = scorer.score(text)
    assert first == second


def test_different_input_can_produce_different_output():
    scorer = FakeScorer()
    a = scorer.score("A short, calm message.")
    b = scorer.score("A completely different, much longer angry message here.")
    assert a.risk_score != b.risk_score or a.sentiment != b.sentiment


def test_transient_fail_marker_always_raises():
    scorer = FakeScorer()
    with pytest.raises(RuntimeError):
        scorer.score(f"some text {TRANSIENT_FAIL_MARKER} more text")
    # deterministic - raises every time, not just once
    with pytest.raises(RuntimeError):
        scorer.score(f"some text {TRANSIENT_FAIL_MARKER} more text")
