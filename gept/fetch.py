"""Fetch category listing pages. Each fetcher yields the raw listing envelope
({products, productsCount, ...}) page by page; parsing lives in parse.py.
"""

import logging
import time
from collections.abc import Callable, Iterator
from urllib.parse import urlencode

import httpx

from .config import DELAY, MAX_PAGES, USER_AGENT
from .parse import listing_from_html

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
# timeouts, 5xx). 4xx is not retried.
RETRY_WAITS = (10, 30)


def get_with_retry(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    for wait in (*RETRY_WAITS, None):
        try:
            r = client.get(url, **kwargs)
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


class AltaBrowser:
    """alta.ge answers every non-browser request with a Cloudflare challenge, so
    open the site once in Chromium and call its listing API from inside the page.
    """

    def __init__(self, headless: bool = True):
        self.headless = headless

    def __enter__(self) -> "AltaBrowser":
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        # Keep Chromium's own UA (honest) and append ours so the store can identify us.
        probe = self._browser.new_page()
        ua = f"{probe.evaluate('navigator.userAgent')} {USER_AGENT}"
        probe.close()
        self.page = self._browser.new_context(user_agent=ua, locale="ka-GE").new_page()
        self.page.goto("https://alta.ge/", wait_until="domcontentloaded")
        self.page.wait_for_function("document.title !== 'Just a moment...'", timeout=60_000)
        return self

    def __exit__(self, *exc) -> None:
        self._browser.close()
        self._pw.stop()

    def get_json(self, path: str, params: dict) -> dict:
        return self.page.evaluate(
            """async (url) => {
                const r = await fetch(url, {headers: {accept: 'application/json'}});
                if (!r.ok) throw new Error('HTTP ' + r.status + ' for ' + url);
                return await r.json();
            }""",
            f"{path}?{urlencode(params)}",
        )


def alta(browser: AltaBrowser, throttle: Throttle, category_id: int) -> Iterator[dict]:
    def fetch_page(page: int) -> dict:
        throttle.wait()
        # Same endpoint and parameter order the site's "see more" button uses;
        # Limit above 16 is rejected by the WAF.
        return browser.get_json(
            "/api/proxy/v1/Products/v4",
            {"CategoryId": category_id, "Limit": 16, "Page": page},
        )

    return paginate(fetch_page, 16)


def new_throttle(store: str) -> Throttle:
    return Throttle(DELAY[store])
