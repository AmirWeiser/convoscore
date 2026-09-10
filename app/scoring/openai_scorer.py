from openai import OpenAI
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_random_exponential

from app import config
from app.scoring.base import PROMPT_VERSION, ScoreResult, Sentiment
from app.scoring.cost import estimate_cost_usd
from app.scoring.fake import TRANSIENT_FAIL_MARKER

_SYSTEM_PROMPT = (
    "You score a customer-support conversation snippet. Respond with the "
    "conversation's overall sentiment, a risk score from 0.0 (no risk of "
    "churn/escalation) to 1.0 (high risk), and a one-sentence rationale."
)


class _LLMResponse(BaseModel):
    """The schema the model itself fills in - deliberately narrower than
    ScoreResult, which also carries model/tokens/cost filled in by this module
    after the call, not by the LLM. Constraints here (Literal, ge/le) are
    encoded into the structured-output JSON schema itself, not just checked
    after the fact."""

    sentiment: Sentiment
    risk_score: float = Field(ge=0.0, le=1.0)
    rationale: str


class OpenAIScorer:
    def __init__(self) -> None:
        # max_retries=0: the SDK's own built-in retry defaults to 2 (3 total
        # attempts) at the transport level, which would nest inside the
        # tenacity retry below - two overlapping retry mechanisms. tenacity is
        # the single source of truth for attempt count (OPENAI_MAX_RETRIES).
        self._client = OpenAI(
            api_key=config.OPENAI_API_KEY,
            timeout=config.OPENAI_TIMEOUT_SECONDS,
            max_retries=0,
        )

    def score(self, text: str) -> ScoreResult:
        # Test/demo-only deterministic transient failure trigger - honored here
        # too so the DLQ demo (Phase 4/9) works identically regardless of
        # SCORER_PROVIDER, without a real API call.
        if TRANSIENT_FAIL_MARKER in text:
            raise RuntimeError("deterministic transient failure (openai scorer)")
        return self._score_with_retry(text)

    @retry(stop=stop_after_attempt(config.OPENAI_MAX_RETRIES), wait=wait_random_exponential(multiplier=1, max=10))
    def _score_with_retry(self, text: str) -> ScoreResult:
        response = self._client.chat.completions.parse(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            response_format=_LLMResponse,
        )
        message = response.choices[0].message
        if message.refusal:
            # One error path for any scoring failure, refusal included - see
            # DECISIONS.md. Retried like any other failure, bounded by the
            # same max-attempts.
            raise RuntimeError(f"model refused to score: {message.refusal}")

        parsed = message.parsed
        usage = response.usage
        cost = estimate_cost_usd(
            config.OPENAI_MODEL, usage.prompt_tokens, usage.completion_tokens
        )
        return ScoreResult(
            sentiment=parsed.sentiment,
            risk_score=parsed.risk_score,
            rationale=parsed.rationale,
            model=config.OPENAI_MODEL,
            prompt_version=PROMPT_VERSION,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            estimated_cost_usd=cost,
        )
