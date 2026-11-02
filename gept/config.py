"""Stores, categories and politeness settings. IDs come from DISCOVERY.md."""

USER_AGENT = "ge-price-tracker/0.1 (price-history research)"

# Seconds between requests to the same store. ee.ge's robots.txt asks for 5.
DELAY = {"zoommer": 1.0, "alta": 1.0, "ee": 5.0}

# Stores a plain `python -m gept.run` collects. alta is dropped for Phase 1:
# Cloudflare returns 403 on its listing API for Playwright (headless and
# headed). Kept opt-in via --store alta. See DISCOVERY.md.
DEFAULT_STORES = ["zoommer", "ee"]

# Hard stop so a pagination bug can't hammer a store.
MAX_PAGES = 100

# zoommer / alta: numeric CategoryId for the Products API.
# ee: leaf category slug (only "-c{id}t" pages list products).
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
    "alta": {
        "smartphones": [16],
        "laptops": [34, 110],
        "tvs": [88],
        "headphones": [22, 300, 308, 212],
        # fridge, washer, dryer, dishwasher, gas cooker, hob, built-in oven, built-in hob
        "large_appliances": [135, 136, 137, 138, 139, 283, 141, 142],
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
