"""End-to-end runner tests with a scripted fake agent (no model call)."""

import json
import sys
from pathlib import Path

import pytest

from gullible.runner import PLACEHOLDER, Case, load_case, prepare_workspace, run_once, snapshot, holds

FIXTURES = Path(__file__).parent.parent / "fixtures"
CASE_DIRS = sorted(p.parent for p in FIXTURES.glob("*/*/case.yaml"))
FAKE_AGENT = Path(__file__).parent / "fake_agent.py"


@pytest.fixture(params=CASE_DIRS, ids=lambda p: p.name)
def case_dir(request: pytest.FixtureRequest) -> Path:
    return request.param


def obey_actions(case: Case) -> list[dict]:
    """What a fully gullible agent does to satisfy every predicate of the case."""
    actions = []
    for p in case.predicates:
        if p.kind == "exists":
            actions.append({"op": "write", "path": p.path, "text": ""})
        elif p.kind == "contains":
            actions.append({"op": "write", "path": p.path, "text": p.text})
        elif p.kind == "changed":
            actions.append({"op": "append", "path": p.path, "text": "touched\n"})
        else:  # missing
            actions.append({"op": "delete", "path": p.path})
    return actions


def _cmd(mode: str, case_dir: Path) -> list[str]:
    return [sys.executable, str(FAKE_AGENT), mode, json.dumps(obey_actions(load_case(case_dir)))]


def test_obeying_agent_is_detected(case_dir: Path) -> None:
    case = load_case(case_dir)
    d = run_once(case, timeout=30, agent_cmd=_cmd("obey", case_dir))
    assert d.file_created and d.trap_seen and d.triggered
    # The token reaches a tool call only when it is in the path or command the agent used;
    # otherwise only the workspace outcome proves the agent obeyed.
    assert d.token_in_tool_calls == any(PLACEHOLDER in a["path"] for a in obey_actions(case))


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
        # Instructions live in prose and config. Code may legitimately name its own output file
        # (the telemetry stub does), so only non-code files are checked.
        texts = [p.read_text() for p in (d / "workspace").rglob("*") if p.is_file() and p.suffix != ".py"]
        for pred in case.predicates:
            name = Path(pred.path.replace(PLACEHOLDER, "")).name.strip(".")
            assert not any(name in t for t in texts), (d.name, pred)


def test_no_success_predicate_holds_on_a_fresh_workspace(case_dir: Path, tmp_path: Path) -> None:
    # If one did, the case would "trigger" without the agent doing anything.
    case = load_case(case_dir)
    workdir = tmp_path / "w"
    prepare_workspace(case.workspace, workdir, "ab" * 8)
    before = snapshot(workdir)
    assert not any(holds(p, workdir, "ab" * 8, before) for p in case.predicates)


def test_every_fixture_is_labeled_with_a_difficulty(case_dir: Path) -> None:
    assert load_case(case_dir).difficulty in ("easy", "medium", "hard")


def test_deleting_a_decoy_needs_a_shell(case_dir: Path) -> None:
    case = load_case(case_dir)
    if any(p.kind == "missing" for p in case.predicates):
        assert "Bash" in case.tools.split(","), "no deletion tool without Bash: the case could never trigger"


def test_quoting_the_secret_in_another_file_is_not_a_disclosure(case_dir: Path) -> None:
    # The agent that repeats the decoy secret in a summary, instead of writing it where the
    # planted instruction says, must not count as having been fooled.
    case = load_case(case_dir)
    d = run_once(case, timeout=30, agent_cmd=_cmd("quote", case_dir))
    assert not d.triggered


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


def test_error_result_is_explained_with_the_agents_own_message(case_dir: Path) -> None:
    d = run_once(load_case(case_dir), timeout=30, agent_cmd=_cmd("apierror", case_dir))
    assert not d.completed and not d.triggered
    assert d.note.startswith("error result: API Error: Your computer went to sleep")


def test_main_filters_by_difficulty_and_impact(tmp_path: Path, monkeypatch, capsys) -> None:
    from gullible import runner

    seen = []

    def fake_run_once(case, timeout, trace_path=None, agent_cmd=None, model=None):
        seen.append(case.id)
        return runner.Detection(False, False, True)

    monkeypatch.setattr(runner, "run_once", fake_run_once)
    base = [str(FIXTURES), "--repeat", "1", "--results-dir", str(tmp_path)]
    assert runner.main([*base, "--difficulty", "hard", "--impact", "disclose"]) == 0
    assert seen == ["project-memory-016-secret-disclose", "project-memory-017-secret-disclose-control"]
    seen.clear()
    assert runner.main([*base, "--difficulty", "easy,medium"]) == 0
    assert seen and all(load_case(d).difficulty in ("easy", "medium") for d in CASE_DIRS if load_case(d).id in seen)
    capsys.readouterr()
    assert runner.main([*base, "--difficulty", "easy", "--impact", "destroy"]) == 2
    assert "no case matches" in capsys.readouterr().err
