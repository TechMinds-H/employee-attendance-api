"""Employee Attendance & Analytics API, contract v2.0.

Application implementation intentionally lives in this file to match the assignment.
"""
import calendar
import json
import math
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError
from contextlib import asynccontextmanager
from bson import json_util

load_dotenv()
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), serverSelectionTimeoutMS=1500)
db = client[os.getenv("MONGO_DB", "attendance_db")]
employees = db.employees
attendance = db.attendance_logs
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
PRESENCE = {"PRESENT", "WFH", "ON_DUTY"}
STATUSES = PRESENCE | {"ABSENT", "LEAVE"}
MIN_TS = 100_000_000_000
MAX_TS = 4_102_444_800_000



@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create database indexes on application startup
    employees.create_index([("emp_code", ASCENDING)], unique=True, name="uq_employee_code")
    employees.create_index([("department", ASCENDING), ("emp_code", ASCENDING)], name="department_employee_code")
    employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)], name="department_joined_on")
    attendance.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="uq_attendance_employee_date")
    attendance.create_index([("date", DESCENDING), ("emp_code", ASCENDING)], name="attendance_date_employee")
    attendance.create_index([("emp_code", ASCENDING), ("date", ASCENDING), ("status", ASCENDING)], name="attendance_employee_date_status")
    attendance.create_index([("date", ASCENDING), ("status", ASCENDING), ("emp_code", ASCENDING)], name="attendance_date_status_employee")
    yield
app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")



def fail422(message: str):
    raise HTTPException(status_code=422, detail=message)


def parse_date(value: str, label: str = "date") -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        fail422(f"{label} must be YYYY-MM-DD")


def parse_month(value: str) -> tuple[date, date]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value or ""):
        fail422("month must be YYYY-MM")
    year, month = map(int, value.split("-"))
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    return first, last


