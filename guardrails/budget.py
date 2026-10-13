"""Budget cap: refuses new requests once the session spent too much."""
from guardrails.config import GUARDRAILS
from tracking.cost import CURRENCY, CostTracker


def over_budget(tracker: CostTracker) -> str | None:
    """Return a message if the session reached a limit, else None."""
    limits = GUARDRAILS["budget"]
    rows = tracker.session_by_agent().values()
    cost = sum(r["cost"] for r in rows)
    tokens = sum(r["input"] + r["output"] for r in rows)
    max_cost = limits.get("max_session_cost") or 0
    max_tokens = limits.get("max_session_tokens") or 0
    if max_cost and cost >= max_cost:
        return f"This session reached its budget of {max_cost:.2f} {CURRENCY} (spent {cost:.4f})."
    if max_tokens and tokens >= max_tokens:
        return f"This session reached its limit of {max_tokens:,} tokens (used {tokens:,})."
    return None
