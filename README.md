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

The `issue_lifecycle` module implements LIFE-05: GitHub issues that are closed
(as completed or not planned) or deleted are never claimed as unstarted work,
and runs already in progress stop gracefully at the next checkpoint.

Run the test suite:

```bash
python -m unittest discover -s tests -t . -v
```

## Project Structure

```
.
├── README.md
├── issue_lifecycle.py
└── tests/
    └── test_issue_lifecycle.py
```

## Next Steps

- Add application code and a `requirements.txt` (or `pyproject.toml`).
- Add a test suite and document the test command.
- Update this README as the project takes shape.
