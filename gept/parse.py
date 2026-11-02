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
    in_stock: bool | None  # None = the store doesn't report stock (zoommer)
    buyable: bool
    # Raw discount fields as the store reports them. They disagree with
    # previousPrice, so they are kept for analysis but never used as old_price.
    has_discount: bool | None
    discount_amount: Decimal | None
    discount_percent: Decimal | None

    def state(self) -> tuple:
        """The fields whose change is worth a price_changes row."""
        return (self.price, self.old_price, self.in_stock, self.buyable)


def money(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(CENTS)


def is_in_stock(store: str, p: dict) -> bool | None:
    if store == "zoommer":
        # Zoommer's listing reports isInStock=false for everything and has no
        # storageQuantity: stock is unknown.
        return None
    return (p.get("storageQuantity") or 0) > 0


def is_buyable(p: dict) -> bool:
    """The listing would let you add it to the cart."""
    return not p.get("disableBuyButton") and not p.get("onSaleSoon") and (p.get("price") or 0) > 0


def parse_products(store: str, category: str, products: list[dict]) -> list[Item]:
    return [
        Item(
            store=store,
            store_product_id=str(p["id"]),
            name=p["name"].strip(),
            url=BASE_URL[store] + p["route"].lstrip("/"),
            category=category,
            price=money(p.get("price")),
            # Only previousPrice is the crossed-out price.
            old_price=money(p.get("previousPrice")),
            in_stock=is_in_stock(store, p),
            buyable=is_buyable(p),
            has_discount=p.get("hasDiscount"),
            discount_amount=money(p.get("discountAmount")),
            discount_percent=money(p.get("discountPercent")),
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
