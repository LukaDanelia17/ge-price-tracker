"""Turn the product JSON shared by zoommer.ge, alta.ge and ee.ge into Items.

All three stores run the same platform, so one parser covers them. See
DISCOVERY.md for which fields are trustworthy.
"""

import json
from dataclasses import dataclass
from decimal import Decimal

from selectolax.lexbor import LexborHTMLParser

BASE_URL = {
    "zoommer": "https://zoommer.ge/",
    "alta": "https://alta.ge/",
    "ee": "https://ee.ge/",
}

CENTS = Decimal("0.01")


@dataclass(frozen=True)
class Item:
    store: str
    store_product_id: str
    name: str
    url: str
    category: str
    price: Decimal | None
    old_price: Decimal | None
    in_stock: bool

    def state(self) -> tuple:
        """The fields whose change is worth a price_changes row."""
        return (self.price, self.old_price, self.in_stock)


def money(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(CENTS)


def is_in_stock(store: str, p: dict) -> bool:
    if store == "zoommer":
        # Zoommer's listing reports isInStock=false for everything and has no
        # storageQuantity, so use "can be bought" as the proxy.
        return not p.get("disableBuyButton") and not p.get("onSaleSoon") and (p.get("price") or 0) > 0
    return (p.get("storageQuantity") or 0) > 0


def parse_products(store: str, category: str, products: list[dict]) -> list[Item]:
    return [
        Item(
            store=store,
            store_product_id=str(p["id"]),
            name=p["name"].strip(),
            url=BASE_URL[store] + p["route"].lstrip("/"),
            category=category,
            price=money(p.get("price")),
            # Only previousPrice is the crossed-out price; hasDiscount and
            # discountAmount disagree with it on both zoommer and ee.
            old_price=money(p.get("previousPrice")),
            in_stock=is_in_stock(store, p),
        )
        for p in products
    ]


def next_data(html: str) -> dict:
    node = LexborHTMLParser(html).css_first("script#__NEXT_DATA__")
    if node is None:
        raise ValueError("no __NEXT_DATA__ script in page")
    return json.loads(node.text())


def listing_from_html(html: str) -> dict:
    """The listing envelope ({products, productsCount, ...}) embedded in a category page."""
    listing = next_data(html)["props"]["pageProps"].get("initialListingData")
    if listing is None:
        raise ValueError("page has no initialListingData (not a leaf category?)")
    return listing
