"""Stores, categories and politeness settings. IDs come from DISCOVERY.md."""

from pathlib import Path

# Item counts per category from the last real run, for the drop check.
COUNTS_FILE = Path(__file__).resolve().parent.parent / "logs" / "category_counts.json"

# When the last run that saved data finished; decides whether a crawl is due.
LAST_SUCCESS_FILE = COUNTS_FILE.parent / "last_success"

# Scheduled crawl times, Tbilisi hours. A run missed at one of these (Mac off,
# asleep or offline) is caught up by the next hourly check.
SLOTS = (8, 20)

USER_AGENT = "ge-price-tracker/0.1 (price-history research)"

# Seconds between requests to the same store. ee.ge's robots.txt asks for 5.
DELAY = {"zoommer": 1.0, "ee": 5.0, "alta_gamige": 1.0}

# Stores a plain `python -m gept.run` collects. alta.ge blocks automated access
# behind Cloudflare, so its prices come second-hand from gamige.com's open
# comparison API under the store id "alta_gamige" (never a direct observation).
# See DISCOVERY.md.
DEFAULT_STORES = ["zoommer", "ee", "alta_gamige"]

# Hard stop so a pagination bug can't hammer a store.
MAX_PAGES = 100

# zoommer: numeric CategoryId for the Products API.
# ee: leaf category slug (only "-c{id}t" pages list products).
# alta_gamige: gamige.com's own Georgian category name (its ?category= filter).
# A product listed under several categories is kept once, under the first one.
# Every entry must list at least one product, or the run fails.
CATEGORIES: dict[str, dict[str, list]] = {
    "zoommer": {
        "smartphones": [855],
        "laptops": [531],
        "tvs": [505],
        "headphones": [533],
        # Zoommer doesn't sell large appliances.
    },
    "alta_gamige": {
        "smartphones": ["ტელეფონები"],
        "laptops": ["ლეპტოპები"],
        "tvs": ["ტელევიზორები"],
        "headphones": ["ყურსასმენები"],
        "large_appliances": ["მსხვილი ტექნიკა"],
    },
    "ee": {
        "smartphones": ["mobiluri-telefoni-c377t"],
        "laptops": ["noutbuqebi-c58t"],
        "tvs": ["televizori-c73t"],
        # The "ყურსასმენები" section (yursasmenebi-c15s/t) lists no products
        # itself; per ee's mega-menu its product leaves are these three
        # (the fourth child, c126t, is headphone accessories).
        "headphones": ["headphones-c134t", "buds-c127t", "earphones-c131t"],
        "large_appliances": [
            "macivari-c300t",
            "sareckhi-manqana-c77t",
            "sareckhis-manqana-sashrobit-c78t",  # renamed by ee on 2026-11-02
            "sashrobi-c50t",
            "churchlis-sareckhi-manqana-c45t",
            "gazqura-c59t",
            "gazquris-zedapiri-c53t",
            "chasashenebeli-eleqtro-ghumeli-c52t",
        ],
    },
}
