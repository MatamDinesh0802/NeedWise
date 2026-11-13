"""Token prices for cost reporting (usage per message, evaluation reports).

USD per million tokens (input, output), as published by each provider. A tenant's catalog
price, stamped on the usage by the tenant resolver, comes first; then this table. Models with
neither (local models, unknown names) report no cost rather than a guess.
"""

from needwise.providers.llm import LLMUsage

PRICES: dict[str, tuple[float, float]] = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    # Gemini through an OpenAI-compatible endpoint (Vertex AI uses the "google/" prefix).
    "gemini-3.1-flash-lite": (0.25, 1.5),
    "google/gemini-3.1-flash-lite": (0.25, 1.5),
}


def cost_usd(usage: LLMUsage) -> float | None:
    price = usage.price_per_mtok or PRICES.get(usage.model)
    if price is None:
        return None
    return (usage.input_tokens * price[0] + usage.output_tokens * price[1]) / 1_000_000
