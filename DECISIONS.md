# DECISIONS.md

1. **Indexes.** A unique `employees.emp_code` index protects employee identity. A unique `(emp_code, date)` index is the final guard against duplicate attendance. Department/join-date and date/employee indexes support department and listing queries. I avoided indexing every field because excess indexes slow writes and consume storage.
2. **Punch-in race.** Both requests may reach the insert, but the unique compound index permits only one document for the same employee/date. The successful request returns 201; the duplicate-key error becomes 409. A pre-check alone would not be safe.
3. **Ties.** The leaderboard uses `$setWindowFields` with `$rank`, then filters on `rank <= limit`. Employees tied at the cutoff are all returned, ordered by employee code within the tie.
4. **Headcount.** Department summary starts from eligible employees and looks up their logs. That preserves employees who have no attendance documents, while `joined_on <= month_end` excludes future joiners.
5. **100x scale.** I would add production-grade observability and load tests, inspect real execution statistics, and consider pre-aggregated daily summaries or a reporting store if live pipelines became too expensive. I would retain the raw audit trail.
