# Employee Attendance & Analytics API

FastAPI + PyMongo implementation for the HROne attendance assignment. Application code is in `app/main.py`, as required by the contract.

## Requirements

- Python 3.11+
- MongoDB 6.0+

## Setup (Windows PowerShell)

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create a local `.env` file in the repository root (do not commit it):

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

Real environment variables take precedence over `.env`. Indexes are created automatically at startup.

## Run

```powershell
uvicorn app.main:app --port 8000
```

Open `http://127.0.0.1:8000/docs` for interactive API documentation. `/health` checks MongoDB connectivity.

## Sample data

After confirming MongoDB is running and the variables are set:

```powershell
python sample_seed.py
```

Only use the seed script on a development database. The evaluator supplies its own database and dataset.

## Tests

Install pytest and run `pytest -q`. Tests cover deterministic helper calculations; API/integration and concurrency behavior should also be exercised against a running MongoDB instance.

## Notes

Analytics are implemented using MongoDB aggregation pipelines. Query plans can be inspected with `/admin/explain/{endpoint}`. Before submission, test every contract endpoint against MongoDB 6.0+ and review `REVIEW.md` and `DECISIONS.md` so you can explain the code in a live walkthrough.
