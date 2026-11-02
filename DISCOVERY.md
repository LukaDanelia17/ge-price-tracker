# Discovery — where prices come from (2026-11-02)

All three stores run the **same e-commerce platform** (Next.js pages-router
front end, .NET/Kestrel backend, `/api/proxy/v1/Products/v3|v4` endpoints,
identical product JSON schema). One parser can map all three; only the
transport differs.

## Summary

| Store | Best source | Transport | Page size | old_price | Stock |
|---|---|---|---|---|---|
| zoommer.ge | JSON API `GET /api/proxy/v1/Products/v3?CategoryId={id}&Page={n}&Limit=28` | **httpx**, no cookies/auth | 28 | `previousPrice` | ⚠ no reliable field (see below) |
| alta.ge | JSON API `GET /api/proxy/v1/Products/v4?CategoryId={id}&Limit=16&Page={n}` (page 1 also in `__NEXT_DATA__`) | **Playwright** — Cloudflare challenge on every non-browser request | 16 (Limit>16 → 403) | `previousPrice` | `storageQuantity` |
| ee.ge | Embedded JSON: `__NEXT_DATA__.props.pageProps.initialListingData` on `/{slug}-c{id}t?page={n}` | **httpx**, plain HTML | 16 | `previousPrice` | `storageQuantity` |

## Shared product schema (fields we need)

```
id, name, route, barCode, brandName, categoryId, categoryName,
price            -> price
previousPrice    -> old_price (null when no crossed-out price)
discountAmount, discountPercent, hasDiscount   -- informational, NOT reliable
storageQuantity  -> in_stock = storageQuantity > 0   (alta, ee)
disableBuyButton, onSaleSoon, isPreOrderProduct
```

Response envelope: `products[]`, `productsCount`, `hasNextPage` (alta/ee),
`categoryName`. Paginate until `page * size >= productsCount`.

## zoommer.ge

