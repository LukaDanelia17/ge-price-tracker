from datetime import datetime

from gept.schedule import TZ, is_due


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)


def test_scheduled_slot_is_due_after_the_previous_run():
    assert is_due(last_success=at(10, 20, 30), now=at(11, 8, 0))


def test_no_extra_runs_between_slots():
    assert not is_due(last_success=at(11, 8, 40), now=at(11, 14, 0))
    assert not is_due(last_success=at(10, 20, 30), now=at(11, 3, 0))


def test_missed_morning_run_is_caught_up_later():
    # Mac was off at 08:00 and came back at 10:30
    assert is_due(last_success=at(10, 20, 30), now=at(11, 10, 30))


def test_manual_run_before_a_slot_doesnt_skip_it():
    assert is_due(last_success=at(11, 15, 0), now=at(11, 20, 0))


def test_first_run_ever_is_due():
    assert is_due(last_success=None, now=at(11, 9, 0))


def test_a_partial_run_is_retried_but_not_every_hour():
    # 20:00 run started and ended with a failed category (no new last_success)
    started = at(11, 20, 2)
    assert not is_due(last_success=at(11, 8, 30), now=at(11, 21, 0), last_attempt=started)
    assert is_due(last_success=at(11, 8, 30), now=at(11, 22, 30), last_attempt=started)


def test_an_attempt_for_an_earlier_slot_doesnt_block_the_next_one():
    assert is_due(last_success=at(11, 8, 30), now=at(11, 20, 0), last_attempt=at(11, 19, 30))
