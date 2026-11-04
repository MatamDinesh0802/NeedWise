"""Token prices for cost reporting (usage per message, evaluation reports).

USD per million tokens (input, output), as published by each provider. Models that are not
listed (local models, unknown names) report no cost rather than a guess.
"""

from workmind.providers.llm import LLMUsage

PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
}


def cost_usd(usage: LLMUsage) -> float | None:
    price = PRICES.get(usage.model)
    if price is None:
        return None
    return (usage.input_tokens * price[0] + usage.output_tokens * price[1]) / 1_000_000
