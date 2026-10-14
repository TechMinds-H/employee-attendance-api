from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.main import (
    IST, compute_late_minutes, compute_overtime, compute_work_hours,
    attendance_date, weekdays_between, validate_ts,
)

UTC = timezone.utc


def ist(day, hour, minute, second=0):
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=IST).astimezone(UTC)


def test_late_grace_boundary():
    d = date(2026, 10, 13)
    assert compute_late_minutes(ist(d, 9, 40, 0), "09:30", d.isoformat()) == 0
    assert compute_late_minutes(ist(d, 9, 40, 1), "09:30", d.isoformat()) == 10
    assert compute_late_minutes(ist(d, 10, 15, 59), "09:30", d.isoformat()) == 45


def test_work_hours_half_up():
    start = datetime(2026, 10, 13, 0, 0, tzinfo=UTC)
    end = start + timedelta(seconds=3618)  # exactly 1.005 hours
    assert compute_work_hours(start, end) == 1.01


def test_overtime_minimum_threshold():
    d = date(2026, 10, 13)
    shift_end = ist(d, 18, 30)
    assert compute_overtime(shift_end + timedelta(minutes=29), "18:30", d.isoformat(), "09:30") == 0
    assert compute_overtime(shift_end + timedelta(minutes=30), "18:30", d.isoformat(), "09:30") == 30


def test_overnight_attendance_date():
    emp = {"shift_start": "22:00", "shift_end": "06:00"}
    punch = ist(date(2026, 10, 14), 1, 15)
    assert attendance_date(punch, emp) == "2026-10-13"
    assert compute_overtime(ist(date(2026, 10, 14), 6, 30), "06:00", "2026-10-13", "22:00") == 30


def test_weekday_count():
    assert weekdays_between(date(2026, 10, 5), date(2026, 10, 9)) == 5
    assert weekdays_between(date(2026, 10, 10), date(2026, 10, 9)) == 0


def test_timestamp_rejects_seconds_and_float():
    for value in (1783312500, 1783312500000.0, "1783312500000"):
        with pytest.raises(HTTPException) as exc:
            validate_ts(value)
        assert exc.value.status_code == 422