- robots.txt: allows category pages and `/api/proxy`; disallows `?orderby=`,
  `?viewmode=`, `?pagesize=` on HTML pages (we don't use those).
- Category page XHR → `Products/v3`. Works from curl with an honest UA, no
  cookies. Behind Cloudflare, but not challenged (from a Georgian IP — the
  response came through Cloudflare's Tbilisi edge).
- Phones (cat 855): 450 products, 17 pages; 134 with `previousPrice`.
- **Stock caveat:** `isInStock` is `false` for all 450 listing items, even ones
  the product page shows "in stock" at every branch. There's no
  `storageQuantity` in Zoommer's listing and no stock filter. **Decided
  (2026-11-02):** `in_stock = not disableBuyButton and not onSaleSoon and price > 0`
  ("buyable"). It's a proxy, not true stock — say so in the report.
  First live run: 1076 of 1086 items count as buyable, so for Zoommer this
  field is close to "listed" and carries little signal.
- `hasDiscount` is true for 198 items but only 134 have `previousPrice`.
  Trust `previousPrice` only.
- One item had `price <= 0` → the quality check will catch these.
- Category IDs:
  - smartphones **855**
  - laptops **531** (152)
  - TVs **505** (61)
  - headphones **533** (424, includes buds)
  - large appliances — **Zoommer doesn't sell them** (none of its 13 top-level menu sections covers them)

## alta.ge

- robots.txt: only account/cart/search/compare disallowed.
- **Every** request from curl/httpx (HTML and API, any UA) gets Cloudflare's
  "Just a moment..." managed challenge (403). A real browser passes.
- Category HTML embeds page 1 in `__NEXT_DATA__.props.pageProps.initialListingData`;
  "ნახე მეტი" (see more) calls `Products/v4?CategoryId=..&Limit=16&Page=n`.
  `v3` and `Limit=48` both 403 → stick exactly to what the site sends.
- Plan was: Playwright opens the site once (clears the challenge), then calls
  `Products/v4` via `page.evaluate(fetch)`. **Tested 2026-11-02 — doesn't work:**
  headless Chromium gets Cloudflare's "Attention Required" block page; headed
  Chromium loads the site and receives `cf_clearance`, but `Products/v4` still
  returns 403 (it returns 200 in a normal desktop browser). Cloudflare is
  blocking automated browsers on the API on purpose. Getting around that means
  hiding automation (stealth plugins etc.) — not doing that. Alta is out of
  the default run until there's a sanctioned route (see Decisions).
- Phones (cat 16): 299 products → 19 pages. `storageQuantity` is a real count.
- Category IDs:
  - smartphones **16**
  - laptops **34** (+ gaming laptops 110)
  - TVs **88**
  - headphones: parent **299**, children 22 / 300 / 308 / 212
  - large appliances: parent **6**, refrigerator **135**; still need the washer/dishwasher/oven IDs

## ee.ge (Elite)

- robots.txt: `Crawl-delay: 5` for `*` (the file is malformed — several rules
  on one line — but the intent is clear). **Use 5 s between requests**, not 1 s.
- The API (`ee-api.ee.ge/v1/Products/v3`) returns 401 without the anonymous
  JWT the site sets in a cookie. Not needed: the SSR HTML has the same JSON.
- `?page=N` works in plain HTML (verified pages 1, 2, 21; no overlap).
  Phones: 327 products, 21 pages × 16. ~450 KB per page.
- Only leaf categories (`-c{id}t`) list products; section pages (`-c{id}s`) return 0–1.
- **Slugs change without notice** (`sareckhi-manqana-sashrobit-c78t` →
  `sareckhis-…` mid-day on 2026-11-02). The old slug 301-redirects to the new
  one but **drops `?page=N`**, so every page silently becomes page 1. The
  fetcher re-requests the redirect target with the page number and logs the
  move; a page that only repeats earlier products fails the category.
- `hasDiscount` can be `false` while `previousPrice` is set (e.g. 4929.99 vs
  5309.99). Trust `previousPrice` only. Prices can have cents (`.99`).
- Category slugs (leaf):
  - smartphones `mobiluri-telefoni-c377t`
  - laptops `noutbuqebi-c58t` (151)
  - TVs `televizori-c73t` (253)
  - headphones `headphones-c134t`, `buds-c127t`, `earphones-c131t`, `yursasmenebi-c15t`
  - large appliances `macivari-c300t`, `sareckhi-manqana-c77t`, … (rest from `sitemap/category/domestic-appliances-c16.xml`)

## Implications for the build

1. One `parse_products(json) -> list[Item]` shared by all stores; three thin fetchers.
2. Dedupe by `(store, id)` within a run — parent categories overlap children.
3. Scheduling: Zoommer + ee are httpx-only → fine for GitHub Actions *if* their
   Cloudflare doesn't challenge datacenter IPs (untested). Alta needs Playwright
   and is the most likely to fail from GH runners → plan on the local runner for alta.
4. Fixtures saved: `tests/fixtures/zoommer/`, `tests/fixtures/ee/`. Alta fixture
   still to capture (needs Playwright).

## Decisions

- 2026-11-02: Zoommer stock → "buyable" proxy (see zoommer.ge section).
  **Superseded the same day:** Zoommer `in_stock` is NULL (unknown); `buyable`
  is its own column for every store and part of change detection. Raw
  discount flags are stored in price_changes.
- 2026-11-02: ee headphones. `yursasmenebi-c15t` is the Headphones *section*
  (0 products of its own, confirmed via ee's API: CategoryId 15 → 0). ee's
  mega-menu lists its product leaves as c127t buds (176), c134t headphones
  (184), c131t earphones (98) — all configured — plus c126t accessories.
- 2026-11-02: Zoommer pagination. The API has no sort by id (params:
  NameAsc, PriceAsc, NotInStock, …). Default, NameAsc and PriceAsc full passes
  all returned 448/448 unique ids; the earlier 449/450 didn't reproduce
  (likely a catalogue change mid-run). Using NameAsc=true; a run logs a
  warning when unique ids ≠ productsCount.
- 2026-11-02: Large appliances → Alta + ee only. Zoommer doesn't sell them;
  no veli.store.
- 2026-11-02: Alta dropped for Phase 1 (API blocked for automation, see above).
  Phase 1 = Zoommer + ee; large appliances = ee only. The Alta fetcher and
  category IDs stay in the code, opt-in via `--store alta`.
