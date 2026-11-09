from decimal import Decimal

from gept import run, telegram
from gept.parse import Item
from gept.quality import count_issues, price_issues


def item(pid="1", price="100.00"):
    return Item(
        store="ee", store_product_id=pid, name="TV", url="https://ee.ge/tv-p1", category="tvs",
        price=Decimal(price) if price is not None else None, old_price=None, in_stock=True, buyable=True,
        has_discount=None, discount_amount=None, discount_percent=None,
    )


def test_count_drop_over_half_is_reported():
    previous = {"ee/tvs/a": 250, "ee/tvs/b": 100, "ee/tvs/c": 40}
    current = {"ee/tvs/a": 124, "ee/tvs/b": 51, "ee/tvs/c": 40, "ee/tvs/new": 7}
    assert count_issues(previous, current) == ["ee/tvs/a: 124 items, was 250"]


def test_zero_count_is_reported():
    assert count_issues({}, {"ee/tvs/a": 0}) == ["ee/tvs/a: 0 items"]


def test_price_checks():
    latest = {"1": (Decimal("100.00"), None, True, True), "2": (Decimal("100.00"), None, True, True)}
    issues = price_issues(latest, [item("1", "5.00"), item("2", "150.00"), item("3", "0.00"), item("4", None)])
    assert issues == [
        "ee: 100.00 → 5.00 for TV https://ee.ge/tv-p1",
        "ee: price 0.00 for TV https://ee.ge/tv-p1",
        "ee: price None for TV https://ee.ge/tv-p1",
    ]


def test_new_product_with_normal_price_is_fine():
    assert price_issues({}, [item("9", "2999.99")]) == []


def test_report_sends_issues_or_summary(monkeypatch):
    sent = []
    monkeypatch.setattr(telegram, "send", sent.append)
    monkeypatch.delenv("TELEGRAM_SUMMARY", raising=False)
    run.report([], {"ee": (2176, 407)})
    run.report(["failed: ee/tvs/x"], {"ee": (2176, 407)})
    monkeypatch.setenv("TELEGRAM_SUMMARY", "0")
    run.report([], {"ee": (2176, 407)})
    assert sent == [
        "✅ ge-price-tracker: ee 2176 products / 407 changes",
        "⚠️ ge-price-tracker: 1 issue(s)\nfailed: ee/tvs/x\n\nee 2176 products / 407 changes",
    ]


def test_telegram_without_config_does_not_send(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert telegram.send("hello") is False
