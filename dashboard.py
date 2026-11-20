"""Build dashboard/index.html (every product) and dashboard/report.html (the
Black Friday report: per-store figures and the clearest examples) from what
the crawler has collected. Rebuilt by scripts/crawl.sh after every run.

    .venv/bin/python dashboard.py            # then: open dashboard/index.html

A discount is judged the way the EU's Omnibus rule does it: against the lowest
price the product sold at in the SALE_LOOKBACK before the sale started.
  fake         the sale price isn't below that, or the store raised the
               crossed-out price mid-sale without lowering the price
  exaggerated  cheaper, but EXAGGERATION_PP+ points less than the store claims
  real         cheaper by roughly what the store claims
  unconfirmed  on sale since we first saw it, so we never saw the pre-sale price
  none         no crossed-out price above the current one
"""

import json
import os
from bisect import bisect_left
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

import psycopg

from export import read_env_file

HERE = Path(__file__).resolve().parent
OUT = HERE / "dashboard" / "index.html"
REPORT_OUT = HERE / "dashboard" / "report.html"
SALE_LOOKBACK = timedelta(days=30)
EXAGGERATION_PP = 10
CHANGE_DAYS = 7
# Runs are 12 h apart; a longer stretch without one means we weren't looking.
GAP = timedelta(hours=16)
# An unconfirmed sale this many days long with no change at any check is
# flagged as "perpetual" (the page says "at least N days": we can't see earlier).
PERM_DAYS = 14
DAY_MS = 86_400_000
REPORT_TOP = {"fake": 12, "exaggerated": 6, "perm": 6, "real": 6}


def ms(ts: datetime) -> int:
    return int(ts.timestamp() * 1000)


def num(d: Decimal | None) -> float | None:
    return None if d is None else float(d)


def pct_off(before: Decimal, now: Decimal) -> float:
    """How much cheaper `now` is than `before`, in % (negative = pricier)."""
    return round(float((before - now) / before * 100), 1)


def on_sale(price: Decimal | None, old: Decimal | None) -> bool:
    return price is not None and old is not None and old > price


class Assessment(NamedTuple):
    verdict: str
    price: Decimal | None
    old: Decimal | None
    claimed: float | None  # discount the store shows, %
    actual: float | None  # cheaper than our reference by this %, negative = pricier
    ref: Decimal | None  # lowest price in SALE_LOOKBACK before the sale
    since: datetime | None  # when the current sale started, as far as we saw
    reason: str | None  # why "fake": "no_drop", "resumed" or "old_raised"
    old_from: Decimal | None  # crossed-out price before the store raised it
    max_seen: Decimal | None
    min_seen: Decimal | None
    chg7: float | None  # price change vs. CHANGE_DAYS ago, %
    low: bool  # current price is the lowest we've seen, and it has varied
    steady: bool = False  # on sale with the same price and crossed-out price at every check since `since`


def assess(rows: list[tuple], now: datetime) -> Assessment:
    """rows: one product's (observed_at, price, old_price) change points, oldest first."""
    _, price, old = rows[-1]
    prices = [p for _, p, _ in rows if p is not None]
    max_seen, min_seen = max(prices, default=None), min(prices, default=None)
    low = price is not None and len(set(prices)) > 1 and price == min_seen

    then = rows[0][1]
    for t, p, _ in rows:
        if t > now - timedelta(days=CHANGE_DAYS):
            break
        then = p
    chg7 = round(float((price - then) / then * 100), 1) if price is not None and then else None
    seen = {"price": price, "old": old, "max_seen": max_seen, "min_seen": min_seen, "chg7": chg7, "low": low}

    if not on_sale(price, old):
        return Assessment("none", claimed=None, actual=None, ref=None, since=None, reason=None, old_from=None, **seen)

    claimed = pct_off(old, price)
    start = len(rows) - 1
    while start > 0 and on_sale(rows[start - 1][1], rows[start - 1][2]):
        start -= 1
    since = rows[start][0]
    sale = rows[start:]

    old_from = None
    for (_, p0, o0), (_, p1, o1) in zip(sale, sale[1:]):
        if o1 > o0 and p1 >= p0 and old_from is None:
            old_from = o0

    # Prices in effect at any point in the lookback window before the sale.
    window = since - SALE_LOOKBACK
    before = [(rows[i][1], on_sale(rows[i][1], rows[i][2]))
              for i in range(start) if rows[i][1] and rows[i + 1][0] > window]
    reason = None
    if before:
        ref = min(p for p, _ in before)
        actual = pct_off(ref, price)
        if price >= ref:
            # "resumed": that low price was itself an earlier "sale", paused and restarted.
            verdict = "fake"
            reason = "resumed" if any(was_sale for p, was_sale in before if p == ref) else "no_drop"
        elif claimed - actual >= EXAGGERATION_PP:
            verdict = "exaggerated"
        else:
            verdict = "real"
    else:
        ref = None
        top = max(p for _, p, _ in sale)
        actual = pct_off(top, price) if top > 0 else None
        verdict = "unconfirmed"
    if old_from is not None and verdict != "fake":
        verdict, reason = "fake", "old_raised"
    # A "sale" that never moves: same price, same crossed-out price at every check.
    steady = len({(p, o) for _, p, o in sale}) == 1
    return Assessment(verdict, claimed=claimed, actual=actual, ref=ref, since=since, reason=reason,
                      old_from=old_from, steady=steady, **seen)


