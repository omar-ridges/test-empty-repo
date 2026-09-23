# Getting Started

This repository is a fresh starter project. It currently contains no application code — this README is the entry point for getting started.

## Requirements

- Git
- Python 3.11+ (once code is added)

## Setup

1. Clone the repository:

   ```bash
   git clone <repository-url>
   cd <repository-name>
   ```

2. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

3. Install dependencies (none required yet):

   ```bash
   pip install -r requirements.txt   # add this file when dependencies exist
   ```

## Usage

### Issue lifecycle handling (LIFE-05)

The `issue_lifecycle` package implements handling for GitHub issues that are
closed (as completed or as not planned) or deleted, both before execution and
during a run:

- `claim_issue(number, provider)` — the pre-execution gate. It re-checks the
  live issue state and refuses to claim anything that is not OPEN, so unstarted
  closed/deleted work is never claimed.
- `LifecycleWatcher(number, provider, interval=30.0)` — polls issue state during
  a run. Call `watcher.check()` at safe points; it raises `RunAborted` if the
  issue was closed (completed or not planned) or deleted mid-run.

### Running the tests

```bash
python -m pytest -q
```

## Project Structure

```
.
├── README.md
├── issue_lifecycle
│   ├── __init__.py
│   ├── claimer.py
│   ├── models.py
│   └── watcher.py
└── tests
    └── test_issue_lifecycle.py
```

## Next Steps

- Add application code and a `requirements.txt` (or `pyproject.toml`).
- Add a test suite and document the test command.
- Update this README as the project takes shape.
