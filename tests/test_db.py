from decimal import Decimal

import pytest

from gept import run
from gept.db import changed_items
from gept.fetch import paginate
from gept.parse import Item


def item(pid="1", price="100.00", old=None, stock=True, buyable=True, has_discount=False):
    return Item(
        store="ee",
        store_product_id=pid,
        name="x",
        url="https://ee.ge/x",
        category="tvs",
        price=Decimal(price),
        old_price=Decimal(old) if old else None,
        in_stock=stock,
        buyable=buyable,
        has_discount=has_discount,
        discount_amount=None,
        discount_percent=None,
    )


LATEST = {"1": (Decimal("100.00"), None, True, True)}


def test_new_product_is_a_change():
    assert changed_items({}, [item()]) == [item()]


def test_unchanged_product_is_skipped():
    # numeric(12,2) comes back from Postgres as Decimal('100.00')
    assert changed_items(LATEST, [item()]) == []


def test_each_tracked_field_counts():
    assert changed_items(LATEST, [item(price="90.00")])
    assert changed_items(LATEST, [item(old="120.00")])
    assert changed_items(LATEST, [item(stock=False)])
    assert changed_items(LATEST, [item(buyable=False)])


def test_unknown_stock_compares_as_unknown():
    latest = {"1": (Decimal("100.00"), None, None, True)}
    assert changed_items(latest, [item(stock=None)]) == []
    assert changed_items(latest, [item(stock=True)])


def test_discount_flags_alone_dont_count():
    assert changed_items(LATEST, [item(has_discount=True)]) == []


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


def product(pid):
    return {"id": pid, "name": f"p{pid}", "route": f"x-p{pid}", "price": 10, "storageQuantity": 1}


@pytest.fixture
def fake_ee(monkeypatch):
    """Point collect() at canned listings instead of the network."""
    listings: dict[str, list[dict]] = {}

    def install(**by_slug):
        listings.update(by_slug)
        monkeypatch.setattr(run, "CATEGORIES", {"ee": {"tvs": list(listings)}})
        monkeypatch.setattr(run.fetch, "ee", lambda client, throttle, slug: iter(listings[slug]))

    return install


def test_zero_item_category_fails_the_run(fake_ee):
    fake_ee(good=[{"productsCount": 1, "products": [product(1)]}], empty=[{"productsCount": 0, "products": []}])
    items, failures = run.collect("ee")
    assert list(items) == ["1"]
    assert failures == ["ee/tvs/empty"]


def test_repeated_page_fails_the_category(fake_ee):
    page = {"productsCount": 3, "products": [product(1), product(2)]}
    fake_ee(stuck=[page, page])
    _, failures = run.collect("ee")
    assert failures == ["ee/tvs/stuck"]


def test_ee_follows_renamed_slug_keeping_the_page():
    import httpx

    from gept.fetch import Throttle, ee

    def listing(ids):
        products = ",".join(
            f'{{"id": {i}, "name": "p{i}", "route": "x-p{i}", "price": 1, "storageQuantity": 1}}' for i in ids
        )
        return (
            '<script id="__NEXT_DATA__">{"props": {"pageProps": {"initialListingData": '
            f'{{"productsCount": 17, "products": [{products}]}}}}}}}}</script>'
        )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old-c78t":  # ee's redirect drops the query string
            return httpx.Response(301, headers={"Location": "https://ee.ge/new-c78t"})
        page = request.url.params.get("page")
        return httpx.Response(200, text=listing(range(1, 17) if page in (None, "1") else [17]))

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    pages = list(ee(client, Throttle(0), "old-c78t"))
    assert [len(pg["products"]) for pg in pages] == [16, 1]
    assert pages[1]["products"][0]["id"] == 17


def test_transient_errors_are_retried(monkeypatch):
    import httpx

    from gept import fetch

    monkeypatch.setattr(fetch, "RETRY_WAITS", (0, 0))
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("[Errno 49] Can't assign requested address")
        if len(calls) == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"productsCount": 1, "products": [product(1)]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    pages = list(fetch.zoommer(client, fetch.Throttle(0), 855))
    assert len(calls) == 3 and pages[0]["products"][0]["id"] == 1


def test_trickling_response_hits_the_deadline(monkeypatch):
    import time

    import httpx

    from gept import fetch

    monkeypatch.setattr(fetch, "REQUEST_DEADLINE", 0.05)

    class Trickle(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(10):
                time.sleep(0.02)
                yield b" "

    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Trickle())))
    with pytest.raises(httpx.ReadTimeout):
        fetch.get_bounded(client, "https://ee.ge/x")


def test_bounded_get_keeps_json_and_redirect_history():
    import httpx

    from gept import fetch

    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(301, headers={"Location": "https://ee.ge/new"})
        return httpx.Response(200, json={"ok": "ფასი"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    r = fetch.get_bounded(client, "https://ee.ge/old")
    assert r.json() == {"ok": "ფასი"}
    assert r.url.path == "/new" and len(r.history) == 1


def test_client_errors_are_not_retried(monkeypatch):
    import httpx

    from gept import fetch

    monkeypatch.setattr(fetch, "RETRY_WAITS", (0, 0))
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        list(fetch.zoommer(client, fetch.Throttle(0), 855))
    assert len(calls) == 1


def test_count_mismatch_only_warns(fake_ee, caplog):
    fake_ee(short=[{"productsCount": 3, "products": [product(1), product(2)]}])
    items, failures = run.collect("ee")
    assert len(items) == 2 and failures == []
    assert "productsCount=3" in caplog.text
