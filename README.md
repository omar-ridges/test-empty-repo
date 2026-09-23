# Getting Started

This repository hosts the run/claim pipeline, including the GitHub issue
lifecycle guard from **LIFE-05**: unstarted work whose issue has been closed
(as completed or as not planned) or deleted is never claimed — whether the
change happens before execution or while a run is in progress.

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

See the [Issue Lifecycle Guard (LIFE-05)](#issue-lifecycle-guard-life-05)
section below for behaviour and a code example.

## Issue Lifecycle Guard (LIFE-05)

Before an unstarted work item is claimed, the current state of its GitHub
issue is checked:

| Issue state | Behaviour |
| --- | --- |
| open | claimed and processed normally |
| closed, `state_reason: completed` | skipped, never claimed |
| closed, `state_reason: not_planned` | skipped, never claimed |
| deleted (API responds 404/410) | skipped, never claimed |
| lookup fails transiently | left unstarted, retried later |

The check happens immediately before each claim, so it covers both timings:
issues closed or deleted **before** a run and those changed **during** a run
(before the item is claimed). Already claimed/started work is allowed to
finish (LIFE-05 marks in-flight behaviour as "TODO - discuss").

### Usage

```python
from ridges import GitHubIssueClient, RunOrchestrator, WorkItem

client = GitHubIssueClient(repo="owner/repo", token="ghp_...")
orchestrator = RunOrchestrator(client)

queue = [WorkItem(issue_number=101), WorkItem(issue_number=102)]
processed = orchestrator.run(queue)
# Items whose issues were closed or deleted are skipped with a reason and
# produce no claim signal; open issues are claimed and processed.
```

## Testing

```bash
pip install pytest
pytest -q
```

## Project Structure

```
.
├── ridges/
│   ├── __init__.py
│   ├── models.py         # IssueStatus / WorkItem models
│   ├── github_client.py  # GitHub lookup; 404/410 => deleted
│   ├── claimer.py        # claim gating on issue state
│   └── runner.py         # run orchestration
├── tests/
│   └── test_issue_lifecycle.py
└── requirements.txt
```