def build(products: list[tuple], changes: list[tuple], now: datetime, runs: list[tuple] = (),
          images: dict[int, str] | None = None) -> dict:
    """products: (id, store, name, url, category, first_seen, last_seen);
    changes: (product_id, observed_at, price, old_price), ordered by product, time;
    runs: (store, observed_at) of saved runs (older runs are recovered from changes);
    images: product id -> photo URL, for the products that have one."""
    series: dict[int, list] = {}
    for pid, t, price, old in changes:
        series.setdefault(pid, []).append((t, price, old))

    store_of = {pid: store for pid, store, *_rest in products}
    run_sets: dict[str, set] = {}
    for store, t in runs:
        run_sets.setdefault(store, set()).add(t)
    for pid, t, _price, _old in changes:
        if pid in store_of:
            run_sets.setdefault(store_of[pid], set()).add(t)
    run_times = {s: sorted(ts) for s, ts in run_sets.items()}
    gaps = {s: [[ms(a), ms(b)] for a, b in zip(ts, ts[1:]) if b - a > GAP] for s, ts in run_times.items()}

    last_run = {}
    for _, store, *_rest, last_seen in products:
        last_run[store] = max(last_run.get(store, last_seen), last_seen)

    stores = {
        s: {"store": s, "products": 0, "on_sale": 0, "fake": 0, "exaggerated": 0, "real": 0, "unconfirmed": 0,
            "last_run": ms(t)}
        for s, t in sorted(last_run.items())
    }
    catalog = []
    # Listed = seen in the latest run that covered the product's category. Per
    # category, so a category that failed in the latest run doesn't make all of
    # its products look delisted.
    category_run = {}
    for _, store, _name, _url, category, _first_seen, last_seen in products:
        key = (store, category)
        category_run[key] = max(category_run.get(key, last_seen), last_seen)

    for pid, store, _name, _url, category, _first_seen, last_seen in products:
        rows = series.get(pid)
        if not rows or last_seen < category_run[(store, category)]:
            continue  # gone from its category in the latest run that covered it
        st = stores[store]
        st["products"] += 1
        a = assess(rows, now)
        if a.verdict != "none":
            st["on_sale"] += 1
            st[a.verdict] += 1
        # If the sale was first seen right after a gap, it started somewhere in that gap.
        since_from = None
        if a.since:
            ts = run_times.get(store, [])
            i = bisect_left(ts, a.since)
            if i > 0 and a.since - ts[i - 1] > GAP:
                since_from = ts[i - 1]
        catalog.append([
            pid, a.verdict, num(a.price), num(a.old), a.claimed, a.actual, num(a.ref),
            ms(a.since) if a.since else None, a.reason, num(a.old_from), num(a.max_seen), num(a.min_seen),
            a.chg7, int(a.low), ms(since_from) if since_from else None, int(a.steady),
        ])

    return {
        "generated": ms(now),
        "stores": list(stores.values()),
        "catalog": catalog,
        "gaps": gaps,
        "products": [[pid, s, n, u, c, ms(f), ms(lst)] for pid, s, n, u, c, f, lst in products if pid in series],
        "series": {pid: [[ms(t), num(p), num(o)] for t, p, o in rows] for pid, rows in series.items()},
        "images": {row[0]: images[row[0]] for row in catalog if row[0] in (images or {})},
        "perm_days": PERM_DAYS,
    }


