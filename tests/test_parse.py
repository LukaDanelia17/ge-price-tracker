import json
from decimal import Decimal
from pathlib import Path

import pytest

from gept.parse import (
    _alta_value,
    is_buyable,
    is_in_stock,
    kontakt_listing,
    kontakt_money,
    listing_from_html,
    parse_gamige,
    parse_kontakt,
    parse_products,
)

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


@pytest.fixture(scope="module")
def gamige_items():
    data = json.loads((FIXTURES / "gamige/search_alta.json").read_text())
    return parse_gamige("alta_gamige", "smartphones", data["results"])


def test_gamige_skips_products_alta_does_not_carry(gamige_items):
    # The EE-only item (no "Alta" in its prices) is dropped.
    assert set(by_id(gamige_items)) == {"6657907", "6280223", "6661320"}


def test_gamige_takes_alta_price_and_old_price(gamige_items):
    item = by_id(gamige_items)["6657907"]
    assert item.store == "alta_gamige"
    assert item.price == Decimal("3849.00")
    assert item.old_price == Decimal("4049.00")  # alta's, not zoomer's or ee's
    assert item.url == "https://alta.ge/telefonebi/apple-iphone-17-pro-max-256gb-p12345"  # resolved alta link
    assert item.category == "smartphones"  # the category we queried, not gamige's label


def test_gamige_stock_unknown_discount_flags_empty(gamige_items):
    item = by_id(gamige_items)["6657907"]
    assert item.in_stock is None  # gamige's listing doesn't report stock
    assert item.buyable is True  # price > 0
    assert (item.has_discount, item.discount_amount, item.discount_percent) == (None, None, None)


def test_gamige_no_old_price_and_name_stripped(gamige_items):
    assert by_id(gamige_items)["6280223"].old_price is None  # no oldPrices block
    assert by_id(gamige_items)["6661320"].name == "HONOR CHOICE Headphones Lite"


def test_gamige_url_falls_back_to_gamige_page_without_alta_link(gamige_items):
    # No altaUrl resolved (detail lookup missing/failed) → link to gamige's page.
    assert by_id(gamige_items)["6280223"].url == "https://gamige.com/?p=6280223"


def test_alta_value_case_insensitive():
    assert _alta_value({"ALTA": 5, "EE": 9}) == 5
    assert _alta_value({"EE": 9}) is None
    assert _alta_value(None) is None


@pytest.fixture(scope="module")
def kontakt_tvs():
    return kontakt_listing((FIXTURES / "kontakt/televizorebi_page1.html").read_text())


def test_kontakt_page_lists_cards_and_the_next_page(kontakt_tvs):
    assert len(kontakt_tvs["products"]) == 20
    assert kontakt_tvs["next_url"] == "https://kontakt.ge/televizorebi/televizorebi?p=2"


def test_kontakt_last_page_has_no_next():
    listing = kontakt_listing((FIXTURES / "kontakt/kabeliani-qursasmenebi_page1.html").read_text())
    assert len(listing["products"]) == 17 and listing["next_url"] is None


def test_kontakt_prices_and_stock(kontakt_tvs):
    items = by_id(parse_kontakt("kontakt", "tvs", kontakt_tvs["products"]))
    toshiba = items["31328"]
    assert (toshiba.price, toshiba.old_price) == (Decimal("1149.99"), Decimal("1499.99"))
    assert toshiba.url.startswith("https://kontakt.ge/") and toshiba.in_stock is True and toshiba.buyable
    no_price = items["23252"]  # card shows "------ ₾"
    assert (no_price.price, no_price.old_price, no_price.buyable) == (None, None, False)
    assert sum(i.old_price is None for i in items.values()) > 0  # some cards aren't discounted


def test_kontakt_stock_unknown_without_the_in_stock_filter():
    card = ('<div class="prodItem product-item"><a class="prodItem__img" href="https://kontakt.ge/x"></a>'
            '<a data-product-id="7"></a><div class="prodItem__title">X</div>'
            '<div class="prodItem__prices"><strong><b>10,00 ₾</b></strong></div></div>')
    assert kontakt_listing(card)["products"][0]["in_stock"] is None


def test_kontakt_card_without_id_fails_loudly():
    with pytest.raises(ValueError):
        kontakt_listing('<div class="prodItem product-item"><div class="prodItem__title">X</div></div>')


def test_kontakt_money():
    assert kontakt_money("1539,99\xa0₾") == Decimal("1539.99")
    assert kontakt_money("1.539,99 ₾") == Decimal("1539.99")
    assert kontakt_money("------ ₾") is None and kontakt_money(None) is None
