# Agri Scan project rules

Agri Scan is a rice-grain quality assessment DEMO. There is no physical
hardware. The camera and lighting box are simulated in software and must
always be labelled "simulated" in the UI and reports. Keep the code simple,
commented, and beginner-friendly.

## Fixed stack and layout

- Python 3.10+, OpenCV, NumPy, pandas, scikit-learn, joblib, matplotlib,
  FastAPI, Uvicorn, and SQLite.
- Frontend: plain HTML, CSS, and JavaScript with canvas; no build tools or
  frameworks. Everything must work offline; save libraries in
  `frontend/vendor/`.
- Do not rename or move the prescribed files and folders:
  `backend/`, `frontend/`, `validation/`, `data/`, and `docs/CONTRACT.md`.
- Follow `docs/CONTRACT.md` exactly. Do not change it, function names, JSON
  field names, or other people's files.

## Working rules

- Use Windows PowerShell and the project's `.venv`. Create it if missing.
- Add required packages to `requirements.txt`; do not change the stack.
- Run and test code after writing it. If a test fails, inspect the error, fix
  it, and run the test again.
- Edit only files that belong to the current task.
- Never delete files outside `data/`, run destructive commands, or disable
  security or SSL checks.
- Ask before installing anything not listed in the stack.
- Commit working code on the current branch with a clear message. Do not push
  to `main`.

At the end, state what was built, the exact command to run it, what was tested,
and anything still not working.
