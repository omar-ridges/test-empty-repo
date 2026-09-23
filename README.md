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

The `publication` package implements the publication lifecycle for the
assisted flow (LIFE-03): patch upload followed by pull-request (PR)
publication, with a Stop action available at every checkpoint before the
point of no return.

```python
from publication import PublicationSession, render_status

session = PublicationSession("session-1", amount_cents=1000)
outcome = session.run(patch_bytes)   # upload + publish, honouring Stop
print(outcome.value)                 # "published" | "stopped" | "failed"
print(render_status(session))        # one truthful status message
```

Key guarantees:

- **One clear final outcome** — `PUBLISHED`, `STOPPED` or `FAILED`, final
  and immutable once reached.
- **No duplicate charge** — a session is charged exactly once, and only if
  the PR was actually published; stopped and failed sessions are never
  charged.
- **Truthful UI** — if the PR was already published, the UI says so (with
  the PR URL); it never claims "stopped" for a published PR.
- **Explicit point of no return** — see
  `PublicationSession.POINT_OF_NO_RETURN`: the transition to the
  `PUBLISHING` phase, after which Stop cannot prevent publication.

`PublicationSession.request_stop()` is idempotent and thread-safe; long
uploads/publishes can cooperatively cancel by polling
`session.stop_requested`.

## Running the tests

```bash
python -m pytest -q tests        # with pytest
python tests/test_stop_during_publication.py   # standalone, no deps
```

## Project Structure

```
.
├── README.md
├── publication/
│   ├── __init__.py     # public API
│   ├── states.py       # Phase / FinalOutcome enums
│   ├── billing.py      # idempotent BillingLedger (no duplicate charges)
│   ├── session.py      # Stop-aware PublicationSession state machine
│   └── ui.py           # truthful status rendering
└── tests/
    └── test_stop_during_publication.py
```

## Next Steps

- Wire `upload_patch_fn` / `create_pr_fn` to the real patch host and PR API.
- Add a `requirements.txt` (or `pyproject.toml`) when dependencies exist.
