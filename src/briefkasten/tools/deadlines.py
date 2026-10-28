"""Deterministic deadline calculator.

The LLM only *extracts* the rule ("one month after notification"); this module does
the date arithmetic, so a model can never miscount days. Each result carries the
steps it took, in plain English, so the user can check the reasoning.

Rules implemented:
- Notification fiction for decisions sent by post or electronically: deemed delivered on
  day 4 after the letter date (3 days for letters dated before 2025-01-01), e.g.
  § 122(2) AO, § 41(2) VwVfG, § 37(2) SGB X as changed by the Postrechtsmodernisierungsgesetz.
- If that day is a Saturday, Sunday or public holiday it moves to the next working day
  (§ 108(3) AO; the courts apply the same idea to other agencies).
- Periods: §§ 187(1), 188(2)/(3) BGB (a month ends on the same day number, or the last
  day of a shorter month).
- A period ending on a weekend or holiday ends on the next working day (§ 193 BGB, § 108(3) AO).

Where the law leaves room (actual later receipt, missing letter date) the calculator
chooses the earlier, safer date and says so.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from ..models import ExtractedDeadline, ResolvedDeadline
from .holidays_de import holiday_name, next_working_day

FOUR_DAY_RULE_FROM = date(2025, 1, 1)


def fmt(d: date) -> str:
    return d.strftime("%a %d.%m.%Y")


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _why_not_working(d: date, state: str) -> str:
    name = holiday_name(d, state)
    if name:
        return f"a public holiday ({name})"
    return "a Saturday" if d.weekday() == 5 else "a Sunday"


def _roll_forward(d: date, state: str, steps: list[str], rule: str) -> date:
    n = next_working_day(d, state)
    if n != d:
        skipped, cur = [], d
        while cur < n:
            skipped.append(f"{fmt(cur)} is {_why_not_working(cur, state)}")
            cur += timedelta(days=1)
        steps.append(f"{'; '.join(skipped)}. So it moves to the next working day: {fmt(n)} ({rule}).")
    return n


def notification_date(letter_date: date, delivery: str, state: str, steps: list[str]) -> date:
    """Day an official decision legally counts as delivered (Bekanntgabe)."""
    if delivery == "hand":
        steps.append(f"Handed over in person, so it counts as delivered on the letter date, {fmt(letter_date)}.")
        return letter_date
    days = 4 if letter_date >= FOUR_DAY_RULE_FROM else 3
    how = "sent electronically" if delivery == "electronic" else "sent by post"
    d = letter_date + timedelta(days=days)
    steps.append(
        f"Letter dated {fmt(letter_date)}, {how}: by law it counts as delivered on day {days} after that, {fmt(d)}."
    )
    return _roll_forward(d, state, steps, "§ 108(3) AO")


def estimated_receipt(letter_date: date, state: str, steps: list[str]) -> date:
    d = next_working_day(letter_date + timedelta(days=4), state)
    steps.append(
        f"Receipt date unknown: estimated as {fmt(d)} (about 4 days after the letter date). "
        "Give the real date with --received for an exact result."
    )
    return d


def add_period(start: date, months: int, weeks: int, days: int, steps: list[str]) -> date:
    end = add_months(start, months) if months else start
    end += timedelta(weeks=weeks, days=days)
    parts = [f"{n} {unit}{'s' if n != 1 else ''}" for n, unit in ((months, "month"), (weeks, "week"), (days, "day")) if n]
    steps.append(f"{' + '.join(parts) or '0 days'} counted from {fmt(start)} ends on {fmt(end)} (§§ 187, 188 BGB).")
    return end


def _status(due: date | None, today: date) -> tuple[int | None, str]:
    if due is None:
        return None, "unknown"
    left = (due - today).days
    return left, "overdue" if left < 0 else "due_today" if left == 0 else "open"


def resolve_deadline(
    dl: ExtractedDeadline,
    *,
    letter_date: date | None,
    received_on: date | None,
    state: str,
    today: date,
) -> ResolvedDeadline:
    steps: list[str] = []
    rule = dl.rule
    due: date | None = None
    basis = "unknown"

    if rule.kind == "fixed_date":
        if rule.fixed_date:
            due, basis = rule.fixed_date, "stated"
            steps.append(f"The letter states the date directly: {fmt(due)}.")
        else:
            steps.append("The letter mentions a deadline but no date could be read.")
    elif rule.kind == "after_notification":
        if letter_date is None:
            steps.append("No letter date found, so the start of this period cannot be computed. Check the letter.")
        else:
            start = notification_date(letter_date, rule.delivery, state, steps)
            if received_on and received_on > start:
                steps.append(
                    f"You received it on {fmt(received_on)}, later than the legal delivery date. A later receipt "
                    "can push the deadline back, but you may have to prove it, so the earlier date is used here."
                )
            elif received_on and received_on < start:
                steps.append(f"You received it on {fmt(received_on)}, but the legal delivery date still applies.")
            end = add_period(start, rule.months, rule.weeks, rule.days, steps)
            due, basis = _roll_forward(end, state, steps, "§ 193 BGB / § 108(3) AO"), "computed"
    else:  # after_receipt
        if received_on:
            start, basis = received_on, "computed"
            steps.append(f"You received the letter on {fmt(received_on)}.")
        elif letter_date:
            start, basis = estimated_receipt(letter_date, state, steps), "estimated"
        else:
            start = None
            steps.append("Neither the letter date nor the receipt date is known; give --received to compute it.")
        if start:
            end = add_period(start, rule.months, rule.weeks, rule.days, steps)
            due = _roll_forward(end, state, steps, "§ 193 BGB")

    days_left, status = _status(due, today)
    return ResolvedDeadline(
        id=dl.id,
        purpose=dl.purpose,
        kind=dl.kind,
        due_date=due,
        days_left=days_left,
        status=status,
        basis=basis if due else "unknown",
        steps=steps,
        quote=dl.quote,
    )


def urgency(deadlines: list[ResolvedDeadline]) -> str:
    open_days = [d.days_left for d in deadlines if d.days_left is not None]
    if not open_days:
        return "none"
    soonest = min(open_days)
    if soonest < 0:
        return "overdue"
    if soonest <= 7:
        return "urgent"
    if soonest <= 21:
        return "soon"
    return "relaxed"