def report_data(data: dict) -> dict:
    """The Black Friday report from build()'s output: per-store counts and the
    clearest examples of each finding, including real deals for balance."""
    prod = {p[0]: p for p in data["products"]}
    images = data.get("images", {})
    items = []
    for (pid, v, price, old, claimed, actual, ref, since, reason, old_from, _max, _min, _chg7, _low, _since_from,
         steady) in data["catalog"]:
        _, store, name, url, category, first, last = prod[pid]
        days = (last - since) // DAY_MS if since else None
        items.append({
            "id": pid, "v": v, "store": store, "name": name, "url": url, "cat": category, "img": images.get(pid),
            "price": price, "old": old, "claimed": claimed, "actual": actual, "ref": ref, "reason": reason,
            "old_from": old_from, "days": days,
            "perm": v == "unconfirmed" and bool(steady) and days is not None and days >= PERM_DAYS,
        })

    stores: dict[str, Counter] = {}
    watched_since: dict[str, int] = {}  # first observation per store: "perpetual" needs PERM_DAYS of history
    for pid, store, *_rest, first, _last in data["products"]:
        watched_since[store] = min(watched_since.get(store, first), first)
    for it in items:
        c = stores.setdefault(it["store"], Counter())
        c["products"] += 1
        c[it["v"]] += 1
        c["on_sale"] += it["v"] != "none"
        c["perm"] += it["perm"]

    def top(kind, test, key):
        return sorted((i for i in items if test(i)), key=key)[:REPORT_TOP[kind]]

    return {
        "generated": data["generated"],
        "since": min((p[5] for p in data["products"]), default=data["generated"]),
        "perm_days": PERM_DAYS,
        "stores": [{"store": s, "since": watched_since[s], **c} for s, c in sorted(stores.items())],
        # boldest claim with no real drop first; then the widest claim/reality gap; the longest
        # unchanging "sales"; the deepest real drops
        "fake": top("fake", lambda i: i["v"] == "fake", lambda i: (-(i["claimed"] or 0), i["price"] or 0)),
        "exaggerated": top("exaggerated", lambda i: i["v"] == "exaggerated",
                           lambda i: -((i["claimed"] or 0) - (i["actual"] or 0))),
        "perm": top("perm", lambda i: i["perm"], lambda i: (-i["days"], -(i["claimed"] or 0))),
        "real": top("real", lambda i: i["v"] == "real", lambda i: -(i["actual"] or 0)),
    }


def render(data: dict, template: str | None = None) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (template or TEMPLATE).replace("__DATA__", payload)


def main() -> None:
    url = os.environ.get("DATABASE_URL") or read_env_file(HERE / ".env").get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set and not found in .env")
    with psycopg.connect(url) as conn:
        products = conn.execute(
            "select id, store, name, url, category, first_seen, last_seen from products order by id"
        ).fetchall()
        changes = conn.execute(
            "select product_id, observed_at, price, old_price from price_changes order by product_id, observed_at, id"
        ).fetchall()
        has_runs = conn.execute("select to_regclass('public.runs') is not null").fetchone()[0]
        runs = conn.execute("select store, observed_at from runs").fetchall() if has_runs else []
        has_images = conn.execute(
            "select exists (select 1 from information_schema.columns"
            " where table_name = 'products' and column_name = 'image_url')"
        ).fetchone()[0]
        images = dict(conn.execute(
            "select id, image_url from products where image_url is not null"
        ).fetchall()) if has_images else {}
    data = build(products, changes, datetime.now(timezone.utc), runs, images)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(data), encoding="utf-8")
    REPORT_OUT.write_text(render(report_data(data), REPORT_TEMPLATE), encoding="utf-8")
    n = Counter(row[1] for row in data["catalog"])
    print(f"{OUT}: {len(data['catalog'])} listed products, {n['fake']} fake, {n['exaggerated']} exaggerated, "
          f"{n['real']} real, {n['unconfirmed']} unconfirmed discounts")


# The page itself lives next to this file; render() fills in __DATA__.
TEMPLATE = (HERE / "dashboard_template.html").read_text(encoding="utf-8")
REPORT_TEMPLATE = (HERE / "report_template.html").read_text(encoding="utf-8")


if __name__ == "__main__":
    main()
