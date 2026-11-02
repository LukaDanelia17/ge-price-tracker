from decimal import Decimal

from gept.db import changed_items
from gept.fetch import paginate
from gept.parse import Item


def item(pid="1", price="100.00", old=None, stock=True):
    return Item(
        store="ee",
        store_product_id=pid,
        name="x",
        url="https://ee.ge/x",
        category="tvs",
        price=Decimal(price),
        old_price=Decimal(old) if old else None,
        in_stock=stock,
    )


def test_new_product_is_a_change():
    assert changed_items({}, [item()]) == [item()]


def test_unchanged_product_is_skipped():
    # numeric(12,2) comes back from Postgres as Decimal('100.00')
    latest = {"1": (Decimal("100.00"), None, True)}
    assert changed_items(latest, [item()]) == []


def test_each_field_change_counts():
    latest = {"1": (Decimal("100.00"), None, True)}
    assert changed_items(latest, [item(price="90.00")])
    assert changed_items(latest, [item(old="120.00")])
    assert changed_items(latest, [item(stock=False)])


def test_paginate_stops_at_products_count():
    calls = []

    def fetch_page(n):
        calls.append(n)
        return {"productsCount": 33, "products": [{}] * (16 if n < 3 else 1)}

    assert len(list(paginate(fetch_page, 16))) == 3
    assert calls == [1, 2, 3]


def test_paginate_stops_on_empty_page():
    pages = list(paginate(lambda n: {"productsCount": 999, "products": [] if n == 2 else [{}]}, 16))
    assert len(pages) == 2
