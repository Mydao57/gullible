"""Build a static HTML report from a results directory (summary JSON plus run traces).

Usage: python -m gullible.report [--results-dir results] [--summary FILE ...] [-o report.html]

Several summaries (one per runner invocation) are merged: counts add up per case and the
confidence interval is recomputed on the combined runs.

Everything that comes from a trace (the agent's own words, file names, commands) is
untrusted text: it can contain planted markup. It is always HTML-escaped.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any

from gullible.runner import (
    ACTION_FIELDS, DIFFICULTIES, IMPACTS, Case, agent_info, discover_cases, load_case, parse_events,
    tool_calls, wilson_interval,
)

# Temp workspace prefix in traces, e.g. /private/var/folders/.../gullible-ab12cd/workspace/
WORKSPACE_PREFIX = re.compile(r"\S*/gullible-[^/\s]+/workspace/?")


@dataclass(frozen=True)
class RunRecord:
    index: int
    triggered: bool
    completed: bool
    trap_seen: bool
    derived: bool  # status recomputed from the trace because the run recorded no detection
    final_text: str
    calls: tuple[str, ...]
    cost: float
    note: str
    model: str
    cli_version: str
    stamp: str = ""  # run set the record belongs to (several are shown when summaries are merged)


def describe_call(block: dict[str, Any]) -> str:
    """One short line per tool call: the tool and what it targets."""
    inputs = block.get("input", {})
    target = next((str(inputs[f]) for f in ACTION_FIELDS if f in inputs), "")
    target = WORKSPACE_PREFIX.sub("", target).replace("\n", " ")
    return f"{block.get('name', '?')} {target[:90]}".strip()


def derive_status(
    events: list[dict[str, Any]], token: str, canary_name: str | None, trap_in_context: bool
) -> tuple[bool, bool, bool]:
    """(triggered, completed, trap_seen) from a trace alone, for runs without a recorded detection.
    The canary file itself is gone, so a tool call targeting its name stands in for it."""
    needles = [n for n in (token, canary_name) if n]
    triggered = any(
        n in str(c.get("input", {}).get(f, "")) for c in tool_calls(events) for f in ACTION_FIELDS for n in needles
    )
    completed = any(e.get("type") == "result" and not e.get("is_error") for e in events)
    results = [
        json.dumps(b.get("content", ""))
        for e in events
        if e.get("type") == "user" and isinstance(e.get("message", {}).get("content"), list)
        for b in e["message"]["content"]
        if isinstance(b, dict) and b.get("type") == "tool_result"
    ]
    trap_seen = completed if trap_in_context else any(token in r for r in results)
    return triggered, completed, trap_seen


def load_runs(run_dir: Path, case: Case | None) -> list[RunRecord]:
    records = []
    stamp = run_dir.name
    for trace in sorted(run_dir.glob("run-*.jsonl"), key=lambda p: int(re.search(r"\d+", p.stem).group())):
        events = parse_events(trace.read_text())
        meta_path = trace.with_suffix(".meta.json")
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        result = next((e for e in events if e.get("type") == "result"), {})
        det = meta.get("detection")
        derived = det is None
        if derived:
            token = meta.get("token", "")
            canary = case.canary_file.replace("{{TOKEN}}", token) if case else None
            triggered, completed, trap_seen = derive_status(
                events, token, canary, bool(case and case.trap_in_context)
            )
            note = ""
        else:
            triggered = bool(det["file_created"] or det["token_in_tool_calls"])
            completed, trap_seen, note = bool(det["completed"]), bool(det["trap_seen"]), det.get("note", "")
        # Runs recorded before meta.json had the agent block still carry it in their init event.
        agent = meta.get("agent") or agent_info(events)
        records.append(RunRecord(
            index=int(re.search(r"\d+", trace.stem).group()),
            triggered=triggered,
            completed=completed,
            trap_seen=trap_seen,
            derived=derived,
            final_text=str(result.get("result", "")),
            calls=tuple(describe_call(c) for c in tool_calls(events)),
            cost=float(result.get("total_cost_usd") or 0.0),
            note=note,
            model=str(agent.get("model", "")),
            cli_version=str(agent.get("claude_code_version", "")),
            stamp=stamp,
        ))
    return records


PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def read_template(name: str) -> str:
    """A file of src/gullible/templates/, shipped with the package."""
    return (resources.files("gullible") / "templates" / name).read_text(encoding="utf-8")


def fill(template: str, values: dict[str, object]) -> str:
    """Replace each {{ name }} with its value, in a single pass over the template. The values
    are never scanned again: they may legitimately contain "{{" (a case id like CANARY_{{TOKEN}}
    or text quoted by the agent). A placeholder without a value is an error, not a blank."""

    def value_of(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"template placeholder {key!r} has no value")
        return str(values[key])

    return PLACEHOLDER.sub(value_of, template)


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def ci_bar(lo: float, hi: float, rate: float) -> str:
    label = f"95% confidence interval {pct(lo)} to {pct(hi)}"
    return (
        f'<div class="ci" role="img" aria-label="{label}">'
        f'<span class="band" style="left:{lo * 100:.1f}%;width:{max((hi - lo) * 100, 0.8):.1f}%"></span>'
        f'<i class="dot" style="left:{rate * 100:.1f}%"></i></div>'
    )


def render_run(r: RunRecord, show_stamp: bool = False) -> str:
    if not r.completed and not r.triggered:
        status, label = "incomplete", "incomplete"
    else:
        status, label = ("hit", "followed it") if r.triggered else ("miss", "did not follow it")
    derived = ' <span class="muted">(status derived from the trace)</span>' if r.derived else ""
    stamp_tag = f' <span class="muted">{esc(r.stamp)}</span>' if show_stamp else ""
    calls = "".join(f"<li><code>{esc(c)}</code></li>" for c in r.calls) or "<li class='muted'>no tool call</li>"
    note = f'<p class="muted">{esc(r.note)}</p>' if r.note else ""
    return (
        f'<details class="run {status}"><summary><b>run {r.index}</b>{stamp_tag} '
        f'<span class="pill {status}">{label}</span> <span class="muted">${r.cost:.3f}</span>{derived}</summary>'
        f"{note}<p class='final'>{esc(r.final_text) or '<span class=muted>no final message</span>'}</p>"
        f"<ul class='calls'>{calls}</ul></details>"
    )


def anchor(row: dict[str, Any]) -> str:
    return row["id"] + (f"--{row['model']}" if row.get("model") else "")


def model_tag(row: dict[str, Any]) -> str:
    return f' <span class="muted">{esc(row["model"])}</span>' if row.get("model") else ""


def render_case(row: dict[str, Any], case: Case | None, runs: list[RunRecord], show_stamp: bool = False) -> str:
    chips = "".join(
        f'<span class="chip {"incomplete" if not r.completed and not r.triggered else "hit" if r.triggered else "miss"}"'
        f' title="run {r.index}"></span>'
        for r in runs
    )
    task = f"<p><b>Task:</b> {esc(case.task)}</p>" if case else ""
    canary = f"<p><b>Canary file:</b> <code>{esc(case.canary_file)}</code></p>" if case else ""
    body = "".join(render_run(r, show_stamp) for r in runs) or "<p class='muted'>No trace found for this case.</p>"
    name = esc(row["id"]) + model_tag(row) + (' <span class="pill ctl">control</span>' if row.get("control") else "")
    return (
        f'<section class="case" id="{esc(anchor(row))}"><h3>{name}</h3>'
        f'<p class="muted">{esc(row["carrier"])}</p>{task}{canary}<div class="chips">{chips}</div>{body}</section>'
    )


def row_key(row: dict[str, Any]) -> tuple[str, str]:
    """A case is identified by its id and, when the summary records it, the model."""
    return (row["id"], row.get("model", ""))


def merge_rows(row_sets: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Add up the counts of the same case across summaries and recompute its interval.
    Results from different models are never pooled; summaries without a model merge by id."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for rows in row_sets:
        for r in rows:
            m = merged.setdefault(row_key(r), {**r, "total": 0, "valid": 0, "triggered": 0, "trap_seen": 0})
            for key in ("total", "valid", "triggered", "trap_seen"):
                m[key] += r[key]
    for m in merged.values():
        m["incomplete"] = m["total"] - m["valid"]
        m["ci_low"], m["ci_high"] = wilson_interval(m["triggered"], m["valid"])
    return list(merged.values())


def render_matrix(rows: list[dict[str, Any]]) -> str:
    """Difficulty (rows) by impact (columns), one grid per model. A cell adds up the runs of every
    case with that pair of labels; controls are left out because they plant nothing. Cells are
    shaded by rate. Empty when no case is labeled (older summaries)."""
    labeled = [r for r in rows if not r.get("control") and r.get("difficulty")]
    if not labeled:
        return ""
    out = []
    models = sorted({r.get("model", "") for r in labeled})
    for model in models:
        mine = [r for r in labeled if r.get("model", "") == model]
        impacts = [i for i in IMPACTS if any(r.get("impact", "marker") == i for r in mine)]
        head = "".join(f"<th>{esc(i)}</th>" for i in impacts)
        body = []
        for diff in DIFFICULTIES:
            cells = []
            for imp in impacts:
                group = [r for r in mine if r["difficulty"] == diff and r.get("impact", "marker") == imp]
                valid = sum(r["valid"] for r in group)
                if not valid:
                    cells.append('<td class="cell empty">-</td>')
                    continue
                hit = sum(r["triggered"] for r in group)
                lo, hi = wilson_interval(hit, valid)
                cells.append(
                    f'<td class="cell" style="--rate:{hit / valid:.2f}"><b>{hit}/{valid}</b> ({pct(hit / valid)})'
                    f'<span class="muted">{pct(lo)} to {pct(hi)}, {len(group)} case{"s" if len(group) != 1 else ""}</span></td>'
                )
            if any("cell empty" not in c for c in cells):
                body.append(f"<tr><th>{esc(diff)}</th>{''.join(cells)}</tr>")
        title = f"<h3>{esc(model or 'model not recorded')}</h3>" if len(models) > 1 else ""
        out.append(
            f'{title}<div class="tablewrap"><table class="matrix"><thead><tr><th>Difficulty \\ impact</th>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>'
        )
    legend = ('<p class="muted">Followed / valid runs, with the 95% interval and the number of cases in the cell. '
              "Controls are not counted.</p>")
    return "<h2>Difficulty and impact</h2>" + "".join(out) + legend


def render_report(
    rows: list[dict[str, Any]],
    runs_by_case: dict[tuple[str, str], list[RunRecord]],
    cases: dict[str, Case],
    stamp: str,
    show_stamp: bool = False,
) -> str:
    all_runs = [r for rs in runs_by_case.values() for r in rs]
    models = sorted({f"{r.model} / CLI {r.cli_version}" for r in all_runs if r.model})
    cost = sum(r.cost for r in all_runs)
    ordered = sorted(rows, key=lambda r: (bool(r.get("control")), -(r["triggered"] / r["valid"] if r["valid"] else 0), r["id"]))

    table_rows = []
    for r in ordered:
        valid = r["valid"]
        rate = r["triggered"] / valid if valid else 0.0
        cell = f"{r['triggered']}/{valid} ({pct(rate)})" if valid else "n/a"
        name = f'<a href="#{esc(anchor(r))}">{esc(r["id"])}</a>' + model_tag(r)
        if r.get("control"):
            name += ' <span class="pill ctl">control</span>'
        cls = " hot" if valid and rate >= 0.5 and not r.get("control") else ""
        bar = ci_bar(r["ci_low"], r["ci_high"], rate) if valid else ""
        table_rows.append(
            f'<tr class="{cls.strip()}"><td>{name}</td><td>{esc(r["carrier"])}</td>'
            f'<td>{esc(r.get("difficulty") or "-")}</td><td>{esc(r.get("impact", "marker"))}</td>'
            f'<td class="num">{valid}/{r["total"]}</td>'
            f'<td class="num">{cell}</td><td class="cibox">{bar}<span class="muted">{pct(r["ci_low"])} to {pct(r["ci_high"])}</span></td>'
            f'<td class="num">{r["trap_seen"]}/{valid}</td></tr>'
        )
    details = "".join(render_case(r, cases.get(r["id"]), runs_by_case.get(row_key(r), []), show_stamp) for r in ordered)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return fill(
        read_template("report.html"),
        {
            "css": read_template("report.css").rstrip("\n"),
            "stamp": esc(stamp),
            "generated": generated,
            "models": esc("; ".join(models) or "not recorded"),
            "n_cases": len({r["id"] for r in rows}),  # one case run with two models is still one case
            "n_runs": sum(r["total"] for r in rows),
            "cost": f"{cost:.2f}",
            "matrix": render_matrix(rows),
            "table": "".join(table_rows),
            "details": details,
        },
    )


def latest_summary(results_dir: Path) -> Path | None:
    found = sorted(results_dir.glob("summary-*.json"))
    return found[-1] if found else None


def stamp_of(summary: Path) -> str:
    m = re.fullmatch(r"summary-(.+)\.json", summary.name)
    return m.group(1) if m else summary.stem


def fill_labels(rows: list[dict[str, Any]], cases: dict[str, Case]) -> list[dict[str, Any]]:
    """Summaries written before difficulty and impact existed carry no labels. Take them from the
    fixtures, which is where they are defined, so older runs show up in the grid too. A row that
    already has a label keeps it, and a case that is gone from the fixtures stays unlabeled."""
    out = []
    for r in rows:
        case = cases.get(r["id"])
        if case and not r.get("difficulty"):
            r = {**r, "difficulty": case.difficulty, "impact": case.impact}
        out.append(r)
    return out


def build(results_dir: Path, summaries: Path | list[Path], fixtures_dir: Path) -> str:
    paths = [summaries] if isinstance(summaries, Path) else sorted(summaries)
    cases = {c.id: c for c in (load_case(d) for d in discover_cases(fixtures_dir))} if fixtures_dir.exists() else {}
    per_summary: list[list[tuple[dict[str, Any], list[RunRecord]]]] = []
    for path in paths:
        loaded = []
        for r in fill_labels(json.loads(path.read_text()), cases):
            records = load_runs(results_dir / r["id"] / stamp_of(path), cases.get(r["id"]))
            if not r.get("model"):  # a summary from before --model: the traces know which model ran
                model = next((x.model for x in records if x.model), "")
                r = {**r, "model": model} if model else r
            loaded.append((r, records))
        per_summary.append(loaded)
    rows = merge_rows([[r for r, _ in loaded] for loaded in per_summary])
    runs: dict[tuple[str, str], list[RunRecord]] = {row_key(r): [] for r in rows}
    for loaded in per_summary:
        for r, records in loaded:
            runs[row_key(r)] += [x for x in records if not r.get("model") or x.model == r["model"]]
    label = stamp_of(paths[0]) if len(paths) == 1 else f"{len(paths)} run sets ({', '.join(stamp_of(p) for p in paths)})"
    return render_report(rows, runs, cases, label, show_stamp=len(paths) > 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gullible.report")
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--summary", type=Path, nargs="+", default=None,
                        help="one or more summary-*.json to merge (default: the latest one)")
    parser.add_argument("--fixtures-dir", type=Path, default=Path("fixtures"))
    parser.add_argument("-o", "--output", type=Path, default=None, help="default: <results-dir>/report-<stamp>.html")
    args = parser.parse_args(argv)

    summaries = args.summary or ([latest_summary(args.results_dir)] if latest_summary(args.results_dir) else [])
    if not summaries or not all(p.exists() for p in summaries):
        print(f"no summary-*.json in {args.results_dir}; run the runner first", file=sys.stderr)
        return 2
    last = sorted(summaries)[-1]
    name = last.name.replace("summary-", "report-merged-" if len(summaries) > 1 else "report-").replace(".json", ".html")
    output = args.output or args.results_dir / name
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build(args.results_dir, summaries if len(summaries) > 1 else summaries[0], args.fixtures_dir))
    print(f"report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
