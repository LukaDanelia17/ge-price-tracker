"""Build dashboard/index.html: a private, self-contained page with what the
crawler has collected. Rebuilt by scripts/crawl.sh after every run.

    .venv/bin/python dashboard.py            # then: open dashboard/index.html

"Suspicious discount": the product currently shows a crossed-out price that is
higher than any price we saw it sell for, and we have watched it for at least
SUSPICIOUS_MIN_DAYS days.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import psycopg

from export import read_env_file

HERE = Path(__file__).resolve().parent
OUT = HERE / "dashboard" / "index.html"
SUSPICIOUS_MIN_DAYS = 7
RECENT_DAYS = 7


def ms(ts: datetime) -> int:
    return int(ts.timestamp() * 1000)


def num(d: Decimal | None) -> float | None:
    return None if d is None else float(d)


def build(products: list[tuple], changes: list[tuple], now: datetime) -> dict:
    """products: (id, store, name, url, category, first_seen, last_seen);
    changes: (product_id, observed_at, price, old_price), ordered by product, time."""
    series: dict[int, list] = {}
    for pid, t, price, old in changes:
        series.setdefault(pid, []).append((t, price, old))

    last_run = {}
    for _, store, *_rest, last_seen in products:
        last_run[store] = max(last_run.get(store, last_seen), last_seen)

    stores = {s: {"store": s, "products": 0, "recent_changes": 0, "crossed": 0, "suspicious": 0, "last_run": ms(t)}
              for s, t in sorted(last_run.items())}
    suspicious, recent = [], []
    recent_since = now - timedelta(days=RECENT_DAYS)
    for pid, store, name, url, category, first_seen, last_seen in products:
        rows = series.get(pid)
        if not rows or last_seen < last_run[store]:
            continue  # not listed in the store's latest run
        st = stores[store]
        st["products"] += 1
        _, price, old = rows[-1]
        if old is not None:
            st["crossed"] += 1
        sold_at = [p for _, p, _ in rows if p is not None]
        if (
            old is not None and price is not None and sold_at and old > max(sold_at)
            and now - first_seen >= timedelta(days=SUSPICIOUS_MIN_DAYS)
        ):
            st["suspicious"] += 1
            suspicious.append({
                "id": pid, "store": store, "name": name, "url": url, "category": category,
                "price": num(price), "old": num(old), "max_seen": num(max(sold_at)),
                "claimed_pct": round(float((old - price) / old * 100), 1),
                "days": (now - first_seen).days,
            })
        for (_, before, _), (t, after, _) in zip(rows, rows[1:]):
            if t >= recent_since and before and after and after != before:
                st["recent_changes"] += 1
                recent.append({
                    "id": pid, "store": store, "name": name, "url": url, "t": ms(t),
                    "before": num(before), "after": num(after),
                    "pct": round(float((after - before) / before * 100), 1),
                })

    suspicious.sort(key=lambda r: -r["claimed_pct"])
    recent.sort(key=lambda r: (-r["t"], r["pct"]))
    return {
        "generated": ms(now),
        "stores": list(stores.values()),
        "suspicious": suspicious,
        "recent": recent[:300],
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
    data = build(products, changes, datetime.now(timezone.utc))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(data), encoding="utf-8")
    print(f"{OUT}: {len(data['products'])} products, {len(data['suspicious'])} suspicious, {len(data['recent'])} recent changes")


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
  --grid: #ebeae6; --series-1: #2a78d6; --series-2: #eb6834;
  --up: #b3261e; --down: #146c2e; --warn-bg: #fff4e0;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface-0: #121211; --surface-1: #1a1a19; --border: #2e2e2c;
    --text-primary: #f0efec; --text-secondary: #c3c2b7; --text-muted: #94938b;
    --grid: #2a2a28; --series-1: #3987e5; --series-2: #d95926;
    --up: #f2867f; --down: #7bd88f; --warn-bg: #3a2c12;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-0: #121211; --surface-1: #1a1a19; --border: #2e2e2c;
  --text-primary: #f0efec; --text-secondary: #c3c2b7; --text-muted: #94938b;
  --grid: #2a2a28; --series-1: #3987e5; --series-2: #d95926;
  --up: #f2867f; --down: #7bd88f; --warn-bg: #3a2c12;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface-0); color: var(--text-primary);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans Georgian", sans-serif; }
main { max-width: 1120px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 18px; margin: 32px 0 4px; }
.sub, .note { color: var(--text-secondary); margin: 0 0 12px; }
.note { font-size: 13px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 12px; margin-top: 16px; }
.card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
.tile h3 { margin: 0 0 8px; font-size: 15px; }
.tile dl { display: grid; grid-template-columns: 1fr auto; gap: 4px 12px; margin: 0; }
.tile dt { color: var(--text-secondary); }
.tile dd { margin: 0; font-variant-numeric: tabular-nums; font-weight: 600; text-align: right; }
.tile .when { color: var(--text-muted); font-size: 13px; margin-top: 8px; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { padding: 8px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
th { color: var(--text-secondary); font-weight: 600; white-space: nowrap; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
tbody tr { cursor: pointer; }
tbody tr:hover { background: var(--surface-0); }
a { color: var(--series-1); }
.up { color: var(--up); } .down { color: var(--down); }
.empty { color: var(--text-muted); padding: 16px 10px; }
.picker { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 12px; }
.picker input { flex: 1 1 320px; min-width: 0; padding: 8px 10px; border: 1px solid var(--border);
  border-radius: 8px; background: var(--surface-1); color: var(--text-primary); font: inherit; }
.legend { display: flex; gap: 16px; font-size: 13px; color: var(--text-secondary); margin: 4px 0 8px; }
.swatch { display: inline-block; width: 18px; height: 0; border-top: 2px solid; vertical-align: middle; margin-right: 6px; }
.chart-box { position: relative; height: 320px; }
#chart-title { font-weight: 600; margin: 0; }
#chart-title a { font-weight: 400; font-size: 13px; margin-left: 6px; }
</style>
</head>
<body>
<main>
  <h1>ფასების მონიტორი</h1>
  <p class="sub" id="generated"></p>

  <div class="tiles" id="tiles"></div>

  <h2>საეჭვო ფასდაკლებები</h2>
  <p class="note">გადახაზული ფასი უფრო მაღალია, ვიდრე ნებისმიერი ფასი, რომლითაც ეს პროდუქტი
    ჩვენი დაკვირვების განმავლობაში (მინიმუმ 7 დღე) იყიდებოდა. დააჭირე ხაზს, რომ ისტორია ნახო.</p>
  <div class="card table-wrap"><table id="suspicious"></table></div>

  <h2>ფასის ისტორია</h2>
  <div class="card">
    <div class="picker">
      <input id="search" list="product-list" placeholder="მოძებნე პროდუქტი სახელით…" autocomplete="off">
      <datalist id="product-list"></datalist>
    </div>
    <p id="chart-title"></p>
    <div class="legend">
      <span><span class="swatch" style="border-color: var(--series-1)"></span>ფასი</span>
      <span><span class="swatch" style="border-top-style: dashed; border-color: var(--series-2)"></span>გადახაზული („ძველი“) ფასი</span>
    </div>
    <div class="chart-box"><canvas id="chart" aria-label="ფასის ისტორიის გრაფიკი" role="img"></canvas></div>
  </div>

  <h2>ბოლო 7 დღის ფასის ცვლილებები</h2>
  <div class="card table-wrap"><table id="recent"></table></div>
</main>

<script id="data" type="application/json">__DATA__</script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script>
const D = JSON.parse(document.getElementById("data").textContent);
const products = new Map(D.products.map(p => [String(p[0]), p]));
const gel = v => v == null ? "—" : v.toLocaleString("ka-GE", {minimumFractionDigits: 2, maximumFractionDigits: 2}) + " ₾";
const day = t => new Date(t).toLocaleDateString("en-GB", {day: "numeric", month: "short"});
const dateTime = t => new Date(t).toLocaleString("en-GB", {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false});
const STORE = {ee: "ee.ge", zoommer: "zoommer.ge", alta: "alta.ge"};
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function el(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  Object.assign(e, props);
  for (const k of kids) e.append(k);
  return e;
}

document.getElementById("generated").textContent = "განახლდა " + dateTime(D.generated);

for (const s of D.stores) {
  const dl = el("dl");
  for (const [k, v] of [["თვალყურს ვადევნებთ", s.products], ["ფასის ცვლილება (7 დღე)", s.recent_changes],
                        ["გადახაზული ფასით", s.crossed], ["საეჭვო ფასდაკლება", s.suspicious]]) {
    dl.append(el("dt", {textContent: k}), el("dd", {textContent: v.toLocaleString("ka-GE")}));
  }
  document.getElementById("tiles").append(
    el("div", {className: "card tile"}, el("h3", {textContent: STORE[s.store] || s.store}), dl,
       el("div", {className: "when", textContent: "ბოლო გაშვება: " + dateTime(s.last_run)})));
}

function table(id, head, rows, cells, emptyText) {
  const t = document.getElementById(id);
  const tr = el("tr");
  for (const [label, numeric] of head) tr.append(el("th", {textContent: label, className: numeric ? "n" : ""}));
  t.append(el("thead", {}, tr));
  const body = el("tbody");
  if (!rows.length) body.append(el("tr", {}, el("td", {className: "empty", colSpan: head.length, textContent: emptyText})));
  for (const r of rows) {
    const row = el("tr");
    row.addEventListener("click", e => { if (e.target.tagName !== "A") show(String(r.id), true); });
    cells(r).forEach((c, i) => row.append(el("td", {className: head[i][1] ? "n" : ""}, c)));
    body.append(row);
  }
  t.append(body);
}
const link = r => el("a", {href: r.url, target: "_blank", rel: "noopener", textContent: r.name});
const pct = v => el("span", {className: v > 0 ? "up" : "down", textContent: (v > 0 ? "+" : "") + v + "%"});

table("suspicious",
  [["მაღაზია"], ["პროდუქტი"], ["ფასი", 1], ["გადახაზული", 1], ["ჩვენ ნანახი მაქს.", 1], ["„ფასდაკლება“", 1], ["დღე", 1]],
  D.suspicious,
  r => [r.store, link(r), gel(r.price), gel(r.old), gel(r.max_seen), "−" + r.claimed_pct + "%", String(r.days)],
  "ჯერ არცერთი. საჭიროა მინიმუმ 7 დღის ისტორია — სია შეივსება, რაც უფრო მეტ ხანს ვაგროვებთ.");

table("recent",
  [["როდის"], ["მაღაზია"], ["პროდუქტი"], ["იყო", 1], ["გახდა", 1], ["ცვლილება", 1]],
  D.recent,
  r => [day(r.t), r.store, link(r), gel(r.before), gel(r.after), pct(r.pct)],
  "ბოლო 7 დღეში ფასი არ შეცვლილა.");

const list = document.getElementById("product-list");
for (const p of D.products) list.append(el("option", {value: p[2] + " · " + p[1] + " #" + p[0]}));
document.getElementById("search").addEventListener("change", e => {
  const m = e.target.value.match(/#(\d+)$/);
  if (m) show(m[1], false);
});

let chart;
function show(id, scroll) {
  const p = products.get(id);
  const rows = D.series[id];
  if (!p || !rows) return;
  const end = Math.max(p[6], rows[rows.length - 1][0]);
  const pts = i => rows.map(r => ({x: r[0], y: r[i]})).concat([{x: end, y: rows[rows.length - 1][i]}]);
  const title = document.getElementById("chart-title");
  title.replaceChildren(p[2] + " · " + p[1], el("a", {href: p[3], target: "_blank", rel: "noopener", textContent: "საიტზე ↗"}));
  const data = {datasets: [
    {label: "ფასი", data: pts(1), borderColor: css("--series-1"), backgroundColor: css("--series-1"),
     borderWidth: 2, stepped: "before", pointRadius: 4, pointHoverRadius: 6},
    {label: "გადახაზული ფასი", data: pts(2), borderColor: css("--series-2"), backgroundColor: css("--series-2"),
     borderWidth: 2, borderDash: [6, 4], stepped: "before", pointRadius: 4, pointHoverRadius: 6, spanGaps: false},
  ]};
  if (chart) { chart.data = data; chart.update(); }
  else {
    chart = new Chart(document.getElementById("chart"), {
      type: "line", data,
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: {mode: "index", intersect: false},
        plugins: {legend: {display: false},
          tooltip: {callbacks: {title: items => dateTime(items[0].parsed.x),
                                label: c => c.dataset.label + ": " + gel(c.parsed.y)}}},
        scales: {
          x: {type: "linear", grid: {color: css("--grid")}, border: {display: false},
              ticks: {color: css("--text-muted"), maxTicksLimit: 8, callback: v => day(v)}},
          y: {grid: {color: css("--grid")}, border: {display: false},
              ticks: {color: css("--text-muted"), callback: v => v.toLocaleString("ka-GE") + " ₾"}},
        },
      },
    });
  }
  if (scroll) document.getElementById("chart-title").scrollIntoView({behavior: "smooth", block: "start"});
}
const first = D.suspicious[0] || D.recent[0];
if (first) show(String(first.id), false);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
