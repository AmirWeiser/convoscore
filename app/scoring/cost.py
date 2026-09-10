# Approximate USD price per 1M tokens. Not fetched dynamically - OpenAI's
# pricing changes over time, so this is a documented estimate, not a billing
# source of truth. See DECISIONS.md.
_PRICING_PER_1M_TOKENS = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
}
_DEFAULT_PRICE = {"input": 0.15, "output": 0.60}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price = _PRICING_PER_1M_TOKENS.get(model, _DEFAULT_PRICE)
    cost = (input_tokens / 1_000_000) * price["input"] + (
        output_tokens / 1_000_000
    ) * price["output"]
    return round(cost, 6)
