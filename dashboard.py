"""Build dashboard/index.html: a private, self-contained page with what the
crawler has collected. Rebuilt by scripts/crawl.sh after every run.

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
SALE_LOOKBACK = timedelta(days=30)
EXAGGERATION_PP = 10
CHANGE_DAYS = 7
# Runs are 12 h apart; a longer stretch without one means we weren't looking.
GAP = timedelta(hours=16)


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
    return Assessment(verdict, claimed=claimed, actual=actual, ref=ref, since=since, reason=reason,
                      old_from=old_from, **seen)


def build(products: list[tuple], changes: list[tuple], now: datetime, runs: list[tuple] = ()) -> dict:
    """products: (id, store, name, url, category, first_seen, last_seen);
    changes: (product_id, observed_at, price, old_price), ordered by product, time;
    runs: (store, observed_at) of saved runs (older runs are recovered from changes)."""
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
    for pid, store, _name, _url, _category, _first_seen, last_seen in products:
        rows = series.get(pid)
        if not rows or last_seen < last_run[store]:
            continue  # not listed in the store's latest run
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
            a.chg7, int(a.low), ms(since_from) if since_from else None,
        ])

    return {
        "generated": ms(now),
        "stores": list(stores.values()),
        "catalog": catalog,
        "gaps": gaps,
        "products": [[pid, s, n, u, c, ms(f), ms(lst)] for pid, s, n, u, c, f, lst in products if pid in series],
        "series": {pid: [[ms(t), num(p), num(o)] for t, p, o in rows] for pid, rows in series.items()},
    }


def render(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return TEMPLATE.replace("__DATA__", payload)


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
    data = build(products, changes, datetime.now(timezone.utc), runs)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(data), encoding="utf-8")
    n = Counter(row[1] for row in data["catalog"])
    print(f"{OUT}: {len(data['catalog'])} listed products, {n['fake']} fake, {n['exaggerated']} exaggerated, "
          f"{n['real']} real, {n['unconfirmed']} unconfirmed discounts")


TEMPLATE = r"""<!doctype html>
<html lang="ka">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ფასების მონიტორი</title>
<style>
:root {
  color-scheme: light;
  --surface-0: #f4f4f2; --surface-1: #fcfcfb; --border: #e3e2de;
  --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #77766f;
  --grid: #ebeae6; --series-1: #2a78d6; --series-2: #eb6834; --ref: #8a8984;
  --up: #b3261e; --down: #146c2e;
  --bad-bg: #fdecea; --bad: #a8201a; --warn-bg: #fff3dc; --warn: #8a5300;
  --good-bg: #e6f4ea; --good: #146c2e; --early-bg: #efeeea; --early: #52514e; --sel: #eaf2fd;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface-0: #121211; --surface-1: #1a1a19; --border: #2e2e2c;
    --text-primary: #f0efec; --text-secondary: #c3c2b7; --text-muted: #94938b;
    --grid: #2a2a28; --series-1: #3987e5; --series-2: #d95926; --ref: #8f8e86;
    --up: #f2867f; --down: #7bd88f;
    --bad-bg: #3b1a17; --bad: #f2867f; --warn-bg: #3a2c12; --warn: #f0b860;
    --good-bg: #15301e; --good: #7bd88f; --early-bg: #2a2a28; --early: #c3c2b7; --sel: #1c2836;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-0: #121211; --surface-1: #1a1a19; --border: #2e2e2c;
  --text-primary: #f0efec; --text-secondary: #c3c2b7; --text-muted: #94938b;
  --grid: #2a2a28; --series-1: #3987e5; --series-2: #d95926; --ref: #8f8e86;
  --up: #f2867f; --down: #7bd88f;
  --bad-bg: #3b1a17; --bad: #f2867f; --warn-bg: #3a2c12; --warn: #f0b860;
  --good-bg: #15301e; --good: #7bd88f; --early-bg: #2a2a28; --early: #c3c2b7; --sel: #1c2836;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface-0); color: var(--text-primary);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans Georgian", sans-serif; }
main { max-width: 1120px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 26px; margin: 0 0 6px; }
h2 { font-size: 18px; margin: 36px 0 10px; }
.lead { color: var(--text-secondary); margin: 0 0 4px; max-width: 760px; }
.meta { color: var(--text-muted); font-size: 13px; margin: 0; }
.note { color: var(--text-secondary); font-size: 13px; margin: -4px 0 12px; }
.card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
a { color: var(--series-1); }

#focus { margin-top: 20px; }
.focus-head { display: flex; gap: 12px; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; }
#f-name { margin: 0; font-size: 19px; }
.btn { display: inline-block; padding: 7px 12px; border: 1px solid var(--border); border-radius: 8px;
  text-decoration: none; white-space: nowrap; font-size: 14px; background: var(--surface-0);
  color: var(--text-primary); font-family: inherit; cursor: pointer; }
a.btn { color: var(--series-1); }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin: 16px 0 12px; }
.facts > div { background: var(--surface-0); border-radius: 8px; padding: 10px 12px; }
.label { display: block; color: var(--text-secondary); font-size: 13px; }
.big { display: block; font-size: 21px; font-weight: 700; font-variant-numeric: tabular-nums; }
.verdict { margin: 0 0 14px; padding: 10px 12px; border-radius: 8px; background: var(--surface-0); }
.verdict.v-bad { background: var(--bad-bg); }
.verdict.v-warn { background: var(--warn-bg); }
.verdict.v-good { background: var(--good-bg); }
.verdict .badge { margin-right: 8px; background: transparent; padding: 0; }
.badge { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 13px; font-weight: 600; white-space: nowrap; }
.badge.v-bad { background: var(--bad-bg); color: var(--bad); }
.badge.v-warn { background: var(--warn-bg); color: var(--warn); }
.badge.v-good { background: var(--good-bg); color: var(--good); }
.badge.v-early { background: var(--early-bg); color: var(--early); }
.badge.v-none { color: var(--text-muted); font-weight: 400; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 13px; color: var(--text-secondary); margin: 0 0 8px; }
.swatch { display: inline-block; width: 18px; height: 0; border-top: 2px solid; vertical-align: middle; margin-right: 6px; }
.chart-box { position: relative; height: 280px; }

