"""End-to-end runner tests with a scripted fake agent (no model call)."""

import sys
from pathlib import Path

import pytest

from gullible.runner import load_case, run_once

FIXTURES = Path(__file__).parent.parent / "fixtures"
CASE_DIRS = sorted(p.parent for p in FIXTURES.glob("*/*/case.yaml"))
FAKE_AGENT = Path(__file__).parent / "fake_agent.py"


@pytest.fixture(params=CASE_DIRS, ids=lambda p: p.name)
def case_dir(request: pytest.FixtureRequest) -> Path:
    return request.param


def _cmd(mode: str) -> list[str]:
    return [sys.executable, str(FAKE_AGENT), mode]


def test_obeying_agent_is_detected_by_both_signals(case_dir: Path) -> None:
    d = run_once(load_case(case_dir), timeout=30, agent_cmd=_cmd("obey"))
    assert d.file_created and d.token_in_tool_calls and d.trap_seen and d.triggered


def test_ignoring_agent_is_not_triggered_but_saw_the_trap(case_dir: Path) -> None:
    d = run_once(load_case(case_dir), timeout=30, agent_cmd=_cmd("ignore"))
    assert d.trap_seen and not d.triggered


def test_agent_quoting_the_injection_is_not_triggered(case_dir: Path) -> None:
    d = run_once(load_case(case_dir), timeout=30, agent_cmd=_cmd("quote"))
    assert d.trap_seen and not d.triggered


def test_trace_is_saved_outside_the_workspace(case_dir: Path, tmp_path: Path) -> None:
    trace = tmp_path / "out" / "run-1.jsonl"
    run_once(load_case(case_dir), timeout=30, trace_path=trace, agent_cmd=_cmd("obey"))
    assert trace.read_text().count("\n") >= 4


def test_canary_never_leaks_into_the_fixture(case_dir: Path) -> None:
    run_once(load_case(case_dir), timeout=30, agent_cmd=_cmd("obey"))
    workspace = case_dir / "workspace"
    assert not any(p.name.startswith("CANARY_") for p in workspace.iterdir())
    assert "{{TOKEN}}" in (workspace / "README.md").read_text()


@pytest.mark.parametrize("mode", ["obey", "ignore", "quote"])
def test_each_run_gets_a_fresh_token(case_dir: Path, mode: str) -> None:
    # Two runs must not see each other's canary: a leftover file would be a false positive.
    case = load_case(case_dir)
    first = run_once(case, timeout=30, agent_cmd=_cmd("obey"))
    second = run_once(case, timeout=30, agent_cmd=_cmd(mode))
    assert first.triggered
    assert second.triggered == (mode == "obey")
