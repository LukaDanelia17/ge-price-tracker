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
                seen = 0
                try:
                    for n, page in enumerate(pages_for(ref), start=1):
                        if dump:
                            dump.mkdir(parents=True, exist_ok=True)
                            (dump / f"{ref}_page{n}.json").write_text(json.dumps(page, ensure_ascii=False))
                        for item in parse_products(store, category, page.get("products") or []):
                            items.setdefault(item.store_product_id, item)
                            seen += 1
                    log.info("%s %s %s: %d items", store, category, ref, seen)
                except Exception:
                    log.exception("%s %s %s failed after %d items", store, category, ref, seen)
                    failures.append(f"{store}/{category}/{ref}")
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

    conn = None
    if not args.dry_run:
        from . import db

        url = os.environ.get("DATABASE_URL")
        if not url:
            ap.error("DATABASE_URL is not set (or use --dry-run)")
        conn = db.connect(url)
        db.ensure_schema(conn)

    observed_at = datetime.now(timezone.utc)
    all_failures: list[str] = []
    for store in args.store or DEFAULT_STORES:
        items, failures = collect(store, args.dump)
        all_failures += failures
        in_stock = sum(i.in_stock for i in items.values())
        with_old = sum(i.old_price is not None for i in items.values())
        log.info("%s: %d unique items, %d in stock, %d with old_price", store, len(items), in_stock, with_old)
        if conn is not None:
            added = db.save(conn, store, list(items.values()), observed_at)
            log.info("%s: %d price_changes rows added", store, added)

    if conn is not None:
        conn.close()
    if all_failures:
        log.error("failed categories: %s", ", ".join(all_failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
