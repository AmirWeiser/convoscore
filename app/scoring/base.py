from typing import Protocol

from pydantic import BaseModel, Field

PROMPT_VERSION = "v1"


class ScoreResult(BaseModel):
    sentiment: str = Field(description="One of: positive, neutral, negative")
    risk_score: float = Field(ge=0.0, le=1.0, description="0 = no risk, 1 = high risk")
    rationale: str
    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float


class Scorer(Protocol):
    def score(self, text: str) -> ScoreResult: ...
