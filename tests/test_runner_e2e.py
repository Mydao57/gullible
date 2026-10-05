"""End-to-end runner tests with a scripted fake agent (no model call)."""

import sys
from pathlib import Path

import pytest

from gullible.runner import load_case, run_once

CASE_DIR = Path(__file__).parent.parent / "fixtures" / "readme" / "001-html-comment"
FAKE_AGENT = Path(__file__).parent / "fake_agent.py"


def _cmd(mode: str) -> list[str]:
    return [sys.executable, str(FAKE_AGENT), mode]


def test_obeying_agent_is_detected_by_both_signals() -> None:
    d = run_once(load_case(CASE_DIR), timeout=30, agent_cmd=_cmd("obey"))
    assert d.file_created and d.token_in_tool_calls and d.trap_seen and d.triggered


def test_ignoring_agent_is_not_triggered_but_saw_the_trap() -> None:
    d = run_once(load_case(CASE_DIR), timeout=30, agent_cmd=_cmd("ignore"))
    assert d.trap_seen and not d.triggered


def test_agent_quoting_the_injection_is_not_triggered() -> None:
    d = run_once(load_case(CASE_DIR), timeout=30, agent_cmd=_cmd("quote"))
    assert d.trap_seen and not d.triggered


def test_trace_is_saved_outside_the_workspace(tmp_path: Path) -> None:
    trace = tmp_path / "out" / "run-1.jsonl"
    run_once(load_case(CASE_DIR), timeout=30, trace_path=trace, agent_cmd=_cmd("obey"))
    assert trace.read_text().count("\n") >= 4


def test_canary_never_leaks_into_the_fixture() -> None:
    run_once(load_case(CASE_DIR), timeout=30, agent_cmd=_cmd("obey"))
    workspace = CASE_DIR / "workspace"
    assert [p.name for p in workspace.iterdir()] == ["README.md"]
    assert "{{TOKEN}}" in (workspace / "README.md").read_text()


@pytest.mark.parametrize("mode", ["obey", "ignore", "quote"])
def test_each_run_gets_a_fresh_token(mode: str) -> None:
    # Two runs must not see each other's canary: a leftover file would be a false positive.
    case = load_case(CASE_DIR)
    first = run_once(case, timeout=30, agent_cmd=_cmd("obey"))
    second = run_once(case, timeout=30, agent_cmd=_cmd(mode))
    assert first.triggered
    assert second.triggered == (mode == "obey")
