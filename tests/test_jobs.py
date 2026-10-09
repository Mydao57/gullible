"""Parallel runs: `execute` and `--jobs`. No model is called."""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from gullible import runner
from gullible.runner import Detection, execute, load_case, run_once

FIXTURES = Path(__file__).parent.parent / "fixtures"
FAKE_AGENT = Path(__file__).parent / "fake_agent.py"


def test_tasks_really_run_at_the_same_time() -> None:
    # Two tasks wait for each other: this only finishes if both are in flight together.
    barrier = threading.Barrier(2, timeout=5)

    def work(n: int) -> int:
        barrier.wait()
        return n * 10

    results, interrupted = execute([1, 2], jobs=2, work=work, on_done=lambda i, r: None)
    assert results == {0: 10, 1: 20} and not interrupted


def test_with_one_job_tasks_run_one_after_the_other() -> None:
    running, peak, lock = 0, 0, threading.Lock()

    def work(_: int) -> None:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.02)
        with lock:
            running -= 1

    execute(list(range(5)), jobs=1, work=work, on_done=lambda i, r: None)
    assert peak == 1


def test_on_done_is_called_in_the_calling_thread_once_per_task() -> None:
    seen: list[tuple[int, int]] = []
    main = threading.get_ident()
    execute(list(range(6)), jobs=3, work=lambda n: n,
            on_done=lambda i, r: seen.append((i, threading.get_ident())))
    assert sorted(i for i, _ in seen) == list(range(6)) and {t for _, t in seen} == {main}


def test_interrupt_cancels_queued_tasks_and_keeps_what_finished() -> None:
    started: list[int] = []

    def work(n: int) -> int:
        started.append(n)
        time.sleep(0.05)
        return n

    calls: list[int] = []

    def on_done(i: int, r: int) -> None:
        calls.append(i)
        if len(calls) == 1:
            raise KeyboardInterrupt  # one Ctrl-C, as seen by the calling thread

    results, interrupted = execute(list(range(20)), jobs=1, work=work, on_done=on_done)
    assert interrupted
    assert len(started) < 20, "queued runs must not keep going after Ctrl-C: they cost quota"
    assert 0 in results
    assert sorted(calls) == sorted(results), "every result that is kept was also reported"


def test_an_exception_in_a_task_cancels_the_queue_and_propagates() -> None:
    started: list[int] = []

    def work(n: int) -> int:
        started.append(n)
        if n == 0:
            raise RuntimeError("boom")
        time.sleep(0.05)
        return n

    with pytest.raises(RuntimeError, match="boom"):
        execute(list(range(20)), jobs=1, work=work, on_done=lambda i, r: None)
    assert len(started) < 20


def test_run_once_is_safe_to_run_many_at_a_time() -> None:
    # Real subprocesses with the fake agent: no run may see another's token, workspace or canary.
    case_dir = FIXTURES / "readme" / "002-setup-step"
    case = load_case(case_dir)
    actions = json.dumps([{"op": "write", "path": case.canary_file, "text": ""}])
    modes = ["obey", "ignore"] * 6

    def work(mode: str) -> Detection:
        return run_once(case, 30, agent_cmd=[sys.executable, str(FAKE_AGENT), mode, actions])

    results, _ = execute(modes, jobs=6, work=work, on_done=lambda i, r: None)
    assert [results[i].triggered for i in range(12)] == [m == "obey" for m in modes]
    assert all(r.completed and r.trap_seen for r in results.values())


def _patch_run_once(monkeypatch: pytest.MonkeyPatch, delay: float = 0.0) -> list[Path]:
    traces: list[Path] = []

    def fake(case, timeout, trace_path=None, agent_cmd=None, model=None):
        time.sleep(delay)
        traces.append(trace_path)
        return Detection(case.id.endswith("009-claude-md-plain"), False, True)

    monkeypatch.setattr(runner, "run_once", fake)
    return traces


def _summary(tmp_path: Path) -> list[dict]:
    return json.loads(next(tmp_path.glob("summary-*.json")).read_text())


def test_main_gives_the_same_summary_with_one_job_or_several(tmp_path: Path, monkeypatch, capsys) -> None:
    _patch_run_once(monkeypatch)
    args = [str(FIXTURES / "project-memory"), "--repeat", "3"]
    assert runner.main([*args, "--results-dir", str(tmp_path / "a")]) == 0
    assert runner.main([*args, "--jobs", "4", "--results-dir", str(tmp_path / "b")]) == 0
    assert _summary(tmp_path / "a") == _summary(tmp_path / "b")
    assert [r["id"] for r in _summary(tmp_path / "b")] == sorted(r["id"] for r in _summary(tmp_path / "b"))


def test_main_uses_one_trace_path_per_run_and_prints_each_case_summary_once(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    traces = _patch_run_once(monkeypatch, delay=0.01)
    assert runner.main([str(FIXTURES / "project-memory"), "--repeat", "4", "--jobs", "5",
                        "--results-dir", str(tmp_path)]) == 0
    assert len(traces) == len(set(traces)) and all(t.name.startswith("run-") for t in traces)
    out = capsys.readouterr().out
    assert out.count("project-memory-009-claude-md-plain: triggered 4/4") == 1  # the one-line summary


def test_main_rejects_a_bad_jobs_value_and_warns_on_a_large_one(tmp_path: Path, monkeypatch, capsys) -> None:
    _patch_run_once(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        runner.main([str(FIXTURES), "--jobs", "0"])
    assert exc.value.code == 2
    capsys.readouterr()
    assert runner.main([str(FIXTURES / "readme"), "--repeat", "1", "--jobs", "9", "--results-dir", str(tmp_path)]) == 0
    assert "--jobs 9 is a lot" in capsys.readouterr().err


def test_main_reports_a_partial_summary_and_exit_130_when_interrupted(tmp_path: Path, monkeypatch, capsys) -> None:
    calls: list[int] = []

    def fake_execute(tasks, jobs, work, on_done):
        # Two runs finish, then Ctrl-C: the rest is cancelled.
        results = {i: work(tasks[i]) for i in (0, 1)}
        for i, r in results.items():
            on_done(i, r)
        calls.append(len(tasks))
        return results, True

    _patch_run_once(monkeypatch)
    monkeypatch.setattr(runner, "execute", fake_execute)
    code = runner.main([str(FIXTURES / "readme"), "--repeat", "3", "--results-dir", str(tmp_path)])
    assert code == 130
    err = capsys.readouterr().err
    assert f"interrupted: 2 of {calls[0]} runs finished" in err
    rows = _summary(tmp_path)
    assert len(rows) == 1 and rows[0]["total"] == 2  # only the case that has finished runs, with those runs
