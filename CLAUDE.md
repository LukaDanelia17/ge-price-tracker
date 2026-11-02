# GE Price Tracker — price history for Georgian electronics stores

## Goal
Validate demand for a Keepa-like price history tool for Georgian electronics
retailers. NOT building a product yet. Phase 1 = collect data before
Black Friday (27 Nov 2026), publish a "fake discount" report, measure reaction.

## Scope (do not expand without asking)
- Stores: zoommer.ge, ee.ge. alta.ge dropped for Phase 1 (2026-11-02):
  Cloudflare blocks automated browsers on its API; don't work around it.
- Categories: smartphones, laptops, TVs, headphones, large appliances
  (large appliances = ee only; Zoommer doesn't sell them)
- Out of scope: cross-store product matching, browser extension, user accounts, UI

## Data collection rules
- Discovery first: for each store, inspect network traffic and report where
  prices come from (JSON API > embedded JSON/JSON-LD > HTML) BEFORE writing a scraper.
- Prefer httpx; Playwright only if blocked or JS-only rendering.
- Scrape category listing pages, not individual product pages.
- Max ~1 req/sec per store, honest User-Agent, respect robots.txt.
- Capture both current price and crossed-out old_price, plus stock status.
- old_price = `previousPrice` only; `hasDiscount`/`discountAmount` are unreliable.
- in_stock: `storageQuantity > 0` (ee, alta); NULL for Zoommer (no stock field).
- buyable (all stores): `not disableBuyButton and not onSaleSoon and price > 0`.
- Keep raw hasDiscount / discountAmount / discountPercent in price_changes.
- A configured category that returns 0 items fails the run; fix the config,
  don't delete the entry.
- ee.ge robots.txt sets Crawl-delay 5 → 5 s between ee requests.
- See DISCOVERY.md for per-store endpoints and category IDs.

## Storage
Postgres (Supabase). Tables: products, price_changes.
Insert into price_changes ONLY when price, old_price, in_stock or buyable changed.

## Scheduling
Twice daily (08:00 / 20:00 Tbilisi). If GitHub Actions is geo-blocked or hits
Cloudflare, fall back to a local runner.

## Quality checks (each run)
- Item count per store/category vs previous run (alert on 0 or >50% drop)
- Price <= 0, null, or >90% change overnight → flag
- Alerts to Telegram

## Testing
pytest with saved HTML/JSON fixtures per store, so parser changes are tested
offline. Commit fixtures.

## Export for analysis
`export.py` → CSV (products + price_changes) for analysis outside the repo.

## Stack
Python 3.12, httpx, selectolax, Playwright (fallback), psycopg, pytest.
