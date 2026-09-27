"""Configured per-million-token prices and estimates from reported usage."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _decimal_rate(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError("price must be a JSON number or decimal string")
    try:
        rate = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("price must be a decimal number") from error
    if rate.is_finite() and rate.as_tuple().exponent < -9:
        raise ValueError("price must have at most 9 decimal places")
    return rate


Rate = Annotated[
    Decimal,
    BeforeValidator(_decimal_rate),
    Field(ge=0, max_digits=18, decimal_places=9, allow_inf_nan=False),
]


class ModelPrice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    currency: str = Field(pattern=r"^[A-Z]{3}$")
    input: Rate
    output: Rate
    cache_read: Rate


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int | None
    completion_tokens: int | None
    cached_tokens: int | None


def cost_summary(costs: list[dict | None]) -> dict:
    amounts: dict[str, Decimal] = {}
    known = 0
    with localcontext() as context:
        for cost in costs:
            if cost is None:
                continue
            known += 1
            currency = cost["currency"]
            amount = Decimal(cost["amount"])
            previous = amounts.get(currency, Decimal(0))
            context.prec = max(previous.adjusted(), amount.adjusted(), 0) - min(
                previous.as_tuple().exponent, amount.as_tuple().exponent) + 2
            amounts[currency] = previous + amount
    return {"basis": "configured_estimate",
            "known_amounts": {currency: format(amount, "f") for currency, amount in sorted(amounts.items())},
            "known_calls": known, "unknown_calls": len(costs) - known}


def estimate_cost(price: ModelPrice | None, usage: TokenUsage | None) -> dict[str, str] | None:
    if price is None or usage is None:
        return None
    prompt = usage.prompt_tokens
    completion = usage.completion_tokens
    cached = usage.cached_tokens
    if prompt is None or completion is None:
        return None
    if cached is None:
        if price.input != price.cache_read:
            return None
        cached = 0
    with localcontext() as context:
        rates = (price.input, price.cache_read, price.output)
        context.prec = max(28, len(str(max(prompt, completion, cached)))
                           + max(rate.adjusted() for rate in rates)
                           - min(rate.as_tuple().exponent for rate in rates) + 3)
        amount = ((prompt - cached) * price.input + cached * price.cache_read
                  + completion * price.output) / Decimal(1_000_000)
    return {"basis": "configured_estimate", "currency": price.currency, "amount": format(amount, "f")}