def validate_ts(value):
    if type(value) is not int or not MIN_TS <= value <= MAX_TS:
        fail422("timestamp must be integer epoch milliseconds")
    # Contract says truncate to whole seconds before calculation/storage.
    return datetime.fromtimestamp((value // 1000), tz=UTC).replace(microsecond=0)


def now_utc():
    return datetime.now(UTC).replace(microsecond=0)


def as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def epoch_ms(value):
    value = as_utc(value)
    return None if value is None else int(value.timestamp() * 1000)


def serialize_doc(doc, employee=False):
    if not doc:
        return doc
    out = {k: v for k, v in doc.items() if k != "_id"}
    if employee:
        if "created_at" in out:
            out["created_at"] = epoch_ms(out["created_at"])
        return out
    for key in ("punch_in", "punch_out"):
        out[key] = epoch_ms(out.get(key))
    out.setdefault("work_hours", None)
    out.setdefault("late_minutes", 0)
    out.setdefault("overtime_minutes", 0)
    out.setdefault("half_day", False)
    out.setdefault("history", [])
    for entry in out["history"]:
        if "at" in entry:
            entry["at"] = epoch_ms(entry["at"])
        for change in entry.get("changes", {}).values():
            for side in ("from", "to"):
                if isinstance(change.get(side), datetime):
                    change[side] = epoch_ms(change[side])
    return out


def shift_instant(day: date, shift: str) -> datetime:
    h, m = map(int, shift.split(":"))
    return datetime.combine(day, time(h, m), tzinfo=IST).astimezone(UTC)


def attendance_date(punch_in: datetime, emp: dict) -> str:
    local = as_utc(punch_in).astimezone(IST)
    d = local.date()
    if emp["shift_end"] <= emp["shift_start"]:
        eh, em = map(int, emp["shift_end"].split(":"))
        if (local.hour, local.minute) < (eh, em):
            d -= timedelta(days=1)
    return d.isoformat()


def compute_late_minutes(punch_in: datetime, shift_start: str, record_date: str | None = None, shift_end: str | None = None) -> int:
    punch = as_utc(punch_in).replace(microsecond=0)
    local = punch.astimezone(IST)
    d = parse_date(record_date) if record_date else local.date()
    start = shift_instant(d, shift_start)
    # For a punch in the early hours of an overnight shift, attendance_date is the prior day.
    delta = (punch - start).total_seconds()
    mins = math.floor(delta / 60)
    return mins if delta > 600 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    hours = Decimal(str((as_utc(punch_out) - as_utc(punch_in)).total_seconds())) / Decimal(3600)
    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(punch_out: datetime, shift_end: str, record_date: str, shift_start: str | None = None) -> int:
    d = parse_date(record_date)
    h, m = map(int, shift_end.split(":"))
    end_day = d + timedelta(days=1) if (shift_start is not None and shift_end <= shift_start) else d
    end = datetime.combine(end_day, time(h, m), tzinfo=IST).astimezone(UTC)
    minutes = math.floor((as_utc(punch_out) - end).total_seconds() / 60)
    return minutes if minutes >= 30 else 0


def round_decimal(value, places=2):
    if value is None:
        return None
    quantum = Decimal("1") if places == 0 else Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def page_validation(page: int, page_size: int):
    if page < 1:
        fail422("page must be >= 1")
    if page_size < 1 or page_size > 100:
        fail422("page_size must be between 1 and 100")


def valid_shift(value: str):
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value or ""):
        fail422("shift time must be HH:MM")
    return value


class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("shift_start", "shift_end")
    @classmethod
    def shift_format(cls, v): return valid_shift(v)

    @field_validator("joined_on")
    @classmethod
    def join_date(cls, v): parse_date(v, "joined_on"); return v

    @model_validator(mode="after")
    def shifts_differ(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


class PunchInIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    emp_code: str
    punched_at: Optional[StrictInt] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

    @field_validator("punched_at")
    @classmethod
    def ts(cls, v):
        if v is not None and (type(v) is not int or not MIN_TS <= v <= MAX_TS):
            raise ValueError("punched_at must be epoch milliseconds")
        return v


class PunchOutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    emp_code: str
    punched_at: Optional[StrictInt] = None

    @field_validator("punched_at")
    @classmethod
    def ts(cls, v):
        if v is not None and (type(v) is not int or not MIN_TS <= v <= MAX_TS):
            raise ValueError("punched_at must be epoch milliseconds")
        return v


class RegularizeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None
    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @field_validator("punch_in", "punch_out", mode="before")
    @classmethod
    def ts(cls, v):
        if v is None:
            raise ValueError("punch timestamps must be integer epoch milliseconds when supplied")
        if type(v) is not int or not MIN_TS <= v <= MAX_TS:
            raise ValueError("punch timestamps must be epoch milliseconds")
        return v


def get_employee_or_404(emp_code):
    emp = employees.find_one({"emp_code": emp_code}, {"_id": 0})
    if not emp:
        raise HTTPException(404, "employee not found")
    return emp


@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(503, "database unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = now_utc()
    try:
        employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    return serialize_doc(doc, employee=True)


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    q = {"department": department} if department is not None else {}
    total = employees.count_documents(q)
    items = [serialize_doc(x, employee=True) for x in employees.find(q, {"_id": 0}).sort("emp_code", ASCENDING).skip((page - 1) * page_size).limit(page_size)]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = get_employee_or_404(body.emp_code)
    ts = validate_ts(body.punched_at) if body.punched_at is not None else now_utc()
    day = attendance_date(ts, emp)
    doc = {"emp_code": body.emp_code, "date": day, "status": body.status, "punch_in": ts,
           "punch_out": None, "work_hours": None,
           "late_minutes": compute_late_minutes(ts, emp["shift_start"], day, emp["shift_end"]),
           "overtime_minutes": 0, "half_day": False, "history": []}
    try:
        attendance.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this attendance date")
    return serialize_doc(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = get_employee_or_404(body.emp_code)
    ts = validate_ts(body.punched_at) if body.punched_at is not None else now_utc()
    # Find latest eligible punch-in; records are addressed by natural key.
    candidate = attendance.find_one({"emp_code": body.emp_code, "punch_in": {"$ne": None, "$lte": ts}}, sort=[("punch_in", DESCENDING)])
    if not candidate:
        raise HTTPException(404, "no eligible punch-in found")
    punch_start = as_utc(candidate["punch_in"])
    elapsed = (ts - punch_start).total_seconds()
    if elapsed <= 0 or elapsed > 24 * 3600:
        fail422("punched_at must be after punch_in and within 24 hours")
    if candidate.get("punch_out") is not None:
        raise HTTPException(409, "attendance record is already punched out")
    hours = compute_work_hours(punch_start, ts)
    overtime = compute_overtime(ts, emp["shift_end"], candidate["date"], emp["shift_start"])
    update = {"punch_out": ts, "work_hours": hours, "overtime_minutes": overtime, "half_day": hours < 4.50}
    result = attendance.update_one({"_id": candidate["_id"], "punch_out": None}, {"$set": update})
    if result.modified_count != 1:
        raise HTTPException(409, "attendance record was already punched out")
    fresh = attendance.find_one({"_id": candidate["_id"]})
    return serialize_doc(fresh)


@app.get("/attendance")
def list_attendance(emp_code: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None,
                    status: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    q = {}
    if emp_code is not None: q["emp_code"] = emp_code
    if date_from is not None: parse_date(date_from, "date_from")
    if date_to is not None: parse_date(date_to, "date_to")
    if date_from and date_to and date_from > date_to: fail422("date_from must be <= date_to")
    if date_from or date_to:
        q["date"] = {}
        if date_from: q["date"]["$gte"] = date_from
        if date_to: q["date"]["$lte"] = date_to
    if status is not None:
        if status not in STATUSES: fail422("invalid status")
        q["status"] = status
    total = attendance.count_documents(q)
    cursor = attendance.find(q, {"_id": 0}).sort([("date", DESCENDING), ("emp_code", ASCENDING)]).skip((page - 1) * page_size).limit(page_size)
    return {"items": [serialize_doc(x) for x in cursor], "total": total, "page": page, "page_size": page_size}


@app.patch("/attendance/{emp_code}/{date}")
def regularize(emp_code: str, date: str, body: RegularizeIn):
    parse_date(date)
    emp = get_employee_or_404(emp_code)
    old = attendance.find_one({"emp_code": emp_code, "date": date})
    if not old: raise HTTPException(404, "attendance record not found")
    requested = body.model_dump(exclude_unset=True)
    status = requested.get("status", old.get("status"))
    if status in {"ABSENT", "LEAVE"}:
        if requested.get("punch_in") is not None or requested.get("punch_out") is not None:
            fail422("ABSENT and LEAVE cannot have punch times")
        pin = pout = None
    else:
        pin = validate_ts(requested["punch_in"]) if "punch_in" in requested and requested["punch_in"] is not None else as_utc(old.get("punch_in"))
        pout = validate_ts(requested["punch_out"]) if "punch_out" in requested and requested["punch_out"] is not None else as_utc(old.get("punch_out"))
        if pin is None: fail422("presence status requires punch_in")
        if attendance_date(pin, emp) != date: fail422("punch_in must belong to attendance date")
        if pout is not None:
            elapsed = (pout - pin).total_seconds()
            if elapsed <= 0 or elapsed > 86400: fail422("punch_out must be after punch_in and within 24 hours")
    late = compute_late_minutes(pin, emp["shift_start"], date, emp["shift_end"]) if pin else 0
    if pout:
        hours = compute_work_hours(pin, pout)
        overtime = compute_overtime(pout, emp["shift_end"], date, emp["shift_start"])
        half = hours < 4.50
    else:
        hours, overtime, half = None, 0, False
    new_values = {"status": status, "punch_in": pin, "punch_out": pout, "work_hours": hours,
                  "late_minutes": late, "overtime_minutes": overtime, "half_day": half}
    changes = {}
    for key, new in new_values.items():
        defaults = {"late_minutes": 0, "overtime_minutes": 0, "half_day": False, "work_hours": None,
                    "punch_in": None, "punch_out": None}
        previous = old.get(key, defaults.get(key))
        if key in {"punch_in", "punch_out"}:
            previous = as_utc(previous) if previous else None
        if previous != new:
            changes[key] = {"from": previous, "to": new}
    if not changes: fail422("request does not change the record")
    entry = {"at": now_utc(), "by": body.regularized_by, "reason": body.reason, "changes": changes}
    # Optimistic concurrency: only update if the values observed are still the current values.
    expected = {"emp_code": emp_code, "date": date, "status": old.get("status"), "punch_in": old.get("punch_in"), "punch_out": old.get("punch_out")}
    result = attendance.update_one(expected, {"$set": new_values, "$push": {"history": entry}})
    if result.modified_count != 1: raise HTTPException(409, "record changed concurrently; retry regularization")
    return serialize_doc(attendance.find_one({"emp_code": emp_code, "date": date}))


def month_match(month: str):
    first, last = parse_month(month)
    return first.isoformat(), last.isoformat(), first, last


def weekdays_between(start: date, end: date) -> int:
    if start > end: return 0
    days = (end - start).days + 1
    weeks, rem = divmod(days, 7)
    count = weeks * 5
    for i in range(rem):
        if (start + timedelta(days=weeks * 7 + i)).weekday() < 5: count += 1
    return count


def attendance_base_match(first_s, last_s, emp_code=None):
    q = {"date": {"$gte": first_s, "$lte": last_s}}
    if emp_code is not None: q["emp_code"] = emp_code
    return q


def employee_month_pipeline(emp_code, month):
    first_s, last_s, first, last = month_match(month)
    # The aggregation reads stored derived values. Weekday count is a calendar scalar; all attendance totals are in MongoDB.
    return [
        {"$match": attendance_base_match(first_s, last_s, emp_code)},
        {"$group": {"_id": None,
            "present_days": {"$sum": {"$cond": [{"$in": ["$status", list(PRESENCE)]}, {"$cond": [{"$and": [{"$eq": [{"$ifNull": ["$half_day", False]}, True]}, {"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, [2,3,4,5,6]]}]}, 0.5, {"$cond": [{"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, [2,3,4,5,6]]}, 1, 0]}]}, 0]}},
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}}
        }}
    ]


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    emp = get_employee_or_404(emp_code)
    first_s, last_s, first, last = month_match(month)
    start = max(first, parse_date(emp["joined_on"], "joined_on"))
    working_days = weekdays_between(start, last)
    result = list(attendance.aggregate(employee_month_pipeline(emp_code, month)))
    row = result[0] if result else {}
    present = round_decimal(row.get("present_days", 0), 2)
    pct = round_decimal(present / working_days * 100, 4) if working_days else None
    return {"emp_code": emp_code, "month": month, "working_days": working_days, "present_days": present,
            "leave_days": row.get("leave_days", 0), "late_count": row.get("late_count", 0),
            "total_late_minutes": row.get("total_late_minutes", 0), "total_overtime_minutes": row.get("total_overtime_minutes", 0),
            "attendance_pct": pct}


def department_summary_pipeline(month, department=None):
    first_s, last_s, first, last = month_match(month)
    match = {"joined_on": {"$lte": last_s}}
    if department is not None: match["department"] = department
    return [
        {"$match": match},
        {"$lookup": {"from": "attendance_logs", "let": {"code": "$emp_code"}, "pipeline": [
            {"$match": {"$expr": {"$and": [{"$eq": ["$emp_code", "$$code"]}, {"$gte": ["$date", first_s]}, {"$lte": ["$date", last_s]}]}}}
        ], "as": "logs"}},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$group": {"_id": "$department", "headcount_codes": {"$addToSet": "$emp_code"},
            "present_days": {"$sum": {"$cond": [{"$and": [{"$in": ["$logs.status", list(PRESENCE)]}, {"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": {"$ifNull": ["$logs.date", "2000-01-01"]}}}}, [2,3,4,5,6]]}]}, {"$cond": [{"$eq": [{"$ifNull": ["$logs.half_day", False]}, True]}, 0.5, 1]}, 0]}},
            "work_hours_values": {"$push": {"$cond": [{"$and": [{"$in": ["$logs.status", list(PRESENCE)]}, {"$ne": ["$logs.work_hours", None]}]}, "$logs.work_hours", None]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$logs.late_minutes", 0]}},
            "leave_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}},
            "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}}
        }},
        {"$set": {"headcount": {"$size": "$headcount_codes"}, "work_hours_values": {"$filter": {"input": "$work_hours_values", "as": "v", "cond": {"$ne": ["$$v", None]}}}}},
        {"$match": {"headcount": {"$gt": 0}}},
        {"$project": {"_id": 0, "department": "$_id", "headcount": 1, "present_days": 1,
            "avg_work_hours": {"$cond": [{"$gt": [{"$size": "$work_hours_values"}, 0]}, {"$divide": [{"$floor": {"$add": [{"$multiply": [{"$avg": "$work_hours_values"}, 100]}, 0.5]}}, 100]}, None]},
            "late_count": 1, "total_late_minutes": 1, "leave_count": 1, "on_duty_count": 1}},
        {"$sort": {"department": 1}}
    ]


