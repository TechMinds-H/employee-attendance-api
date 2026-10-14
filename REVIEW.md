# REVIEW.md — verified starter defects

| # | Where | What is wrong | How to notice it | How it was fixed |
|---|---|---|---|---|
| 1 | `compute_late_minutes` | The starter uses the timestamp's existing timezone/naive wall clock and does not explicitly apply the record's IST shift date. | Use a UTC instant and compare it with a shift start in IST. | Convert the instant to UTC, construct the scheduled start in IST for the attendance date, then compare instants. |
| 2 | `compute_late_minutes` | It does not normalize timestamps to whole seconds before calculations. | Supply a timestamp with milliseconds around the 10-minute boundary. | `validate_ts` truncates epoch milliseconds to whole seconds at the API boundary. |
| 3 | `compute_late_minutes` | It can calculate the wrong scheduled start for overnight shifts. | Punch in after midnight for a 22:00–06:00 shift. | Derive the attendance date per R1 and use that date to construct shift start. |
| 4 | `compute_work_hours` | Python `round()` uses ties-to-even, not required half-up rounding. | Test a duration whose hour value rounds at an exact 0.005 boundary. | Use `Decimal` with `ROUND_HALF_UP`. |
| 5 | `compute_overtime` | It always places shift end on the attendance date. | Punch out the morning after a 22:00–06:00 shift. | Place shift end on the next day when `shift_end <= shift_start`. |
| 6 | `compute_overtime` | It counts any positive overtime, despite the 30-minute minimum. | Punch out 29 minutes after scheduled end. | Return overtime only when the floored duration is at least 30 minutes. |
| 7 | `GET /health` | It reports healthy without contacting MongoDB. | Stop MongoDB and call `/health`. | Ping MongoDB and return 503 on database failure. |
| 8 | `POST /employees` | Duplicate checking is check-then-insert and is not race-safe. | Send two creates with the same `emp_code` concurrently. | Enforce a unique index and translate `DuplicateKeyError` to 409. |
| 9 | `POST /employees` | Request fields are insufficiently validated; shift equality and date/time formats are unchecked. | Send malformed dates, invalid shift times, or identical shift start/end. | Add Pydantic validation and reject invalid inputs with 422. |
| 10 | `POST /employees` | `created_at` is stored from a naive local clock. | Compare creation values across hosts/timezones. | Store a UTC timestamp and serialize it as epoch milliseconds. |
| 11 | `GET /employees` | Pagination offset uses `page * page_size`, skipping page one. | Request page 1 with page size 20. | Use `(page - 1) * page_size`. |
| 12 | `GET /employees` | `total` counts the entire collection rather than the filtered department. | Filter a department and compare `total` with matching records. | Count using the same query as the listing. |
| 13 | `GET /employees` | Sorting and pagination bounds are not enforced. | Request page size 101 or inspect order. | Sort by `emp_code` ascending and constrain page size to 1–100. |
| 14 | `POST /attendance/punch-in` | An unknown employee causes an unhandled error when shift data is accessed. | Punch in using an unknown code. | Return 404 before reading shift fields. |
| 15 | `POST /attendance/punch-in` | Timestamp conversion uses machine-local time and treats zero/missing values with truthiness. | Run on a non-UTC machine or submit invalid timestamp values. | Validate integer epoch milliseconds, distinguish omitted from supplied, and convert explicitly to UTC. |
| 16 | `POST /attendance/punch-in` | Attendance date and lateness do not account for IST and overnight shifts. | Punch in after midnight for an overnight shift. | Apply R1 before calculating date and R2. |
| 17 | `POST /attendance/punch-in` | The starter does not validate the allowed punch-in statuses. | Submit `ABSENT` as a punch-in status. | Restrict punch-in to `PRESENT`, `WFH`, or `ON_DUTY`. |
| 18 | `POST /attendance/punch-in` | Check-then-insert allows concurrent duplicate records. | Send parallel punch-ins for one employee/date. | Use the unique `(emp_code, date)` index as the final concurrency guard. |
| 19 | `GET /attendance` | It loads all matching records into Python before sorting and paginating. | Seed many records and observe memory/time growth. | Apply filters, sort, skip, limit, and count in MongoDB. |
| 20 | `GET /attendance` | It sorts only by date and does not apply the required employee-code tie-breaker. | Have multiple employees with the same date. | Sort by date descending and `emp_code` ascending. |
| 21 | `GET /attendance` | It does not validate date formats/ranges or page-size bounds. | Submit reversed dates or invalid page sizes. | Validate dates, enforce `date_from <= date_to`, and validate pagination. |
| 22 | `GET /attendance` | It exposes a synthetic `id` derived from `_id`, contrary to the natural-key contract. | Inspect the response fields. | Remove `_id` and return the documented attendance fields only. |
| 23 | Missing punch-out endpoint | Work hours, overtime, half-day, and concurrent closure were not implemented. | Call the documented endpoint or send concurrent punch-outs. | Implement conditional atomic update and the R3–R5 calculations. |
| 24 | Missing regularization endpoint | Manual correction and immutable audit history were absent. | PATCH a record and inspect its history. | Recompute derived values and atomically append one audit entry. |
| 25 | Missing analytics endpoints | Monthly, department, leaderboard, and trend analytics were absent. | Call the documented analytics paths. | Implement aggregation pipelines using stored derived fields. |
| 26 | Missing explain endpoint | There was no way to inspect query execution plans. | Call `/admin/explain/...`. | Return MongoDB `executionStats` explain output for supported query shapes. |

## Reviewed and intentionally retained

- The natural attendance key is `(emp_code, date)`; MongoDB `_id` remains internal and is not part of the API.
- `history` is only for manual corrections, so punch-in and punch-out do not append history entries.
- Missing seeded `history` and `half_day` values are treated as `[]` and `false` respectively.
- A missing weekday record is not an `ABSENT` record; analytics must not silently synthesize absence records.
