"""Fetch category listing pages. Each fetcher yields the raw listing envelope
({products, productsCount, ...}) page by page; parsing lives in parse.py.
"""

import logging
import time
from collections.abc import Callable, Iterator

import httpx

from .config import DELAY, MAX_PAGES, USER_AGENT
from .parse import kontakt_listing, listing_from_html

log = logging.getLogger(__name__)


class Throttle:
    """Keep at least `delay` seconds between consecutive requests."""

    def __init__(self, delay: float):
        self.delay = delay
        self._last = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last = time.monotonic()


# Seconds to wait before each retry of a failed request (connection errors,
# timeouts, 5xx). 4xx is not retried. A Mac waking from sleep can take a couple
# of minutes to get its network back, hence the longer tail.
RETRY_WAITS = (10, 30, 60, 120)

# A whole response must arrive within this many seconds. httpx's timeouts are
# per read, so a server that trickles bytes never trips them; on 2026-11-02
# ee held single requests open for 15-80 minutes.
REQUEST_DEADLINE = 90


def get_bounded(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    """GET that raises httpx.ReadTimeout once REQUEST_DEADLINE has passed."""
    started = time.monotonic()
    with client.stream("GET", url, **kwargs) as r:
        body = bytearray()
        for chunk in r.iter_bytes():
            body += chunk
            if time.monotonic() - started > REQUEST_DEADLINE:
                raise httpx.ReadTimeout(f"response took over {REQUEST_DEADLINE}s", request=r.request)
        # iter_bytes() already undid any content-encoding.
        headers = [(k, v) for k, v in r.headers.items() if k not in ("content-encoding", "content-length")]
        return httpx.Response(r.status_code, headers=headers, content=bytes(body), request=r.request, history=r.history)


def get_with_retry(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    for wait in (*RETRY_WAITS, None):
        try:
            r = get_bounded(client, url, **kwargs)
            if r.status_code < 500 or wait is None:
                r.raise_for_status()
                return r
            log.warning("%s: HTTP %d, retrying in %ds", url, r.status_code, wait)
        except httpx.TransportError as e:
            if wait is None:
                raise
            log.warning("%s: %s, retrying in %ds", url, e, wait)
        time.sleep(wait)
    raise AssertionError("unreachable")


def paginate(fetch_page: Callable[[int], dict], page_size: int) -> Iterator[dict]:
    for page in range(1, MAX_PAGES + 1):
        data = fetch_page(page)
        yield data
        total = data.get("productsCount") or 0
        if not data.get("products") or page * page_size >= total:
            return
    log.warning("stopped at MAX_PAGES=%d", MAX_PAGES)


def http_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "ka"},
        timeout=30,
        follow_redirects=True,
    )


def zoommer(client: httpx.Client, throttle: Throttle, category_id: int) -> Iterator[dict]:
    def fetch_page(page: int) -> dict:
        throttle.wait()
        # The API has no sort by id; NameAsc (the site's own "by name" sort) is
        # steadier across a run than the default merchandising order.
        r = get_with_retry(
            client,
            "https://zoommer.ge/api/proxy/v1/Products/v3",
            params={"CategoryId": category_id, "Page": page, "Limit": 28, "NameAsc": "true"},
        )
        return r.json()

    return paginate(fetch_page, 28)


def ee(client: httpx.Client, throttle: Throttle, slug: str) -> Iterator[dict]:
    # The JSON API needs a session token; the server-rendered HTML carries the same data.
    def fetch_page(page: int) -> dict:
        throttle.wait()
        r = get_with_retry(client, f"https://ee.ge/{slug}", params={"page": page})
        if r.history and r.url.params.get("page") != str(page):
            # ee renames slugs, and the redirect to the new one drops ?page=,
            # which would serve page 1 again. Request the new URL with the page.
            if page == 1:
                log.warning("ee: %s now redirects to %s; update config", slug, r.url.path)
            throttle.wait()
            r = get_with_retry(client, f"https://ee.ge{r.url.path}", params={"page": page})
        return listing_from_html(r.text)

    return paginate(fetch_page, 16)


# gamige.com returns 20 results per search page.
GAMIGE_PAGE_SIZE = 20


def _alta_url(client: httpx.Client, throttle: Throttle, product_id: str) -> str | None:
    """alta.ge's own product link from gamige's per-product detail endpoint.

    The search list has no per-store URL; only /api/product/{id} carries
    stores[].url. One extra call, so we only make it for products whose link
    we don't already have (see gamige_alta's known_urls).
    """
    throttle.wait()
    try:
        r = get_with_retry(client, f"https://gamige.com/api/product/{product_id}")
        for s in r.json().get("stores") or []:
            if (s.get("store") or "").lower() == "alta":
                return s.get("url")
    except httpx.HTTPError:
        log.warning("gamige: detail fetch failed for %s", product_id)
    return None


def gamige_alta(
    client: httpx.Client, throttle: Throttle, category: str, known_urls: dict[str, str] | None = None
) -> Iterator[dict]:
    """alta.ge prices via gamige.com's open comparison API (no auth, no challenge).

    alta.ge blocks automated access behind Cloudflare, so we don't fetch it
    directly; gamige is an open third-party aggregator that publishes alta's
    prices. `category` is gamige's own Georgian category name. Each result is
    enriched with "altaUrl" (alta's own link): reused from known_urls when we
    already have it, otherwise fetched once from the detail endpoint. Yields the
    same {products, productsCount} envelope the other fetchers do.
    """
    known_urls = known_urls or {}

    def fetch_page(page: int) -> dict:
        throttle.wait()
        r = get_with_retry(
            client,
            "https://gamige.com/api/search",
            params={"storeType": "electronics", "store": "Alta", "category": category, "page": page},
        )
        data = r.json()
        results = data.get("results") or []
        for item in results:
            pid = str(item["id"])
            item["altaUrl"] = known_urls.get(pid) or _alta_url(client, throttle, pid)
        return {"products": results, "productsCount": data.get("total") or 0}

    return paginate(fetch_page, GAMIGE_PAGE_SIZE)


def kontakt(client: httpx.Client, throttle: Throttle, path: str) -> Iterator[dict]:
    """kontakt.ge category pages, page by page via ?p=N until there's no "next".

    Server-rendered HTML; kontakt's GraphQL API is Cloudflare-blocked for
    automation, so we don't use it. Default sort only: robots.txt disallows the
    sort parameters. The page count isn't shown, so productsCount is None.
    """
    url = f"https://kontakt.ge/{path}"
    for page in range(1, MAX_PAGES + 1):
        throttle.wait()
        r = get_with_retry(client, url, params={"p": page} if page > 1 else None)
        listing = kontakt_listing(r.text)
        yield {"products": listing["products"], "productsCount": None}
        if not listing["next_url"] or not listing["products"]:
            return
    log.warning("stopped at MAX_PAGES=%d", MAX_PAGES)


def new_throttle(store: str) -> Throttle:
    return Throttle(DELAY[store])
