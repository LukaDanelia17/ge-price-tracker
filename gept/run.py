"""Collect one snapshot from the stores and save what changed.

    python -m gept.run                          # default stores, needs DATABASE_URL
    python -m gept.run --store ee --dry-run     # fetch + parse only, print counts
    python -m gept.run --store zoommer --dry-run --dump /tmp/zoommer-pages
"""

import argparse
import json
import logging
import os
import sys
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from . import fetch
from .config import CATEGORIES, DEFAULT_STORES
from .parse import Item, parse_products

log = logging.getLogger("gept")


def collect(store: str, dump: Path | None = None) -> tuple[dict[str, Item], list[str]]:
    """Fetch every configured category of one store. Returns items keyed by
    store_product_id and a list of failed categories."""
    items: dict[str, Item] = {}
    failures: list[str] = []
    throttle = fetch.new_throttle(store)

    with ExitStack() as stack:
        if store == "alta":
            browser = stack.enter_context(fetch.AltaBrowser())
            pages_for = lambda ref: fetch.alta(browser, throttle, ref)  # noqa: E731
        else:
            client = stack.enter_context(fetch.http_client())
            fetcher = fetch.zoommer if store == "zoommer" else fetch.ee
            pages_for = lambda ref: fetcher(client, throttle, ref)  # noqa: E731

        for category, refs in CATEGORIES[store].items():
            for ref in refs:
                seen: set[str] = set()
                reported = None
                try:
                    for n, page in enumerate(pages_for(ref), start=1):
                        if dump:
                            dump.mkdir(parents=True, exist_ok=True)
                            (dump / f"{ref}_page{n}.json").write_text(json.dumps(page, ensure_ascii=False))
                        reported = page.get("productsCount")
                        parsed = parse_products(store, category, page.get("products") or [])
                        page_ids = {i.store_product_id for i in parsed}
                        if n > 1 and page_ids and page_ids <= seen:
                            raise RuntimeError(f"page {n} only repeats earlier products; pagination is broken")
                        for item in parsed:
                            items.setdefault(item.store_product_id, item)
                        seen |= page_ids
                except Exception:
                    log.exception("%s %s %s failed after %d items", store, category, ref, len(seen))
                    failures.append(f"{store}/{category}/{ref}")
                    continue
                log.info("%s %s %s: %d items", store, category, ref, len(seen))
                if not seen:
                    # A configured category that lists nothing means a moved
                    # category or a broken parser, never a normal run.
                    log.error("%s %s %s: 0 items", store, category, ref)
                    failures.append(f"{store}/{category}/{ref}")
                elif reported is not None and len(seen) != reported:
                    log.warning(
                        "%s %s %s: %d unique items but productsCount=%d (catalogue changed mid-run?)",
                        store, category, ref, len(seen), reported,
                    )
    return items, failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--store", action="append", choices=list(CATEGORIES), help=f"repeatable; default {', '.join(DEFAULT_STORES)}"
    )
    ap.add_argument("--dry-run", action="store_true", help="don't touch the database")
    ap.add_argument("--dump", type=Path, help="write raw listing pages here (for fixtures)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    url = None
    if not args.dry_run:
        from . import db

        url = os.environ.get("DATABASE_URL")
        if not url:
            ap.error("DATABASE_URL is not set (or use --dry-run)")
        with db.connect(url) as conn:
            db.ensure_schema(conn)

    observed_at = datetime.now(timezone.utc)
    all_failures: list[str] = []
    for store in args.store or DEFAULT_STORES:
        items, failures = collect(store, args.dump)
        all_failures += failures
        values = items.values()
        log.info(
            "%s: %d unique items, in_stock true/false/unknown %d/%d/%d, %d buyable, %d with old_price",
            store, len(items),
            sum(i.in_stock is True for i in values), sum(i.in_stock is False for i in values),
            sum(i.in_stock is None for i in values),
            sum(i.buyable for i in values), sum(i.old_price is not None for i in values),
        )
        if url is not None:
            # Connect per save: a connection left idle through a 10+ minute
            # crawl doesn't survive network blips or pooler idle timeouts.
            try:
                with db.connect(url) as conn:
                    added = db.save(conn, store, list(items.values()), observed_at)
                log.info("%s: %d price_changes rows added", store, added)
            except Exception:
                log.exception("%s: saving failed", store)
                all_failures.append(f"{store}/save")

    if all_failures:
        log.error("failed categories: %s", ", ".join(all_failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
