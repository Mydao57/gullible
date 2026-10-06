import json
import sys
from pathlib import Path

from gullible import report, runner
from gullible.runner import load_case, run_once

FIXTURES = Path(__file__).parent.parent / "fixtures"
FAKE_AGENT = Path(__file__).parent / "fake_agent.py"
CASE_DIR = FIXTURES / "project-memory" / "009-claude-md-plain"


def _row(cid: str = "c1", control: bool = False, valid: int = 4, triggered: int = 1, total: int = 5) -> dict:
    lo, hi = runner.wilson_interval(triggered, valid)
    return {"id": cid, "carrier": "readme", "control": control, "total": total, "valid": valid,
            "triggered": triggered, "trap_seen": valid, "incomplete": total - valid, "ci_low": lo, "ci_high": hi}


def _make_results(tmp_path: Path, mode: str = "obey", stamp: str = "20260101-000000") -> Path:
    """Produce a real trace + meta with the runner and the fake agent, then a matching summary."""
    case = load_case(CASE_DIR)
    run_dir = tmp_path / case.id / stamp
    cmd = [sys.executable, str(FAKE_AGENT), mode, case.canary_file]
    detections = [run_once(case, 30, run_dir / f"run-{i}.jsonl", cmd) for i in (1, 2)]
    stats = runner.case_stats(case, detections)
    (tmp_path / f"summary-{stamp}.json").write_text(json.dumps([stats.as_dict()]))
    return tmp_path / f"summary-{stamp}.json"


def test_meta_records_the_detection(tmp_path: Path) -> None:
    _make_results(tmp_path)
    meta = json.loads(next(tmp_path.rglob("run-1.meta.json")).read_text())
    assert meta["detection"]["file_created"] is True and meta["detection"]["completed"] is True


def test_build_report_from_real_traces(tmp_path: Path) -> None:
    summary = _make_results(tmp_path)
    page = report.build(tmp_path, summary, FIXTURES)
    assert "<title>gullible report</title>" in page
    assert "project-memory-009-claude-md-plain" in page
    assert "2/2 (100%)" in page and "followed it" in page
    assert "fake-model / CLI 0.0.0" in page
    assert "status derived" not in page  # the runs recorded their detection


def test_report_escapes_text_that_comes_from_the_trace(tmp_path: Path) -> None:
    run_dir = tmp_path / "c1" / "20260101-000000"
    run_dir.mkdir(parents=True)
    evil = "<script>alert(1)</script>"
    events = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Write", "input": {"file_path": f"/x/{evil}"}}]}},
        {"type": "result", "subtype": "success", "result": evil, "total_cost_usd": 0.01},
    ]
    (run_dir / "run-1.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (run_dir / "run-1.meta.json").write_text(json.dumps({"detection": {
        "file_created": False, "token_in_tool_calls": False, "trap_seen": True, "completed": True, "note": evil}}))
    summary = tmp_path / "summary-20260101-000000.json"
    summary.write_text(json.dumps([_row("c1", valid=1, triggered=0, total=1)]))
    page = report.build(tmp_path, summary, tmp_path / "no-fixtures")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_runs_without_recorded_detection_are_derived_from_the_trace(tmp_path: Path) -> None:
    run_dir = tmp_path / "c1" / "20260101-000000"
    run_dir.mkdir(parents=True)
    token = "ab" * 8
    write = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Write", "input": {"file_path": f"/w/gullible-x/workspace/f-{token}.txt"}}]}}
    done = {"type": "result", "subtype": "success", "result": "ok"}
    (run_dir / "run-1.jsonl").write_text(json.dumps(write) + "\n" + json.dumps(done) + "\n")
    (run_dir / "run-1.meta.json").write_text(json.dumps({"token": token}))
    (run_dir / "run-2.jsonl").write_text(json.dumps(done) + "\n")
    (run_dir / "run-2.meta.json").write_text(json.dumps({"token": token}))
    runs = report.load_runs(run_dir, None)
    assert [r.triggered for r in runs] == [True, False]
    assert all(r.derived and r.completed for r in runs)
    assert runs[0].calls == (f"Write f-{token}.txt",)  # workspace prefix stripped


def test_controls_are_listed_last_and_labelled() -> None:
    rows = [_row("ctl", control=True, valid=5, triggered=0), _row("hit", valid=5, triggered=5), _row("mid", valid=4, triggered=1)]
    page = report.render_report(rows, {}, {}, "s")
    assert page.index(">hit<") < page.index(">mid<") < page.index(">ctl<")
    assert 'class="pill ctl">control' in page


def test_report_handles_a_case_with_no_valid_run() -> None:
    row = _row("dead", valid=0, triggered=0, total=3)
    row.update(ci_low=0.0, ci_high=1.0)
    assert "n/a" in report.render_report([row], {}, {}, "s")


def test_main_writes_the_latest_report_and_fails_without_a_summary(tmp_path: Path, capsys) -> None:
    assert report.main(["--results-dir", str(tmp_path)]) == 2
    assert "run the runner first" in capsys.readouterr().err
    _make_results(tmp_path)
    assert report.main(["--results-dir", str(tmp_path), "--fixtures-dir", str(FIXTURES)]) == 0
    assert (tmp_path / "report-20260101-000000.html").exists()


def test_merge_rows_adds_counts_and_recomputes_the_interval() -> None:
    a = [_row("c1", valid=5, triggered=0, total=5), _row("c2", valid=5, triggered=5, total=5)]
    b = [_row("c1", valid=4, triggered=1, total=5)]
    merged = {r["id"]: r for r in report.merge_rows([a, b])}
    assert (merged["c1"]["total"], merged["c1"]["valid"], merged["c1"]["triggered"]) == (10, 9, 1)
    assert merged["c1"]["incomplete"] == 1
    lo, hi = runner.wilson_interval(1, 9)
    assert (merged["c1"]["ci_low"], merged["c1"]["ci_high"]) == (lo, hi)
    assert merged["c2"]["triggered"] == 5  # a case present in one summary only is kept


def test_report_merges_several_run_sets(tmp_path: Path) -> None:
    first = _make_results(tmp_path, "obey", "20260101-000000")
    second = _make_results(tmp_path, "ignore", "20260102-000000")
    page = report.build(tmp_path, [first, second], FIXTURES)
    assert "2 run sets (20260101-000000, 20260102-000000)" in page
    assert "2/4 (50%)" in page  # 2 followed in the first set, 0 in the second
    assert page.count("run 1</b>") == 2  # same run index in both sets, told apart by their stamp
    assert "20260102-000000</span>" in page


def test_single_summary_report_does_not_show_stamps_per_run(tmp_path: Path) -> None:
    page = report.build(tmp_path, _make_results(tmp_path), FIXTURES)
    assert 'class="muted">20260101-000000</span> <span class="pill' not in page


def test_main_accepts_several_summaries(tmp_path: Path) -> None:
    a = _make_results(tmp_path, "obey", "20260101-000000")
    b = _make_results(tmp_path, "ignore", "20260102-000000")
    assert report.main(["--results-dir", str(tmp_path), "--fixtures-dir", str(FIXTURES), "--summary", str(a), str(b)]) == 0
    assert (tmp_path / "report-merged-20260102-000000.html").exists()
