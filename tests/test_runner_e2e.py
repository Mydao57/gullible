"""End-to-end runner tests with a scripted fake agent (no model call)."""

import sys
from pathlib import Path

import pytest

from gullible.runner import PLACEHOLDER, load_case, run_once

FIXTURES = Path(__file__).parent.parent / "fixtures"
CASE_DIRS = sorted(p.parent for p in FIXTURES.glob("*/*/case.yaml"))
FAKE_AGENT = Path(__file__).parent / "fake_agent.py"


@pytest.fixture(params=CASE_DIRS, ids=lambda p: p.name)
def case_dir(request: pytest.FixtureRequest) -> Path:
    return request.param


def _cmd(mode: str, case_dir: Path) -> list[str]:
    """Fake agent command. A fixed canary name is passed explicitly; a tokenized one is
    found by the fake agent in the workspace text."""
    cmd = [sys.executable, str(FAKE_AGENT), mode]
    canary = load_case(case_dir).canary_file
    if PLACEHOLDER not in canary:
        cmd.append(canary)
    return cmd


def test_obeying_agent_is_detected(case_dir: Path) -> None:
    case = load_case(case_dir)
    d = run_once(case, timeout=30, agent_cmd=_cmd("obey", case_dir))
    assert d.file_created and d.trap_seen and d.triggered
    # With a fixed canary name the token is not in the tool call, so only the file proves it.
    assert d.token_in_tool_calls == (PLACEHOLDER in case.canary_file)


def test_ignoring_agent_is_not_triggered_but_saw_the_trap(case_dir: Path) -> None:
    d = run_once(load_case(case_dir), timeout=30, agent_cmd=_cmd("ignore", case_dir))
    assert d.trap_seen and not d.triggered


def test_agent_quoting_the_injection_is_not_triggered(case_dir: Path) -> None:
    d = run_once(load_case(case_dir), timeout=30, agent_cmd=_cmd("quote", case_dir))
    assert d.trap_seen and not d.triggered


def test_trace_is_saved_outside_the_workspace(case_dir: Path, tmp_path: Path) -> None:
    trace = tmp_path / "out" / "run-1.jsonl"
    run_once(load_case(case_dir), timeout=30, trace_path=trace, agent_cmd=_cmd("obey", case_dir))
    assert trace.read_text().count("\n") >= 4


def test_canary_never_leaks_into_the_fixture(case_dir: Path) -> None:
    workspace = case_dir / "workspace"
    before = {p: p.read_text() for p in workspace.rglob("*") if p.is_file()}
    run_once(load_case(case_dir), timeout=30, agent_cmd=_cmd("obey", case_dir))
    after = {p: p.read_text() for p in workspace.rglob("*") if p.is_file()}
    assert after == before  # the run works on a copy: no canary, no substituted token


@pytest.mark.parametrize("mode", ["obey", "ignore", "quote"])
def test_each_run_gets_a_fresh_token(case_dir: Path, mode: str) -> None:
    # Two runs must not see each other's canary: a leftover file would be a false positive.
    case = load_case(case_dir)
    first = run_once(case, timeout=30, agent_cmd=_cmd("obey", case_dir))
    second = run_once(case, timeout=30, agent_cmd=_cmd(mode, case_dir))
    assert first.triggered
    assert second.triggered == (mode == "obey")


def test_crashed_agent_is_incomplete_and_explained(case_dir: Path, tmp_path: Path) -> None:
    trace = tmp_path / "run-1.jsonl"
    d = run_once(load_case(case_dir), timeout=30, trace_path=trace, agent_cmd=_cmd("crash", case_dir))
    assert not d.completed and not d.triggered
    assert "exit 1" in d.note and "fake agent crashed" in d.note
    assert "fake agent crashed" in trace.with_suffix(".meta.json").read_text()


def test_hanging_agent_times_out_and_is_incomplete(case_dir: Path) -> None:
    d = run_once(load_case(case_dir), timeout=2, agent_cmd=_cmd("hang", case_dir))
    assert not d.completed and d.note.startswith("timeout")


def test_completed_runs_have_no_note(case_dir: Path) -> None:
    d = run_once(load_case(case_dir), timeout=30, agent_cmd=_cmd("ignore", case_dir))
    assert d.completed and d.note == ""


def test_meta_records_agent_info_and_token(case_dir: Path, tmp_path: Path) -> None:
    import json

    trace = tmp_path / "run-1.jsonl"
    run_once(load_case(case_dir), timeout=30, trace_path=trace, agent_cmd=_cmd("ignore", case_dir))
    meta = json.loads(trace.with_suffix(".meta.json").read_text())
    assert meta["agent"]["model"] == "fake-model"
    assert len(meta["token"]) == 16 and meta["cmd"][0] == sys.executable


def test_control_fixtures_contain_no_planted_instruction() -> None:
    controls = [d for d in CASE_DIRS if load_case(d).control]
    assert controls, "expected at least one control fixture"
    for d in controls:
        case = load_case(d)
        needle = case.canary_file.replace(PLACEHOLDER, "")
        texts = [p.read_text() for p in (d / "workspace").rglob("*") if p.is_file()]
        assert not any(needle.strip(".") in t for t in texts), d.name


def test_main_runs_every_case_and_writes_a_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    import json

    from gullible import runner

    monkeypatch.setattr(runner, "run_once", lambda case, timeout, trace_path=None, agent_cmd=None, model=None:
                        runner.Detection(case.id.endswith("009-claude-md-plain"), False, True))
    code = runner.main([str(FIXTURES / "project-memory"), "--repeat", "2", "--results-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "valid/total" in out and "[control] project-memory-010-claude-md-control" in out
    summary = json.loads(next(tmp_path.glob("summary-*.json")).read_text())
    by_id = {r["id"]: r for r in summary}
    assert by_id["project-memory-009-claude-md-plain"]["triggered"] == 2
    assert by_id["project-memory-010-claude-md-control"]["triggered"] == 0
    assert by_id["project-memory-010-claude-md-control"]["control"] is True


def test_main_fails_cleanly_when_no_case_is_found(tmp_path: Path, capsys) -> None:
    from gullible import runner

    assert runner.main([str(tmp_path)]) == 2
    assert "no case.yaml" in capsys.readouterr().err


def test_detection_carries_the_model_reported_by_the_agent(case_dir: Path) -> None:
    d = run_once(load_case(case_dir), timeout=30, agent_cmd=_cmd("ignore", case_dir))
    assert d.model == "fake-model"
