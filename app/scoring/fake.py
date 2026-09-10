import hashlib

from app.scoring.base import PROMPT_VERSION, ScoreResult

TRANSIENT_FAIL_MARKER = "__TRANSIENT_FAIL__"

_SENTIMENTS = ["negative", "neutral", "positive"]


class FakeScorer:
    """Deterministic - same input always produces the same output. Used only by
    the automated test suite and for fast local iteration; never for the demo.
    Honors TRANSIENT_FAIL_MARKER so the DLQ demo (Phase 4/9) can force a
    guaranteed, repeatable failure on demand instead of hoping for a real one."""

    def score(self, text: str) -> ScoreResult:
        if TRANSIENT_FAIL_MARKER in text:
            raise RuntimeError("deterministic transient failure (fake scorer)")

        digest = hashlib.sha256(text.encode()).hexdigest()
        return ScoreResult(
            sentiment=_SENTIMENTS[int(digest[0], 16) % 3],
            risk_score=round(int(digest[1:3], 16) / 255, 2),
            rationale=f"fake deterministic score (hash={digest[:8]})",
            model="fake",
            prompt_version=PROMPT_VERSION,
            input_tokens=len(text.split()),
            output_tokens=8,
            estimated_cost_usd=0.0,
        )
