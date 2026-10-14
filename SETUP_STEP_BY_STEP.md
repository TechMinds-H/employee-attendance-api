# Follow these steps on your Windows computer

## Step 1 — Extract the project

1. Download `employee_attendance_solution.zip` from this chat.
2. Right-click it and choose **Extract All**.
3. Open the extracted `candidate_kit` folder in VS Code using **File → Open Folder**.

## Step 2 — Check Python

Open **Terminal → New Terminal** in VS Code and run:

```powershell
py --version
```

Use Python 3.11 or newer. If the command is not found, install Python and reopen VS Code.

## Step 3 — Create and activate a virtual environment

Run these commands in the VS Code terminal, from the folder containing `requirements.txt`:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, use the VS Code Command Prompt terminal and run `.venv\Scripts\activate.bat` instead.

## Step 4 — Install packages

```powershell
python -m pip install -r requirements.txt
```

Wait until the command finishes. If installation fails, copy the full error and ask for help; do not continue to the run step yet.

## Step 5 — Start MongoDB

Make sure MongoDB 6.0+ is running locally, or create a MongoDB Atlas database. The API needs a working database connection.

Create a file named `.env` in the same folder as `requirements.txt` and put in your own connection settings:

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

For Atlas, replace the URI with your own Atlas connection string. Never upload `.env` to GitHub.

## Step 6 — Start the API

In the same terminal, run:

```powershell
uvicorn app.main:app --port 8000
```

Keep this terminal open. You should see Uvicorn say that it is running on port 8000.

## Step 7 — Check that it works

Open a browser at:

- `http://127.0.0.1:8000/health` — should show `{"status":"ok"}` if MongoDB is available.
- `http://127.0.0.1:8000/docs` — interactive API page where you can try endpoints.

## Step 8 — Load sample records (optional local testing)

Stop the API with `Ctrl+C` or open a second terminal, then run:

```powershell
python sample_seed.py
```

**Warning:** the sample script deletes existing documents from the two collections in the selected database before loading sample records. Use only a disposable development database, never your Atlas production database or the evaluator's database.

## Step 9 — Run the included helper tests

```powershell
python -m pytest -q
```

These are starter tests, not proof that every API contract and MongoDB aggregation has passed. Test the endpoints in `/docs` against your local database too.

## Step 10 — Before submitting

1. Read `REVIEW.md` and confirm you understand the listed bugs.
2. Read `DECISIONS.md` and rewrite any explanation in your own natural words if needed.
3. Test the required endpoints against MongoDB 6.0+.
4. Do not commit `.env`, `.venv`, credentials, or database dumps.
5. Be ready to explain the implementation in a live interview.
