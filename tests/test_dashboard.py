from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from dashboard import build, render

NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
RUN = NOW - timedelta(hours=1)  # latest run
OLD = NOW - timedelta(days=10)  # first observation


def product(pid, store="ee", first=OLD, last=RUN):
    return (pid, store, f"p{pid}", f"https://ee.ge/p{pid}", "tvs", first, last)


def test_suspicious_needs_old_price_above_anything_seen_and_a_week_of_history():
    products = [product(1), product(2), product(3, first=NOW - timedelta(days=2))]
    changes = [
        # 1: sold at 1000 and 1100, now 900 "was 1500" -> never sold at 1500: suspicious
        (1, OLD, D("1000"), None), (1, OLD + timedelta(days=3), D("1100"), None), (1, RUN, D("900"), D("1500")),
        # 2: sold at 1500, now 1200 "was 1500" -> a real discount
        (2, OLD, D("1500"), None), (2, RUN, D("1200"), D("1500")),
        # 3: same pattern as 1 but only 2 days of history -> not yet
        (3, NOW - timedelta(days=2), D("1000"), None), (3, RUN, D("900"), D("1500")),
    ]
    data = build(products, changes, NOW)
    assert [s["id"] for s in data["suspicious"]] == [1]
    s = data["suspicious"][0]
    assert (s["price"], s["old"], s["max_seen"], s["claimed_pct"]) == (900.0, 1500.0, 1100.0, 40.0)
    assert data["stores"][0]["crossed"] == 3 and data["stores"][0]["suspicious"] == 1


def test_recent_changes_and_products_gone_from_the_latest_run():
    products = [product(1), product(2, last=RUN - timedelta(days=3))]  # 2 no longer listed
    changes = [
        (1, OLD, D("100"), None), (1, NOW - timedelta(days=2), D("80"), None),
        (2, OLD, D("50"), None), (2, NOW - timedelta(days=4), D("60"), None),
    ]
    data = build(products, changes, NOW)
    assert data["stores"][0]["products"] == 1
    assert [(r["id"], r["before"], r["after"], r["pct"]) for r in data["recent"]] == [(1, 100.0, 80.0, -20.0)]
    assert set(data["series"]) == {1, 2}  # history stays searchable


def test_render_embeds_json_safely():
    html = render({"x": "</script><b>"})
    assert "</script><b>" not in html and "<\\/script>" in html
