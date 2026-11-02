import json
from decimal import Decimal
from pathlib import Path

import pytest

from gept.parse import is_in_stock, listing_from_html, parse_products

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


def test_zoommer_coming_soon_is_not_in_stock(zoommer_items):
    # onSaleSoon=true, isInStock=false (as for every zoommer listing item)
    assert by_id(zoommer_items)["54602"].in_stock is False
    assert by_id(zoommer_items)["54834"].in_stock is True


def test_zoommer_stock_proxy():
    base = {"disableBuyButton": False, "onSaleSoon": False, "price": 100, "isInStock": False}
    assert is_in_stock("zoommer", base)
    assert not is_in_stock("zoommer", base | {"disableBuyButton": True})
    assert not is_in_stock("zoommer", base | {"onSaleSoon": True})
    assert not is_in_stock("zoommer", base | {"price": 0})


def test_ee_embedded_listing():
    listing = listing_from_html((FIXTURES / "ee/mobiluri-telefoni-c377t_page1.html").read_text())
    assert listing["productsCount"] == 327
    assert len(listing["products"]) == 16


def test_ee_last_page(ee_listing_p21):
    # 20 full pages of 16 + 7 = 327
    assert len(ee_listing_p21["products"]) == 7


def test_ee_old_price_even_when_has_discount_false(ee_listing_p21):
    raw = next(p for p in ee_listing_p21["products"] if p["id"] == 22102)
    assert raw["hasDiscount"] is False  # the flag the parser must ignore
    item = by_id(parse_products("ee", "smartphones", ee_listing_p21["products"]))["22102"]
    assert item.price == Decimal("4929.99")
    assert item.old_price == Decimal("5309.99")
    assert item.in_stock is True  # storageQuantity 6, despite disableBuyButton


def test_storage_quantity_stock():
    assert is_in_stock("ee", {"storageQuantity": 3})
    assert not is_in_stock("alta", {"storageQuantity": 0})
    assert not is_in_stock("alta", {"storageQuantity": None})


def test_listing_from_html_rejects_section_page():
    html = '<script id="__NEXT_DATA__">{"props": {"pageProps": {"schemaBreadcrumbs": []}}}</script>'
    with pytest.raises(ValueError):
        listing_from_html(html)
