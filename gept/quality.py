"""Checks after each run (CLAUDE.md → Quality checks). Pure functions that
return human-readable issue lines; run.py sends them to Telegram."""

from collections.abc import Iterable
from decimal import Decimal

from .parse import Item

# Alert when a category lists less than half of what it listed last run.
DROP_RATIO = Decimal("0.5")
# Flag a price that moved more than this fraction since the last stored price.
PRICE_JUMP = Decimal("0.9")


def count_issues(previous: dict[str, int], current: dict[str, int]) -> list[str]:
    """`current` holds only categories that finished; failed ones are reported elsewhere."""
    issues = []
    for key, n in current.items():
        before = previous.get(key)
        if n == 0:
            issues.append(f"{key}: 0 items")
        elif before and n < before * DROP_RATIO:
            issues.append(f"{key}: {n} items, was {before}")
    return issues


def price_issues(latest: dict[str, tuple], changed: Iterable[Item]) -> list[str]:
    """Check only rows that changed this run, so a product stuck at price 0
    is reported once, not every run. `latest` maps store_product_id to the
    previously stored state, whose first field is the price."""
    issues = []
    for i in changed:
        if i.price is None or i.price <= 0:
            issues.append(f"{i.store}: price {i.price} for {i.name} {i.url}")
            continue
        before = (latest.get(i.store_product_id) or (None,))[0]
        if before and abs(i.price - before) / before > PRICE_JUMP:
            issues.append(f"{i.store}: {before} → {i.price} for {i.name} {i.url}")
    return issues
