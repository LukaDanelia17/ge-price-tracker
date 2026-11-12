from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from dashboard import assess, build, render

NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
RUN = NOW - timedelta(hours=1)  # latest run


def ago(days):
    return NOW - timedelta(days=days)


def history(*points):
    """(days_ago, price, old_price) -> change rows, oldest first."""
    return [(ago(d), D(p), D(o) if o else None) for d, p, o in points]


def test_same_price_with_a_new_crossed_out_price_is_fake():
    # yesterday 500 with no discount, today 500 "was 700"
    a = assess(history((10, "500", None), (1, "500", "700")), NOW)
    assert (a.verdict, a.reason, a.ref, a.actual, a.claimed) == ("fake", "no_drop", D("500"), 0.0, 28.6)


def test_raising_the_price_then_discounting_back_is_fake():
    a = assess(history((20, "500", None), (5, "700", None), (1, "500", "700")), NOW)
    assert (a.verdict, a.ref) == ("fake", D("500"))


def test_pausing_a_sale_and_restarting_it_is_fake():
    # 69.99 "was 119.99", a day at 119.99 without the discount, then 69.99 "was 119.99" again
    a = assess(history((8, "69.99", "119.99"), (2, "119.99", None), (1, "69.99", "119.99")), NOW)
    assert (a.verdict, a.reason, a.ref) == ("fake", "resumed", D("69.99"))


def test_small_drop_with_a_big_claim_is_exaggerated():
    a = assess(history((10, "500", None), (1, "450", "700")), NOW)
    assert (a.verdict, a.actual, a.claimed) == ("exaggerated", 10.0, 35.7)


def test_drop_that_matches_the_claim_is_real():
    a = assess(history((10, "700", None), (1, "500", "700")), NOW)
    assert (a.verdict, a.actual) == ("real", 28.6)


def test_only_the_30_days_before_the_sale_count():
    # 400 ended 50 days ago, before the 30-day window that precedes the sale
    a = assess(history((60, "400", None), (50, "600", None), (1, "500", "600")), NOW)
    assert (a.verdict, a.ref) == ("real", D("600"))


def test_on_sale_since_first_seen_is_unconfirmed_with_the_drop_we_saw():
    a = assess(history((8, "299.99", "399.99"), (1, "149.99", "399.99")), NOW)
    assert (a.verdict, a.ref, a.actual) == ("unconfirmed", None, 50.0)


def test_raising_the_crossed_out_price_mid_sale_is_fake():
    a = assess(history((8, "500", "600"), (1, "500", "700")), NOW)
    assert (a.verdict, a.reason, a.old_from) == ("fake", "old_raised", D("600"))


def test_week_change_and_lowest_price():
    a = assess(history((10, "100", None), (2, "80", None)), NOW)
    assert (a.verdict, a.chg7, a.low) == ("none", -20.0, True)
    a = assess(history((10, "100", None)), NOW)
    assert (a.chg7, a.low) == (0.0, False)


def product(pid, last=RUN):
    return (pid, "ee", f"p{pid}", f"https://ee.ge/p{pid}", "tvs", ago(10), last)


def test_build_counts_verdicts_and_skips_products_gone_from_the_latest_run():
    products = [product(1), product(2), product(3, last=RUN - timedelta(days=3))]
    changes = [
        (1, ago(10), D("500"), None), (1, ago(1), D("500"), D("700")),
        (2, ago(10), D("700"), None), (2, ago(1), D("500"), D("700")),
        (3, ago(10), D("50"), None),
    ]
    data = build(products, changes, NOW)
    assert {row[0]: row[1] for row in data["catalog"]} == {1: "fake", 2: "real"}
    s = data["stores"][0]
    assert (s["products"], s["on_sale"], s["fake"], s["real"]) == (2, 2, 1, 1)
    assert set(data["series"]) == {1, 2, 3}  # history stays available for the chart


def test_gaps_in_collection_and_a_sale_that_started_inside_one():
    # Runs at -10d, then nothing until -3d, then 12 h later. The product went on
    # sale somewhere in the week-long hole, so its start is a range, not -3d.
    products = [product(1, last=ago(2.5))]
    changes = [(1, ago(10), D("2199.99"), None), (1, ago(3), D("1549.99"), D("2199.99"))]
    runs = [("ee", ago(10)), ("ee", ago(3)), ("ee", ago(2.5))]
    data = build(products, changes, NOW, runs)
    week_ms = [int(ago(10).timestamp() * 1000), int(ago(3).timestamp() * 1000)]
    assert data["gaps"] == {"ee": [week_ms]}
    row = data["catalog"][0]
    assert (row[1], row[7], row[14]) == ("real", week_ms[1], week_ms[0])  # verdict, since, since_from


def test_a_run_with_no_changes_still_closes_a_gap():
    products = [product(1, last=ago(1))]
    changes = [(1, ago(2), D("100"), None)]
    data = build(products, changes, NOW, runs=[("ee", ago(2)), ("ee", ago(1.5)), ("ee", ago(1))])
    assert data["gaps"] == {"ee": []}


def test_render_embeds_json_safely():
    html = render({"x": "</script><b>"})
    assert "</script><b>" not in html and "<\\/script>" in html


def test_a_category_that_failed_in_the_latest_run_stays_listed():
    def item(pid, category, last):
        return (pid, "ee", f"p{pid}", f"https://ee.ge/p{pid}", category, ago(10), last)

    # tvs was crawled in the latest run; phones failed in it, so its products
    # were last seen in the run before.
    products = [item(1, "tvs", RUN), item(2, "tvs", RUN - timedelta(days=3)), item(3, "smartphones", RUN - timedelta(hours=12))]
    changes = [(pid, ago(10), D("100"), None) for pid in (1, 2, 3)]
    data = build(products, changes, NOW)
    assert {row[0] for row in data["catalog"]} == {1, 3}  # 2 really left its category