@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    month_match(month)
    return {"month": month, "items": list(employees.aggregate(department_summary_pipeline(month, department)))}


def leaderboard_pipeline(month, department=None):
    first_s, last_s, _, _ = month_match(month)
    pipeline = [
        {"$match": {"date": {"$gte": first_s, "$lte": last_s}, "late_minutes": {"$gt": 0}}},
        {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}}, "late_count": {"$sum": 1}}},
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"},
    ]
    if department is not None:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline += [
        {"$setWindowFields": {"sortBy": {"total_late_minutes": -1}, "output": {"rank": {"$rank": {}}}}},
        {"$match": {"rank": {"$lte": "$$limit_placeholder"}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {"$project": {"_id": 0, "rank": 1, "emp_code": "$_id", "name": "$employee.name", "department": "$employee.department", "total_late_minutes": 1, "late_count": 1}}
    ]
    return pipeline


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str, limit: int = Query(10, ge=1, le=50), department: Optional[str] = None):
    month_match(month)
    pipeline = leaderboard_pipeline(month, department)
    pipeline[-3] = {"$match": {"rank": {"$lte": limit}}}
    return {"month": month, "items": list(attendance.aggregate(pipeline))}


def trend_pipeline(department, from_date, to_date):
    start = parse_date(from_date, "from")
    end = parse_date(to_date, "to")
    days = (end - start).days + 1
    # MongoDB generates the date spine; there is no Python per-day loop.
    return [
        {"$documents": [{"seed": 1}]},
        {"$set": {"offsets": {"$range": [0, days]}}},
        {"$unwind": "$offsets"},
        {"$set": {"date": {"$dateToString": {"date": {"$dateAdd": {"startDate": {"$dateFromString": {"dateString": from_date}}, "unit": "day", "amount": "$offsets"}}, "format": "%Y-%m-%d"}}}},
        {"$lookup": {"from": "employees", "let": {"day": "$date"}, "pipeline": [
            {"$match": {"department": department, "$expr": {"$lte": ["$joined_on", "$$day"]}}},
            {"$project": {"_id": 0, "emp_code": 1}}
        ], "as": "eligible"}},
        {"$set": {"headcount": {"$size": "$eligible"}, "is_working_day": {"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, [2,3,4,5,6]]}}},
        {"$lookup": {"from": "attendance_logs", "let": {"day": "$date", "codes": "$eligible.emp_code"}, "pipeline": [
            {"$match": {"$expr": {"$and": [{"$eq": ["$date", "$$day"]}, {"$in": ["$emp_code", "$$codes"]}]}}},
            {"$group": {"_id": None,
                "present_count": {"$sum": {"$cond": [{"$in": ["$status", list(PRESENCE)]}, {"$cond": [{"$eq": [{"$ifNull": ["$half_day", False]}, True]}, 0.5, 1]}, 0]}},
                "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}}
            }}
        ], "as": "day_logs"}},
        {"$set": {"present_count": {"$ifNull": [{"$arrayElemAt": ["$day_logs.present_count", 0]}, 0]}, "late_count": {"$ifNull": [{"$arrayElemAt": ["$day_logs.late_count", 0]}, 0]}}},
        {"$set": {"attendance_rate": {"$cond": [{"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]}, {"$divide": [{"$floor": {"$add": [{"$multiply": [{"$divide": ["$present_count", "$headcount"]}, 10000]}, 0.5]}}, 10000]}, None]}}},
        {"$setWindowFields": {"sortBy": {"date": 1}, "output": {"moving_avg_7d": {"$avg": "$attendance_rate", "window": {"documents": [-6, 0]}}}}},
        {"$project": {"_id": 0, "date": 1, "is_working_day": 1, "headcount": 1, "present_count": 1, "late_count": 1, "attendance_rate": 1, "moving_avg_7d": {"$cond": [{"$eq": ["$moving_avg_7d", None]}, None, {"$divide": [{"$floor": {"$add": [{"$multiply": ["$moving_avg_7d", 10000]}, 0.5]}}, 10000]}]}}},
        {"$sort": {"date": 1}}
    ]


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_: str = Query(..., alias="from"), to: str = Query(...)):
    start = parse_date(from_, "from")
    end = parse_date(to, "to")
    if end < start: fail422("to must be on or after from")
    if (end - start).days + 1 > 92: fail422("date range must not exceed 92 days")
    if not employees.find_one({"department": department}, {"_id": 1}): raise HTTPException(404, "department not found")
    # $documents is a database-level aggregation stage; execute on the database rather than a collection.
    command = {"aggregate": 1, "pipeline": trend_pipeline(department, from_, to), "cursor": {}}
    try:
        raw = db.command(command)
        items = list(raw["cursor"]["firstBatch"])
    except PyMongoError as exc:
        raise HTTPException(500, f"trend aggregation failed: {exc}")
    return {"department": department, "items": items}


def json_safe(value):
    return json.loads(json_util.dumps(value))


def explain_find(collection, query, sort, page, page_size):
    return collection.find(query).sort(sort).skip((page - 1) * page_size).limit(page_size).explain("executionStats")


def explain_aggregate(collection, pipeline):
    return collection.database.command({"explain": {"aggregate": collection.name, "pipeline": pipeline, "cursor": {}}, "verbosity": "executionStats"})


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint: str, emp_code: Optional[str] = None, month: Optional[str] = None,
                     department: Optional[str] = None, limit: int = Query(10, ge=1, le=50),
                     date_from: Optional[str] = None, date_to: Optional[str] = None, status: Optional[str] = None,
                     from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
                     page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    if endpoint == "attendance_list":
        q = {}
        if emp_code is not None: q["emp_code"] = emp_code
        if date_from: parse_date(date_from, "date_from"); q.setdefault("date", {})["$gte"] = date_from
        if date_to: parse_date(date_to, "date_to"); q.setdefault("date", {})["$lte"] = date_to
        if date_from and date_to and date_from > date_to: fail422("date_from must be <= date_to")
        if status:
            if status not in STATUSES: fail422("invalid status")
            q["status"] = status
        out = explain_find(attendance, q, [("date", DESCENDING), ("emp_code", ASCENDING)], page, page_size)
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": json_safe(out)}
    if endpoint == "employee_monthly":
        if not emp_code or not month: fail422("emp_code and month are required")
        month_match(month)
        out = explain_aggregate(attendance, employee_month_pipeline(emp_code, month))
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": json_safe(out)}
    if endpoint == "department_summary":
        if not month: fail422("month is required")
        month_match(month)
        out = explain_aggregate(employees, department_summary_pipeline(month, department))
        return {"endpoint": endpoint, "collection": "employees", "explain": json_safe(out)}
    if endpoint == "late_leaderboard":
        if not month: fail422("month is required")
        month_match(month)
        pipeline = leaderboard_pipeline(month, department)
        pipeline[-3] = {"$match": {"rank": {"$lte": limit}}}
        out = explain_aggregate(attendance, pipeline)
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": json_safe(out)}
    if endpoint == "department_trend":
        if not department or not from_ or not to: fail422("department, from and to are required")
        start, end = parse_date(from_), parse_date(to)
        if end < start or (end - start).days + 1 > 92: fail422("invalid trend date range")
        # MongoDB explain for a database-level $documents aggregation.
        pipeline = trend_pipeline(department, from_, to)
        out = db.command({"explain": {"aggregate": 1, "pipeline": pipeline, "cursor": {}}, "verbosity": "executionStats"})
        return {"endpoint": endpoint, "collection": "database", "explain": json_safe(out)}
    fail422("unsupported endpoint")