.views { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
.view { display: flex; flex-direction: column; gap: 2px; text-align: left; padding: 12px 14px; cursor: pointer;
  background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
  color: var(--text-primary); font: inherit; }
.view:hover { border-color: var(--text-muted); }
.view.on { outline: 2px solid var(--series-1); outline-offset: -1px; }
.view .vt { font-weight: 600; font-size: 14px; }
.view .vn { font-size: 26px; font-weight: 700; font-variant-numeric: tabular-nums; }
.view .vd { color: var(--text-secondary); font-size: 12px; }
.view.v-bad .vt { color: var(--bad); }
.view.v-warn .vt { color: var(--warn); }
.view.v-down .vt { color: var(--down); }
.view.v-up .vt { color: var(--up); }

details.how { margin: 0 0 12px; }
details.how summary { cursor: pointer; color: var(--series-1); font-size: 14px; }
details.how .card { margin-top: 8px; font-size: 14px; color: var(--text-secondary); }
details.how p { margin: 0 0 10px; }
.vlegend { display: flex; flex-direction: column; gap: 6px; margin: 0 0 10px; }
.vlegend .badge { min-width: 170px; margin-right: 8px; }

.filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 12px; }
.filters input, .filters select { padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px;
  background: var(--surface-0); color: var(--text-primary); font: inherit; }
