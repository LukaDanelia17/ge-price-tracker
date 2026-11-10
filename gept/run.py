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

from . import fetch, telegram
from .config import CATEGORIES, COUNTS_FILE, DEFAULT_STORES, LAST_SUCCESS_FILE
from .parse import Item, parse_gamige, parse_kontakt, parse_products
from .quality import count_issues

log = logging.getLogger("gept")


def collect(
    store: str,
    dump: Path | None = None,
    counts: dict[str, int] | None = None,
    known_urls: dict[str, str] | None = None,
) -> tuple[dict[str, Item], list[str]]:
    """Fetch every configured category of one store. Returns items keyed by
    store_product_id and a list of failed categories; fills `counts` with
    items per finished category ("store/category/ref"). `known_urls` (alta only)
    lets the gamige fetcher skip the per-product link lookup we already have."""
    items: dict[str, Item] = {}
    failures: list[str] = []
    throttle = fetch.new_throttle(store)
    fetcher = {"zoommer": fetch.zoommer, "ee": fetch.ee, "alta_gamige": fetch.gamige_alta, "kontakt": fetch.kontakt}[store]
    parse = {"alta_gamige": parse_gamige, "kontakt": parse_kontakt}.get(store, parse_products)

    with ExitStack() as stack:
        client = stack.enter_context(fetch.http_client())
        if store == "alta_gamige":
            pages_for = lambda ref: fetcher(client, throttle, ref, known_urls or {})  # noqa: E731
        else:
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
                        parsed = parse(store, category, page.get("products") or [])
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
                    continue
                if reported is not None and len(seen) != reported:
                    log.warning(
                        "%s %s %s: %d unique items but productsCount=%d (catalogue changed mid-run?)",
                        store, category, ref, len(seen), reported,
                    )
                if counts is not None:
                    counts[f"{store}/{category}/{ref}"] = len(seen)
    return items, failures


def load_counts() -> dict[str, int]:
    try:
        return json.loads(COUNTS_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def save_counts(counts: dict[str, int]) -> None:
    COUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    COUNTS_FILE.write_text(json.dumps(counts, indent=1, sort_keys=True))


def report(issues: list[str], totals: dict[str, tuple[int, int]]) -> None:
    """One Telegram message per run: the issues, or a short summary that also
    shows the scheduled runs are still happening (TELEGRAM_SUMMARY=0 to skip)."""
    summary = ", ".join(f"{s} {n} products / {added} changes" for s, (n, added) in totals.items())
    if issues:
        telegram.send(f"⚠️ ge-price-tracker: {len(issues)} issue(s)\n" + "\n".join(issues) + f"\n\n{summary}")
    elif os.environ.get("TELEGRAM_SUMMARY", "1") != "0":
        telegram.send(f"✅ ge-price-tracker: {summary}")


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
    counts: dict[str, int] = {}
    price_flags: list[str] = []
    totals: dict[str, tuple[int, int]] = {}
    for store in args.store or DEFAULT_STORES:
        known_urls = None
        if store == "alta_gamige" and url is not None:
            with db.connect(url) as conn:
                rows = conn.execute(
                    "select store_product_id, url from products where store = %s", (store,)
                ).fetchall()
            known_urls = {pid: u for pid, u in rows if u.startswith("https://alta.ge/")}
            log.info("alta_gamige: %d known alta links; resolving links for the rest", len(known_urls))
        items, failures = collect(store, args.dump, counts, known_urls)
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
                    saved = db.save(conn, store, list(items.values()), observed_at)
                log.info("%s: %d price_changes rows added", store, saved.added)
                price_flags += saved.price_issues
                totals[store] = (len(items), saved.added)
            except Exception:
                log.exception("%s: saving failed", store)
                all_failures.append(f"{store}/save")

    if url is not None:
        previous = load_counts()
        issues = [f"failed: {f}" for f in all_failures] + count_issues(previous, counts) + price_flags
        for line in issues:
            log.warning("quality: %s", line)
        save_counts(previous | counts)
        report(issues, totals)
        if totals:  # at least one store saved: this run counts for the schedule
            LAST_SUCCESS_FILE.write_text(datetime.now(timezone.utc).isoformat())

    if all_failures:
        log.error("failed categories: %s", ", ".join(all_failures))
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        message = f"{type(e).__name__}: {e}"
        if os.environ.get("DATABASE_URL"):
            message = message.replace(os.environ["DATABASE_URL"], "<DATABASE_URL>")
        telegram.send(f"❌ ge-price-tracker crashed: {message[:500]}")
        raise
