import json
from decimal import Decimal
from pathlib import Path

import pytest

from gept.parse import is_buyable, is_in_stock, listing_from_html, parse_products

FIXTURES = Path(__file__).parent / "fixtures"


def by_id(items):
    return {i.store_product_id: i for i in items}


@pytest.fixture(scope="module")
def zoommer_items():
    data = json.loads((FIXTURES / "zoommer/products_v3_cat855_page1.json").read_text())
    return parse_products("zoommer", "smartphones", data["products"])


@pytest.fixture(scope="module")
def ee_listing_p21():
    return listing_from_html((FIXTURES / "ee/mobiluri-telefoni-c377t_page21.html").read_text())


def test_zoommer_page(zoommer_items):
    assert len(zoommer_items) == 28
    assert all(i.price is not None and i.url.startswith("https://zoommer.ge/") for i in zoommer_items)


def test_zoommer_old_price_from_previous_price(zoommer_items):
    item = by_id(zoommer_items)["54834"]
    assert item.price == Decimal("4069.00")
    assert item.old_price == Decimal("4199.00")
    assert item.url == "https://zoommer.ge/mobile-phones/apple-iphone-18-pro-e-sim-only-256gb-burgundy-p54834"
    assert item.has_discount is True
    assert item.discount_amount == Decimal("130.00")
    assert item.discount_percent == Decimal("3.10")


def test_zoommer_stock_is_unknown(zoommer_items):
    assert all(i.in_stock is None for i in zoommer_items)


def test_zoommer_buyable(zoommer_items):
    assert by_id(zoommer_items)["54602"].buyable is False  # onSaleSoon
    assert by_id(zoommer_items)["54834"].buyable is True


def test_buyable_rules():
    base = {"disableBuyButton": False, "onSaleSoon": False, "price": 100}
    assert is_buyable(base)
    assert not is_buyable(base | {"disableBuyButton": True})
    assert not is_buyable(base | {"onSaleSoon": True})
    assert not is_buyable(base | {"price": 0})


def test_ee_embedded_listing():
    listing = listing_from_html((FIXTURES / "ee/mobiluri-telefoni-c377t_page1.html").read_text())
    assert listing["productsCount"] == 327
    assert len(listing["products"]) == 16


def test_ee_last_page(ee_listing_p21):
    # 20 full pages of 16 + 7 = 327
    assert len(ee_listing_p21["products"]) == 7


def test_ee_old_price_even_when_has_discount_false(ee_listing_p21):
    item = by_id(parse_products("ee", "smartphones", ee_listing_p21["products"]))["22102"]
    assert item.price == Decimal("4929.99")
    assert item.old_price == Decimal("5309.99")
    assert item.has_discount is False  # kept raw, not used for old_price
    assert item.discount_amount == Decimal("0.00")
    assert item.in_stock is True  # storageQuantity 6
    assert item.buyable is False  # ...but disableBuyButton


def test_storage_quantity_stock():
    assert is_in_stock("ee", {"storageQuantity": 3}) is True
    assert is_in_stock("alta", {"storageQuantity": 0}) is False
    assert is_in_stock("alta", {"storageQuantity": None}) is False


def test_listing_from_html_rejects_section_page():
    html = '<script id="__NEXT_DATA__">{"props": {"pageProps": {"schemaBreadcrumbs": []}}}</script>'
    with pytest.raises(ValueError):
        listing_from_html(html)
