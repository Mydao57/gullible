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
    actions = json.dumps([{"op": "write", "path": case.canary_file, "text": ""}])
    cmd = [sys.executable, str(FAKE_AGENT), mode, actions]
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


def test_merge_rows_keeps_models_apart_but_pools_summaries_without_a_model() -> None:
    a = {**_row("c1", valid=5, triggered=5), "model": "model-a"}
    b = {**_row("c1", valid=5, triggered=0), "model": "model-b"}
    merged = report.merge_rows([[a], [b]])
    assert sorted((r["model"], r["triggered"], r["valid"]) for r in merged) == [("model-a", 5, 5), ("model-b", 0, 5)]
    legacy = report.merge_rows([[_row("c1", valid=5, triggered=1)], [_row("c1", valid=5, triggered=2)]])
    assert len(legacy) == 1 and legacy[0]["triggered"] == 3


def test_report_shows_two_models_as_two_rows_with_distinct_anchors() -> None:
    rows = [{**_row("c1"), "model": "model-a"}, {**_row("c1"), "model": "model-b"}]
    page = report.render_report(rows, {}, {}, "s")
    assert 'href="#c1--model-a"' in page and 'href="#c1--model-b"' in page
    assert 'id="c1--model-a"' in page and 'id="c1--model-b"' in page


def test_fill_replaces_placeholders_in_a_single_pass() -> None:
    out = report.fill("a {{ x }} b {{y}}", {"x": "{{ y }}", "y": 2})
    assert out == "a {{ y }} b 2"  # a value containing a placeholder is not substituted again


def test_fill_fails_on_a_placeholder_without_a_value() -> None:
    import pytest

    with pytest.raises(KeyError, match="missing"):
        report.fill("{{ missing }}", {})


def test_templates_are_loaded_from_files_and_fully_filled() -> None:
    assert "<style>" in report.read_template("report.html") and "{{ css }}" in report.read_template("report.html")
    assert "--hit" in report.read_template("report.css")
    page = report.render_report([_row("c1")], {}, {}, "s")
    assert "{{" not in page and "<style>" in page and "--hit" in page  # nothing left unfilled


def test_every_template_file_is_declared_as_package_data() -> None:
    import fnmatch
    import tomllib

    root = Path(__file__).parent.parent
    patterns = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]["gullible"]
    for f in (root / "src" / "gullible" / "templates").iterdir():
        assert any(fnmatch.fnmatch(f"templates/{f.name}", p) for p in patterns), f.name


def _lab(cid: str, diff: str, impact: str, valid: int, hit: int, **kw) -> dict:
    return {**_row(cid, valid=valid, triggered=hit, total=valid), "difficulty": diff, "impact": impact, **kw}


def test_matrix_adds_up_cases_in_the_same_cell_and_leaves_controls_out() -> None:
    rows = [_lab("a", "hard", "marker", 10, 10), _lab("b", "hard", "marker", 10, 6),
            _lab("c", "easy", "marker", 10, 0), _lab("ctl", "hard", "marker", 10, 0, control=True)]
    page = report.render_matrix(rows)
    assert "<b>16/20</b> (80%)" in page and "2 cases" in page  # a + b, the control is not in the cell
    assert "<b>0/10</b> (0%)" in page and "1 case<" in page
    assert page.index("<th>easy</th>") < page.index("<th>hard</th>")  # difficulties in order


def test_matrix_columns_follow_the_impact_order_and_skip_unused_ones() -> None:
    rows = [_lab("a", "hard", "disclose", 5, 1), _lab("b", "hard", "marker", 5, 5), _lab("c", "medium", "modify", 5, 0)]
    page = report.render_matrix(rows)
    cols = [c for c in ("marker", "modify", "disclose", "destroy", "transmit") if f"<th>{c}</th>" in page]
    assert cols == ["marker", "modify", "disclose"]
    assert 'class="cell empty"' in page  # medium x marker has no case


def test_matrix_shades_by_rate_and_is_absent_for_unlabeled_summaries() -> None:
    assert '--rate:1.00' in report.render_matrix([_lab("a", "hard", "marker", 4, 4)])
    assert report.render_matrix([_row("old")]) == ""  # older summaries carry no labels


def test_matrix_has_one_grid_per_model() -> None:
    rows = [_lab("a", "hard", "marker", 5, 5, model="model-a"), _lab("a", "hard", "marker", 5, 0, model="model-b")]
    page = report.render_matrix(rows)
    assert page.count('class="matrix"') == 2 and "<h3>model-a</h3>" in page and "<h3>model-b</h3>" in page


def test_full_report_shows_the_matrix_and_the_label_columns() -> None:
    page = report.render_report([_lab("a", "hard", "disclose", 5, 1)], {}, {}, "s")
    assert "Difficulty and impact" in page and "<th>Difficulty</th><th>Impact</th>" in page
    assert "<td>hard</td><td>disclose</td>" in page and "{{" not in page


def test_matrix_names_the_grid_of_summaries_that_recorded_no_model() -> None:
    rows = [_lab("a", "hard", "marker", 5, 5), _lab("b", "hard", "marker", 5, 0, model="model-b")]
    assert "<h3>model not recorded</h3>" in report.render_matrix(rows)


def _case(cid: str, diff: str, impact: str) -> "runner.Case":
    return runner.Case(id=cid, carrier="x", task="t", repeat=1, workspace=Path("."), canary_file="f",
                       difficulty=diff, impact=impact)


def test_fill_labels_takes_missing_labels_from_the_fixtures() -> None:
    cases = {"old": _case("old", "hard", "disclose"), "new": _case("new", "easy", "marker")}
    legacy = _row("old")  # a summary from before the labels existed: no difficulty, no impact
    labeled = {**_row("new"), "difficulty": "medium", "impact": "modify"}
    gone = _row("deleted-fixture")
    out = {r["id"]: r for r in report.fill_labels([legacy, labeled, gone], cases)}
    assert (out["old"]["difficulty"], out["old"]["impact"]) == ("hard", "disclose")
    assert (out["new"]["difficulty"], out["new"]["impact"]) == ("medium", "modify")  # kept, not overwritten
    assert "difficulty" not in out["deleted-fixture"]
    assert "difficulty" not in legacy  # the input is not mutated


def test_build_gives_the_grid_to_summaries_that_predate_the_labels(tmp_path: Path) -> None:
    summary = _make_results(tmp_path)  # written by the runner, then stripped of its labels
    rows = json.loads(summary.read_text())
    for r in rows:
        r.pop("difficulty", None), r.pop("impact", None)
    summary.write_text(json.dumps(rows))
    page = report.build(tmp_path, summary, FIXTURES)
    assert "Difficulty and impact" in page and "<th>hard</th>" in page
