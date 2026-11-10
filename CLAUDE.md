# GE Price Tracker — price history for Georgian electronics stores

## Goal
Validate demand for a Keepa-like price history tool for Georgian electronics
retailers. NOT building a product yet. Phase 1 = collect data before
Black Friday (27 Nov 2026), publish a "fake discount" report, measure reaction.

## Scope (do not expand without asking)
- Stores: zoommer.ge, ee.ge fetched directly. alta.ge is NOT fetched directly:
  Cloudflare blocks automated access and we don't work around it. Its prices
  come second-hand from gamige.com's open comparison API (store id
  `alta_gamige`, 2026-11-09), never presented as a direct alta observation.
- Categories: smartphones, laptops, TVs, headphones, large appliances
  (large appliances = ee + alta_gamige; Zoommer doesn't sell them)
- Out of scope: cross-store product matching, browser extension, user accounts, UI

## Data collection rules
- Discovery first: for each store, inspect network traffic and report where
  prices come from (JSON API > embedded JSON/JSON-LD > HTML) BEFORE writing a scraper.
- Prefer httpx; Playwright only if blocked or JS-only rendering.
- Scrape category listing pages, not individual product pages.
- Max ~1 req/sec per store, honest User-Agent, respect robots.txt.
- Capture both current price and crossed-out old_price, plus stock status.
- old_price = `previousPrice` only; `hasDiscount`/`discountAmount` are unreliable.
- in_stock: `storageQuantity > 0` (ee); NULL for Zoommer (no stock field) and
  for alta_gamige (gamige's list endpoint doesn't report stock).
- alta_gamige: read price + old_price per product from gamige's /api/search
  (store=Alta). The list has no stock or link, so in_stock stays NULL and
  buyable just mirrors price > 0. The real alta.ge link is resolved once per
  product from /api/product/{id} (reused from the DB after that, so steady-state
  runs stay light). Attribute gamige as the source in any report.
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
Status (2026-11-09): GitHub-hosted runners get 403 from both stores, so the
crawl runs from launchd on the Mac (`scripts/install_schedule.sh`, logs in
`logs/`). The repo lives in `~/ge-price-tracker`: launchd jobs can't read
`~/Documents` (macOS privacy protection).

## Judging a discount (dashboard.py, and the report)
Same yardstick as the EU Omnibus rule: compare the sale price with the lowest
price the product sold at in the 30 days before the sale started — not with
the store's crossed-out price.
- fake: sale price isn't below that ("500, was 700" after a day at 500; raise
  then "discount" back; a paused-and-restarted sale), or the crossed-out price
  was raised mid-sale while the price stayed.
- exaggerated: cheaper, but 10+ percentage points less than claimed.
- real / unconfirmed (on sale since we first saw it) / none.

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
Python 3.12, httpx, selectolax, psycopg, pytest.
