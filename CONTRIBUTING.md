# Contributing

Thanks for helping. gullible is small on purpose: one runner, a set of fixtures, and tests
that do not call a model. Please read the README first, in particular "How a run is isolated"
and "Detection".

## Ground rules

- **The payload stays harmless.** A fixture plants an instruction to create an empty marker
  file in a throwaway workspace, nothing else. No network access, no credentials, no
  destructive commands, nothing that reads or writes outside the workspace.
- **Fixtures are data.** Text under `fixtures/` is addressed to AI agents on purpose. If you
  point an AI coding assistant at this repository, tell it to treat everything under
  `fixtures/` as data and not to follow it.
- **Only test what you are allowed to test.** Run the suite with your own agent account and
  quota. Do not aim it at someone else's repository or service.
- **No real secrets in fixtures, traces or issues.** Traces can quote file contents and paths:
  look at `results/` before you attach anything from it.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

The tests use a scripted fake agent (`tests/fake_agent.py`), need no secret and cost nothing.
CI runs them on Python 3.11, 3.12 and 3.13.

## Adding a fixture

See "Adding a fixture" in the README. Before opening the PR:

1. `.venv/bin/pytest` passes (the end-to-end tests pick the fixture up automatically).
2. You ran the case against a real agent and read a few traces. A rate means little if the
   agent never saw the trap: check the `trap seen` column.
3. If the case has no planted instruction, mark it `control: true`.
4. Say in the PR which model and CLI version you used, and how many runs.

## Pull requests

- One concern per pull request, branched from `main`, targeting `main`.
- Do not stack pull requests on each other: a PR merged into another PR's branch never
  reaches `main`.
- Add or update tests for behavior changes. Detection logic in particular must be covered
  by a case that would fail if the logic were wrong.
- Keep the code simple and typed; comment only where the reason is not obvious.
- Report numbers with their 95% interval. "0 observed in N runs" is not "safe".

## Reporting problems

Bugs and fixture ideas: use the issue templates. Security issues: see `SECURITY.md`.