.filters input[type="search"] { flex: 1 1 220px; min-width: 0; }
.filters input[type="number"] { width: 110px; }
.filters .chk { display: inline-flex; align-items: center; gap: 6px; color: var(--text-secondary); font-size: 14px; }
.filters .count { margin-left: auto; color: var(--text-muted); font-size: 13px; white-space: nowrap; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { padding: 8px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
th { color: var(--text-secondary); font-weight: 600; white-space: nowrap; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
td .sub { color: var(--text-muted); font-size: 12px; }
th.sortable { cursor: pointer; user-select: none; }
th.sortable:hover { color: var(--text-primary); }
.arrow { color: var(--series-1); }
tbody tr { cursor: pointer; }
tbody tr:hover { background: var(--surface-0); }
tbody tr.sel { background: var(--sel); }
a.ext { text-decoration: none; margin-left: 6px; }
.up { color: var(--up); } .down { color: var(--down); } .muted { color: var(--text-muted); }
.empty { color: var(--text-muted); padding: 16px 10px; }
.trunc-note { color: var(--text-muted); font-size: 13px; padding: 10px; }

.stores { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
.store { cursor: pointer; }
.store:hover { border-color: var(--text-muted); }
.store h3 { margin: 0; font-size: 16px; }
.store .src { color: var(--text-muted); font-size: 12px; margin: 2px 0 0; }
.store .count { font-size: 28px; font-weight: 700; margin: 8px 0 0; font-variant-numeric: tabular-nums; }
.store .count small { font-size: 14px; font-weight: 400; color: var(--text-secondary); }
.store dl { display: grid; grid-template-columns: 1fr auto; gap: 4px 12px; margin: 10px 0 0; }
.store dt { color: var(--text-secondary); }
.store dd { margin: 0; font-weight: 600; text-align: right; font-variant-numeric: tabular-nums; }
.foot { margin-top: 32px; }
</style>
</head>
<body>
<main>
  <h1>ფასების მონიტორი</h1>
  <p class="lead">ვამოწმებთ, ნამდვილია თუ არა ელექტრონიკის მაღაზიების „ფასდაკლებები“: იაფდება თუ არა ნივთი
    მართლა, თუ მაღაზია უბრალოდ უფრო მაღალ „ძველ ფასს“ აწერს.</p>
  <p class="meta" id="generated"></p>

  <section class="card" id="focus">
    <div class="focus-head">
      <div>
        <h2 id="f-name"></h2>
        <p class="meta" id="f-store"></p>
      </div>
      <a id="f-link" class="btn" target="_blank" rel="noopener">მაღაზიაში ნახვა ↗</a>
    </div>
    <div class="facts">
      <div><span class="label">ახლანდელი ფასი</span><span class="big" id="f-price"></span></div>
      <div><span class="label">მაღაზია წერს, რომ იყო</span><span class="big" id="f-old"></span></div>
      <div><span class="label" id="f-third-label"></span><span class="big" id="f-third"></span></div>
    </div>
    <p class="verdict" id="f-verdict"></p>
    <div class="legend">
      <span><span class="swatch" style="border-color: var(--series-1)"></span>ფასი, რომლითაც იყიდებოდა</span>
      <span><span class="swatch" style="border-top-style: dashed; border-color: var(--series-2)"></span>„ძველი“ ფასი, რომელსაც მაღაზია წერს</span>
      <span id="lg-ref"><span class="swatch" style="border-top-style: dotted; border-color: var(--ref)"></span>ფასდაკლებამდე ყველაზე იაფი (30 დღე)</span>
    </div>
    <div class="chart-box"><canvas id="chart" role="img" aria-label="ფასის ისტორიის გრაფიკი"></canvas></div>
    <p class="meta" id="f-gap"></p>
  </section>

  <h2>რა ვიპოვეთ</h2>
  <p class="note">დააჭირე ბარათს — ქვემოთ სია გაიფილტრება. კიდევ ერთი დაჭერა ფილტრს მოხსნის.</p>
  <div class="views" id="views"></div>

  <h2 id="list">პროდუქტები</h2>
  <details class="how">
    <summary>როგორ ვაფასებთ ფასდაკლებას?</summary>
    <div class="card">
      <p>ფასებს დღეში ორჯერ ვინიშნავთ. ფასდაკლებას ვამოწმებთ იმავე წესით, რასაც ევროკავშირში იყენებენ:
        ფასდაკლება უნდა ითვლებოდეს იმ ყველაზე დაბალი ფასიდან, რომლითაც ნივთი ფასდაკლების დაწყებამდე
        ბოლო 30 დღეში იყიდებოდა — და არა მაღაზიის მიერ მიწერილი „ძველი ფასიდან“.</p>
      <div class="vlegend" id="vlegend"></div>
      <p>„7 დღეში“ — რამდენით შეიცვალა ფასი ერთი კვირის წინანდელთან შედარებით.
        ★ — ახლანდელი ფასი ყველაზე დაბალია, რაც ამ ნივთზე გვინახავს.</p>
    </div>
  </details>
  <div class="card">
    <div class="filters">
      <input id="q" type="search" placeholder="ძებნა სახელით…" autocomplete="off">
      <select id="f-s"><option value="">ყველა მაღაზია</option></select>
      <select id="f-c"><option value="">ყველა კატეგორია</option></select>
      <select id="f-v">
        <option value="">ყველა შეფასება</option>
        <option value="any">მხოლოდ ფასდაკლებით</option>
        <option value="fake">⚠ ყალბი ფასდაკლება</option>
        <option value="exaggerated">⚠ გაზვიადებული</option>
        <option value="real">✓ ნამდვილი</option>
        <option value="unconfirmed">? ვერ ვამოწმებთ</option>
        <option value="none">— ფასდაკლების გარეშე</option>
      </select>
      <select id="f-ch">
        <option value="">ფასი 7 დღეში: ნებისმიერი</option>
        <option value="down">▼ გაიაფდა</option>
        <option value="up">▲ გაძვირდა</option>
        <option value="same">არ შეცვლილა</option>
      </select>
      <input id="f-min" type="number" inputmode="numeric" min="0" placeholder="₾-დან">
      <input id="f-max" type="number" inputmode="numeric" min="0" placeholder="₾-მდე">
      <label class="chk"><input id="f-lo" type="checkbox"> ★ ყველაზე დაბალ ფასზე</label>
      <button id="reset" class="btn" type="button">გასუფთავება</button>
      <span class="count" id="count"></span>
    </div>
    <div class="table-wrap"><table id="catalog"></table></div>
    <div class="trunc-note" id="trunc" hidden></div>
  </div>

  <h2>მაღაზიების მიხედვით</h2>
  <p class="note">დააჭირე მაღაზიას, რომ მხოლოდ მისი პროდუქტები ნახო.</p>
  <div class="stores" id="stores"></div>

  <p class="note foot">zoommer.ge-ს, ee.ge-სა და kontakt.ge-ს ფასებს პირდაპირ მაღაზიების საიტებიდან ვიღებთ
    (kontakt.ge-ს სიაში მხოლოდ მარაგში არსებული ნივთები ჩანს). alta.ge-ს ფასები
    მოდის gamige.com-იდან (ფასების შედარების საიტი), რადგან ალტა ავტომატურ წვდომას ზღუდავს. ბმული ამ გვერდზე
    არჩეულ ფილტრებს ინახავს — შეგიძლია გააზიარო.</p>
</main>

<script id="data" type="application/json">__DATA__</script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script>
const D = JSON.parse(document.getElementById("data").textContent);
const products = new Map(D.products.map(p => [String(p[0]), p]));
const STORE = {ee: "ee.ge", zoommer: "zoommer.ge", alta_gamige: "alta.ge", kontakt: "kontakt.ge"};
const STORE_SRC = {alta_gamige: "ფასები gamige.com-იდან"};
const CAT = {smartphones: "ტელეფონები", laptops: "ლეპტოპები", tvs: "ტელევიზორები",
             headphones: "ყურსასმენები", large_appliances: "მსხვილი ტექნიკა"};
const V = {
  fake: {rank: 0, icon: "⚠", label: "ყალბი ფასდაკლება", cls: "v-bad",
         help: "„ფასდაკლება“ აწერია, მაგრამ ფასი ფასდაკლებამდელზე დაბალი არ არის — ან „ძველი ფასი“ ხელოვნურად გაზარდეს"},
  exaggerated: {rank: 1, icon: "⚠", label: "გაზვიადებული", cls: "v-warn",
                help: "მართლა გაიაფდა, მაგრამ გაცილებით ნაკლებით, ვიდრე მაღაზია წერს"},
  real: {rank: 2, icon: "✓", label: "ნამდვილი", cls: "v-good",
         help: "ფასი მართლა დაიწია დაახლოებით იმდენით, რამდენსაც მაღაზია წერს"},
  unconfirmed: {rank: 3, icon: "?", label: "ვერ ვამოწმებთ", cls: "v-early",
                help: "ფასდაკლებაზე იყო მას შემდეგ, რაც ვაკვირდებით — ფასდაკლებამდელი ფასი არ გვინახავს"},
  none: {rank: 4, icon: "—", label: "ფასდაკლება არ არის", cls: "v-none",
         help: "მაღაზია „ძველ ფასს“ არ აჩვენებს"},
};
const gel = v => v == null ? "—" : v.toLocaleString("ka-GE", {minimumFractionDigits: 2, maximumFractionDigits: 2}) + " ₾";
const day = t => new Date(t).toLocaleDateString("en-GB", {day: "numeric", month: "short"});
const dateTime = t => new Date(t).toLocaleString("en-GB", {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false});
const pc = v => Math.round(Math.abs(v)) + "%";
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const storeName = s => STORE[s] || s;
const $ = id => document.getElementById(id);

function el(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  Object.assign(e, props);
  for (const k of kids) e.append(k);
  return e;
}
const badge = v => el("span", {className: "badge " + V[v].cls, textContent: V[v].icon + " " + V[v].label, title: V[v].help});

const catalog = D.catalog.map(([id, v, price, old, claimed, actual, ref, since, reason, oldFrom, max, min, chg7, low,
                                 sinceFrom]) => {
  const p = products.get(String(id));
  return {id: String(id), v, rank: V[v].rank, price, old, claimed, actual, ref, since, sinceFrom, reason, oldFrom,
          max, min, chg7, low: !!low, gap: claimed == null ? null : claimed - (actual ?? 0),
          store: p[1], name: p[2], url: p[3], cat: p[4], first: p[5], last: p[6]};
});
const gapText = g => g.map(([a, b]) => `${dateTime(a)} – ${dateTime(b)}`).join(", ");
const byId = new Map(catalog.map(c => [c.id, c]));

$("generated").textContent = "ბოლო განახლება: " + dateTime(D.generated);

// ---- Filter state, kept in the URL hash so a view can be shared.
const DEF = {q: "", s: "", c: "", v: "", ch: "", lo: false, min: "", max: "", sort: "rank", dir: 1, p: ""};
const state = {...DEF};
function readHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  for (const k of Object.keys(DEF)) {
    if (!h.has(k)) continue;
    const raw = h.get(k);
    state[k] = k === "lo" ? raw === "1" : k === "dir" ? (Number(raw) < 0 ? -1 : 1) : raw;
  }
}
function writeHash() {
  const h = new URLSearchParams();
  for (const [k, d] of Object.entries(DEF))
    if (state[k] !== d && state[k] !== "") h.set(k, k === "lo" ? "1" : String(state[k]));
  const s = h.toString();
  history.replaceState(null, "", s ? "#" + s : location.pathname + location.search);
}

const CONTROLS = {q: $("q"), s: $("f-s"), c: $("f-c"), v: $("f-v"), ch: $("f-ch"), min: $("f-min"), max: $("f-max")};
for (const s of [...new Set(catalog.map(c => c.store))].sort())
  CONTROLS.s.append(el("option", {value: s, textContent: storeName(s)}));
for (const c of [...new Set(catalog.map(c => c.cat))].sort())
  CONTROLS.c.append(el("option", {value: c, textContent: CAT[c] || c}));
function syncControls() {
  for (const [k, input] of Object.entries(CONTROLS)) input.value = state[k];
  $("f-lo").checked = state.lo;
}
for (const [k, input] of Object.entries(CONTROLS))
  input.addEventListener("input", () => { state[k] = input.value; render(); });
$("f-lo").addEventListener("input", e => { state.lo = e.target.checked; render(); });
$("reset").addEventListener("click", () => { Object.assign(state, DEF, {p: state.p}); syncControls(); render(); });

const base = c => (!state.s || c.store === state.s) && (!state.c || c.cat === state.c) &&
  (state.min === "" || (c.price ?? -1) >= Number(state.min)) &&
  (state.max === "" || (c.price ?? Infinity) <= Number(state.max)) &&
  (!state.q || c.name.toLowerCase().includes(state.q.trim().toLowerCase()));
const vOk = c => !state.v || (state.v === "any" ? c.v !== "none" : c.v === state.v);
const chOk = c => !state.ch || (state.ch === "down" ? c.chg7 < 0 : state.ch === "up" ? c.chg7 > 0 : !c.chg7);

// ---- Quick views ("what we found").
const VIEWS = [
  {title: "⚠ ყალბი ფასდაკლება", desc: "„ფასდაკლება“ აწერია, ფასი კი არ დაწეულა", cls: "v-bad",
   set: {v: "fake", ch: "", lo: false}, sort: ["claimed", -1]},
  {title: "⚠ გაზვიადებული", desc: "გაიაფდა, მაგრამ ნაკლებით, ვიდრე წერია", cls: "v-warn",
   set: {v: "exaggerated", ch: "", lo: false}, sort: ["gap", -1]},
  {title: "▼ გაიაფდა ამ კვირაში", desc: "ფასი 7 დღის წინანდელზე დაბალია", cls: "v-down",
   set: {v: "", ch: "down", lo: false}, sort: ["chg7", 1]},
  {title: "★ ყველაზე დაბალ ფასზეა", desc: "ახლა ყველაზე იაფია, რაც გვინახავს", cls: "",
   set: {v: "", ch: "", lo: true}, sort: ["chg7", 1]},
  {title: "▲ გაძვირდა ამ კვირაში", desc: "ფასი 7 დღის წინანდელზე მაღალია", cls: "v-up",
   set: {v: "", ch: "up", lo: false}, sort: ["chg7", -1]},
];
const viewOn = w => Object.entries(w.set).every(([k, x]) => state[k] === x);
const inView = (w, c) => (!w.set.v || c.v === w.set.v) && (!w.set.lo || c.low) &&
  (!w.set.ch || (w.set.ch === "down" ? c.chg7 < 0 : c.chg7 > 0));
function renderViews(pool) {
  const box = $("views");
  box.replaceChildren();
  for (const w of VIEWS) {
    const n = pool.filter(c => inView(w, c)).length;
    const b = el("button", {type: "button", className: "view " + w.cls + (viewOn(w) ? " on" : "")},
      el("span", {className: "vt", textContent: w.title}),
      el("span", {className: "vn", textContent: n.toLocaleString("ka-GE")}),
      el("span", {className: "vd", textContent: w.desc}));
    b.addEventListener("click", () => {
      if (viewOn(w)) Object.assign(state, {v: "", ch: "", lo: false, sort: "rank", dir: 1});
      else Object.assign(state, w.set, {sort: w.sort[0], dir: w.sort[1]});
      syncControls();
      render();
    });
    box.append(b);
  }
}

// ---- Product table.
const CAP = 500;
const HEADS = [
  ["პროდუქტი", "name", "t"], ["მაღაზია", "store", "t"], ["ფასი", "price", "n"],
  ["7 დღეში", "chg7", "n", "ფასის ცვლილება ერთი კვირის წინანდელთან შედარებით"],
  ["მაღაზია წერს", "claimed", "n", "ფასდაკლება, რომელსაც მაღაზია აჩვენებს"],
  ["რეალურად", "actual", "n", "რამდენით გაიაფდა ჩვენი დაკვირვებით"],
  ["შეფასება", "rank", "r"],
];
const val = (c, k) => k === "store" ? storeName(c.store) : c[k];
function cmp(a, b) {
  const x = val(a, state.sort), y = val(b, state.sort);
  let d = 0;
  if (x == null || y == null) d = x == null && y == null ? 0 : x == null ? 1 : -1;  // empty values last
  else d = (typeof x === "string" ? x.localeCompare(y, "ka") : x - y) * state.dir;
  return d || a.rank - b.rank || (b.gap ?? -1) - (a.gap ?? -1);
}
function chgCell(v) {
  if (!v) return "—";
  return el("span", {className: v < 0 ? "down" : "up", textContent: (v < 0 ? "▼ " : "▲ ") + pc(v)});
}
function actualCell(c) {
  if (c.v === "none" || c.actual == null) return "—";
  if (c.actual > 0) return "−" + pc(c.actual);
  if (c.actual < 0) return el("span", {className: "up", textContent: "+" + pc(c.actual)});
  return "0%";
}

let lastRows = [];
function render() {
  const pool = catalog.filter(base);
  renderViews(pool);
  const rows = pool.filter(c => vOk(c) && chOk(c) && (!state.lo || c.low));
  rows.sort(cmp);
  lastRows = rows;
  $("count").textContent = rows.length.toLocaleString("ka-GE") + " პროდუქტი";

  const tr = el("tr");
  for (const [label, key, kind, title] of HEADS) {
    const th = el("th", {className: (kind === "n" ? "n " : "") + "sortable", title: title || "დალაგება"}, label);
    if (state.sort === key) th.append(el("span", {className: "arrow", textContent: state.dir > 0 ? " ↑" : " ↓"}));
    th.addEventListener("click", () => {
      if (state.sort === key) state.dir = -state.dir;
      else { state.sort = key; state.dir = kind === "n" ? -1 : 1; }
      render();
    });
    tr.append(th);
  }
  const body = el("tbody");
  if (!rows.length)
    body.append(el("tr", {}, el("td", {className: "empty", colSpan: HEADS.length, textContent: "ვერაფერი მოიძებნა. სცადე სხვა ფილტრი."})));
  for (const c of rows.slice(0, CAP)) {
    const row = el("tr", {className: c.id === state.p ? "sel" : ""});
    row.dataset.id = c.id;
    row.addEventListener("click", () => show(c.id, true));
    const ext = el("a", {className: "ext", href: c.url, target: "_blank", rel: "noopener", textContent: "↗", title: "მაღაზიის გვერდი"});
    ext.addEventListener("click", e => e.stopPropagation());
    row.append(
      el("td", {}, el("div", {}, c.name, ext),
         el("div", {className: "sub", textContent: (CAT[c.cat] || c.cat) + (c.low ? " · ★ ყველაზე დაბალი ფასი" : "")})),
      el("td", {textContent: storeName(c.store)}),
      el("td", {className: "n", textContent: gel(c.price)}),
      el("td", {className: "n"}, chgCell(c.chg7)),
      el("td", {className: "n", textContent: c.claimed == null ? "—" : "−" + pc(c.claimed)}),
      el("td", {className: "n"}, actualCell(c)),
      el("td", {}, badge(c.v)),
    );
    body.append(row);
  }
  $("catalog").replaceChildren(el("thead", {}, tr), body);
  $("trunc").hidden = rows.length <= CAP;
  if (rows.length > CAP) $("trunc").textContent = `ნაჩვენებია პირველი ${CAP} — დააზუსტე ძებნა ან ფილტრი.`;
  writeHash();
}

// ---- Store cards.
const firstSeen = {};
for (const c of catalog) firstSeen[c.store] = Math.min(firstSeen[c.store] ?? Infinity, c.first);
for (const s of D.stores) {
  const dl = el("dl");
  for (const [k, n] of [["ფასდაკლებით", s.on_sale], ["⚠ ყალბი", s.fake], ["⚠ გაზვიადებული", s.exaggerated],
                        ["✓ ნამდვილი", s.real], ["? ვერ ვამოწმებთ", s.unconfirmed]])
    dl.append(el("dt", {textContent: k}), el("dd", {textContent: n.toLocaleString("ka-GE")}));
  const gaps = D.gaps[s.store] || [];
  const card = el("div", {className: "card store", title: "მხოლოდ ამ მაღაზიის ჩვენება"},
    el("h3", {textContent: storeName(s.store)}),
    el("p", {className: "src", textContent: [STORE_SRC[s.store], "ვაკვირდებით " + day(firstSeen[s.store]) + "-დან",
                                              "ბოლოს შემოწმდა " + dateTime(s.last_run)].filter(Boolean).join(" · ")}),
    gaps.length ? el("p", {className: "src", textContent: "ფასებს არ ვაგროვებდით: " + gapText(gaps)}) : "",
    el("p", {className: "count"}, s.products.toLocaleString("ka-GE") + " ", el("small", {textContent: "პროდუქტი"})), dl);
  card.addEventListener("click", () => {
    state.s = s.store;
    syncControls();
    render();
    $("list").scrollIntoView({behavior: "smooth", block: "start"});
  });
  $("stores").append(card);
}

for (const k of Object.keys(V)) $("vlegend").append(el("div", {}, badge(k), V[k].help));

// ---- The selected product, with its chart, at the top.
function explain(c) {
  // A sale first seen right after a gap in our data started somewhere inside it.
  const since = !c.since ? "" : c.sinceFrom ? `${day(c.sinceFrom)}–${day(c.since)} შუალედში` : day(c.since);
  const unsure = c.sinceFrom ? ` ზუსტ დღეს ვერ ვიტყვით: ${day(c.sinceFrom)}-დან ${day(c.since)}-მდე ფასებს არ ვაგროვებდით.` : "";
  switch (c.v) {
    case "fake":
      if (c.reason === "old_raised")
        return `ფასდაკლების დროს მაღაზიამ „ძველი ფასი“ ${gel(c.oldFrom)}-დან ${gel(c.old)}-მდე გაზარდა, ` +
               `თვითონ ფასი კი არ დაუწევია — „ფასდაკლება“ ხელოვნურად გაიბერა.`;
      if (c.reason === "resumed")
        return `ეს „ფასდაკლება“ ახალი არ არის: ბოლო 30 დღეში ეს ნივთი უკვე იყიდებოდა ${gel(c.ref)}-ად ` +
               `(მაშინაც „ფასდაკლებით“). შუალედში ფასდაკლება მოხსნეს და მერე ისევ დააბრუნეს „−${pc(c.claimed)}“-ით — ` +
               `რეალურად ფასი იგივეა, რაც ადრე.` + unsure;
      return `ფასდაკლების დაწყებამდე (${since}) ბოლო 30 დღეში ეს ნივთი ${gel(c.ref)}-ად იყიდებოდა. ` +
             `ახლა მაღაზია წერს „იყო ${gel(c.old)}“, მაგრამ ფასი ${gel(c.price)}-ია — ` +
             (c.actual < 0 ? "მანამდელზე ძვირიც კი." : "ანუ რეალურად არ გაიაფებულა.") + unsure;
    case "exaggerated":
      return `მაღაზია წერს −${pc(c.claimed)}, მაგრამ ფასდაკლების დაწყებამდე (${since}) ბოლო 30 დღეში ` +
             `ყველაზე იაფი ფასი ${gel(c.ref)} იყო — რეალურად მხოლოდ −${pc(c.actual)}-ით გაიაფდა.` + unsure;
    case "real":
      return `ფასდაკლების დაწყებამდე (${since}) ${gel(c.ref)}-ად იყიდებოდა, ახლა ${gel(c.price)}-ია — ` +
             `ფასდაკლება ნამდვილია (−${pc(c.actual)}).` + unsure;
    case "unconfirmed":
      return `ეს ნივთი „ფასდაკლებაზეა“ მას შემდეგ, რაც ვაკვირდებით (${day(c.first)}-დან), ამიტომ „ძველ ფასს“ ` +
             `ვერ ვამოწმებთ.` + (c.actual > 0 ? ` ამ ხნის განმავლობაში ფასი ${gel(c.max)}-დან ${gel(c.price)}-მდე ` +
             `დაიწია (−${pc(c.actual)}).` : "");
    default:
      return "მაღაზია ამ ნივთზე ფასდაკლებას არ აჩვენებს." +
             (c.chg7 ? ` ბოლო 7 დღეში ${c.chg7 < 0 ? "გაიაფდა" : "გაძვირდა"} ${pc(c.chg7)}-ით.` : "") +
             (c.low ? " ახლა ყველაზე დაბალ ფასზეა, რაც გვინახავს." : "");
  }
}

let chart, xSpan = 0;
const DAY_MS = 86400000;
// Short histories (a day or two) collapse day-only ticks into "10 Nov" repeated
// several times; show the time too until the span is wide enough for plain dates.
const xTick = v => xSpan < 3 * DAY_MS
  ? new Date(v).toLocaleString("en-GB", {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false})
  : day(v);

function show(id, scroll) {
  const c = byId.get(String(id)), rows = D.series[String(id)];
  if (!c || !rows) return;
  state.p = c.id;
  writeHash();
  document.querySelectorAll("#catalog tbody tr").forEach(tr => tr.classList.toggle("sel", tr.dataset.id === c.id));
  $("f-name").textContent = c.name;
  $("f-store").textContent = storeName(c.store) + (STORE_SRC[c.store] ? " (" + STORE_SRC[c.store] + ")" : "") +
    " · " + (CAT[c.cat] || c.cat) + " · ბოლოს შემოწმდა " + dateTime(c.last);
  $("f-link").href = c.url;
  $("f-price").textContent = gel(c.price);
  $("f-old").textContent = c.v === "none" ? "—" : gel(c.old);
  const [thirdLabel, third] = c.ref != null ? ["ფასდაკლებამდე ყველაზე იაფი (30 დღე)", c.ref]
    : c.v !== "none" ? ["ყველაზე მაღალი ფასი, რაც ვნახეთ", c.max] : ["ყველაზე დაბალი ფასი, რაც ვნახეთ", c.min];
  $("f-third-label").textContent = thirdLabel;
  $("f-third").textContent = gel(third);
  $("f-verdict").className = "verdict " + V[c.v].cls;
  $("f-verdict").replaceChildren(badge(c.v), explain(c));
  $("lg-ref").hidden = c.ref == null;

  const end = Math.max(products.get(c.id)[6], rows[rows.length - 1][0]);
  // X window: fit the data (with a little padding), but never narrower than a
  // day, so a one- or two-point series doesn't blow the axis out to months.
  const lo = rows[0][0];
  let xMin = lo, xMax = end;
  if (end - lo < DAY_MS) { const mid = (lo + end) / 2; xMin = mid - DAY_MS / 2; xMax = mid + DAY_MS / 2; }
  else { const pad = (end - lo) * 0.03; xMin -= pad; xMax += pad; }
  xSpan = xMax - xMin;
  // Draw each value as a step that holds until the next change. Stretches where
  // we weren't collecting (gaps) are split out and drawn faint and dotted, so the
  // chart never pretends we saw the price then. Every dataset gets the same x
  // positions, so tooltips line up.
  const gaps = (D.gaps[c.store] || []).filter(([a, b]) => b > lo && a < end);
  $("f-gap").textContent = gaps.length ? "მკრთალი ხაზი — ამ დროს ფასებს არ ვაგროვებდით: " + gapText(gaps) : "";
  const steps = i => rows.flatMap((r, k) => {
    const t1 = Math.max(k + 1 < rows.length ? rows[k + 1][0] : end, r[0]);
    const out = [{x: r[0], y: r[i]}];
    for (const [a, b] of gaps)
      if (a >= r[0] && b <= t1) out.push({x: a, y: r[i], hold: true}, {x: b, y: r[i], hold: true});
    out.push({x: t1, y: r[i], hold: true});
    return out;
  });
  const inGap = s => gaps.some(([a, b]) => s.p0.parsed.x >= a && s.p1.parsed.x <= b);
  const faint = (color, dash) => ({borderColor: s => inGap(s) ? color + "66" : color,
                                   borderDash: s => inGap(s) ? [2, 4] : dash});
  const dot = ctx => ctx.raw && ctx.raw.hold ? 0 : 4;
  const datasets = [
    {label: "ფასი", data: steps(1), borderColor: css("--series-1"), backgroundColor: css("--series-1"),
     borderWidth: 2, pointRadius: dot, pointHoverRadius: dot, spanGaps: false,
     segment: faint(css("--series-1"), undefined)},
    {label: "„ძველი“ ფასი", data: steps(2), borderColor: css("--series-2"), backgroundColor: css("--series-2"),
     borderWidth: 2, borderDash: [6, 4], pointRadius: dot, pointHoverRadius: dot, spanGaps: false,
     segment: faint(css("--series-2"), [6, 4])},
  ];
  if (c.ref != null)
    datasets.push({label: "ფასდაკლებამდე ყველაზე იაფი", data: steps(1).map(p => ({...p, y: c.ref})),
                   borderColor: css("--ref"), backgroundColor: css("--ref"), borderWidth: 1.5,
                   borderDash: [2, 4], pointRadius: 0, pointHoverRadius: 0});
  if (chart) {
    chart.data = {datasets};
    chart.options.scales.x.min = xMin;
    chart.options.scales.x.max = xMax;
    chart.update();
  } else {
    chart = new Chart($("chart"), {
      type: "line", data: {datasets},
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: {mode: "index", intersect: false},
        plugins: {legend: {display: false},
          tooltip: {filter: i => !i.raw.hold,
                    callbacks: {title: items => dateTime(items[0].parsed.x),
                                label: i => i.dataset.label + ": " + gel(i.parsed.y)}}},
        scales: {
          x: {type: "linear", min: xMin, max: xMax, grid: {color: css("--grid")}, border: {display: false},
              ticks: {color: css("--text-muted"), maxTicksLimit: 6, callback: v => xTick(v)}},
          y: {grid: {color: css("--grid")}, border: {display: false},
              ticks: {color: css("--text-muted"), callback: v => v.toLocaleString("ka-GE") + " ₾"}},
        },
      },
    });
  }
  if (scroll) $("focus").scrollIntoView({behavior: "smooth", block: "start"});
}

readHash();
syncControls();
render();
const start = byId.has(state.p) ? state.p : lastRows[0]?.id ?? catalog[0]?.id;
if (start != null) show(start, false);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
