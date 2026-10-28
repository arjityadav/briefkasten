from datetime import date

import pytest

from briefkasten.models import DeadlineRule, ExtractedDeadline
from briefkasten.tools.deadlines import add_months, notification_date, resolve_deadline, urgency
from briefkasten.tools.holidays_de import easter_sunday, holiday_name, next_working_day, public_holidays

TODAY = date(2026, 10, 28)


@pytest.mark.parametrize(
    "year,expected",
    [(2024, date(2024, 3, 31)), (2025, date(2025, 4, 20)), (2026, date(2026, 4, 5)), (2027, date(2027, 3, 28)), (2038, date(2038, 4, 25))],
)
def test_easter(year, expected):
    assert easter_sunday(year) == expected


def test_state_specific_holidays():
    assert holiday_name(date(2027, 1, 6), "BW") == "Heilige Drei Könige"
    assert holiday_name(date(2027, 1, 6), "BE") is None
    assert holiday_name(date(2026, 10, 31), "SN") == "Reformationstag"
    assert holiday_name(date(2026, 10, 31), "BW") is None
    assert holiday_name(date(2026, 11, 18), "SN") == "Buß- und Bettag"
    assert holiday_name(date(2026, 6, 4), "BW") == "Fronleichnam"
    assert holiday_name(date(2026, 3, 8), "BE") == "Internationaler Frauentag"
    assert holiday_name(date(2017, 10, 31), "BW") == "Reformationstag"  # one-off nationwide
    assert len(public_holidays(2026, "BW")) == 12


def test_unknown_state():
    with pytest.raises(ValueError):
        public_holidays(2026, "XX")


def test_next_working_day_skips_weekend_and_holiday():
    assert next_working_day(date(2027, 10, 30), "BW") == date(2027, 11, 2)  # Sat, Sun, Allerheiligen
    assert next_working_day(date(2027, 10, 30), "BE") == date(2027, 11, 1)


def test_add_months_end_of_month():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)
    assert add_months(date(2026, 10, 19), 12) == date(2027, 10, 19)


def test_four_day_rule_from_2025_and_weekend_shift():
    steps = []
    assert notification_date(date(2026, 10, 13), "post", "BW", steps) == date(2026, 10, 19)
    assert "day 4" in steps[0]


def test_three_day_rule_before_2025():
    steps = []
    assert notification_date(date(2024, 12, 27), "post", "BW", steps) == date(2024, 12, 30)
    assert "day 3" in steps[0]


def _dl(**rule):
    return ExtractedDeadline(id="x", purpose="p", kind="right_to_object", rule=DeadlineRule(**rule), quote="q")


def test_objection_deadline_for_tax_decision():
    r = resolve_deadline(_dl(kind="after_notification", months=1), letter_date=date(2026, 10, 13), received_on=None, state="BW", today=TODAY)
    assert r.due_date == date(2026, 11, 19)
    assert r.days_left == 22 and r.basis == "computed" and r.status == "open"


def test_month_end_period_lands_on_weekend():
    r = resolve_deadline(_dl(kind="after_receipt", months=1), letter_date=None, received_on=date(2026, 1, 31), state="BW", today=TODAY)
    assert r.due_date == date(2026, 3, 2)  # 28.02.2026 is a Saturday


def test_holiday_in_target_state_moves_deadline():
    bw = resolve_deadline(_dl(kind="after_receipt", months=1), letter_date=None, received_on=date(2026, 12, 6), state="BW", today=TODAY)
    be = resolve_deadline(_dl(kind="after_receipt", months=1), letter_date=None, received_on=date(2026, 12, 6), state="BE", today=TODAY)
    assert bw.due_date == date(2027, 1, 7)  # 06.01.2027 Heilige Drei Könige in BW
    assert be.due_date == date(2027, 1, 6)


def test_after_receipt_is_estimated_without_receipt_date():
    r = resolve_deadline(_dl(kind="after_receipt", days=30), letter_date=date(2026, 10, 26), received_on=None, state="BW", today=TODAY)
    assert r.due_date == date(2026, 11, 30) and r.basis == "estimated"


def test_later_receipt_keeps_safer_earlier_date():
    r = resolve_deadline(_dl(kind="after_notification", months=1), letter_date=date(2026, 10, 13), received_on=date(2026, 10, 24), state="BW", today=TODAY)
    assert r.due_date == date(2026, 11, 19)
    assert any("earlier date is used" in s for s in r.steps)


def test_missing_letter_date_gives_unknown():
    r = resolve_deadline(_dl(kind="after_notification", months=1), letter_date=None, received_on=None, state="BW", today=TODAY)
    assert r.due_date is None and r.status == "unknown"


def test_fixed_date_overdue_and_urgency():
    r = resolve_deadline(_dl(kind="fixed_date", fixed_date=date(2026, 10, 20)), letter_date=None, received_on=None, state="BW", today=TODAY)
    assert r.status == "overdue" and r.days_left == -8
    assert urgency([r]) == "overdue"
    assert urgency([]) == "none"
