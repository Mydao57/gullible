"""Scripted stand-in for `claude -p`, used to test the runner without calling a model.

Usage: fake_agent.py <mode> [actions-json] <task>   (cwd = the workspace)
Modes:
  obey      reads every file, then applies `actions` (what following the planted instruction does)
  ignore    reads every file and does nothing else
  quote     reads every file and writes SUMMARY.md that quotes the first action, without doing it
  crash     reads every file, then exits 1 with a message on stderr (no result event)
  hang      reads every file, then sleeps (to exercise the runner's timeout)
  apierror  reads every file, then reports an error result (as when the machine sleeps mid-run)

An action is {"op": "write" | "append" | "delete", "path": ..., "text": ...}. "{{TOKEN}}" in a
path or text is replaced by the run's token, found in the workspace text (16 hex characters).

Emits stream-json events in the shape observed from the real CLI.
"""

import json
import re
import sys
import time
from pathlib import Path


def emit(event: dict) -> None:
    print(json.dumps(event), flush=True)


def tool_use(tid: str, name: str, **input_: str) -> dict:
    block = {"type": "tool_use", "id": tid, "name": name, "input": input_}
    return {"type": "assistant", "message": {"content": [block]}}


def tool_result(tid: str, content: str) -> dict:
    block = {"type": "tool_result", "tool_use_id": tid, "content": content}
    return {"type": "user", "message": {"content": [block]}}


def apply(i: int, action: dict, fill) -> None:
    target = Path(fill(action["path"]))
    text = fill(action.get("text", ""))
    if action["op"] == "write":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        emit(tool_use(f"a{i}", "Write", file_path=str(target.resolve()), content=text))
    elif action["op"] == "append":
        with target.open("a") as f:
            f.write(text)
        emit(tool_use(f"a{i}", "Edit", file_path=str(target.resolve()), new_string=text))
    else:  # delete
        target.unlink()
        emit(tool_use(f"a{i}", "Bash", command=f"rm {target}"))
    emit(tool_result(f"a{i}", "done"))


def main() -> None:
    mode = sys.argv[1]
    actions = json.loads(sys.argv[2]) if len(sys.argv) > 3 else []
    emit({"type": "system", "subtype": "init", "model": "fake-model", "claude_code_version": "0.0.0"})
    text = ""
    for i, path in enumerate(sorted(p for p in Path(".").rglob("*") if p.is_file()), start=1):
        content = path.read_text()
        emit(tool_use(f"r{i}", "Read", file_path=str(path.resolve())))
        emit(tool_result(f"r{i}", content))
        text += content

    if mode == "crash":
        print("fake agent crashed", file=sys.stderr)
        sys.exit(1)
    if mode == "hang":
        time.sleep(60)
    if mode == "apierror":
        emit({"type": "result", "subtype": "success", "is_error": True,
              "result": "API Error: Your computer went to sleep mid-response."})
        sys.exit(1)

    token = re.search(r"[0-9a-f]{16}", text)
    fill = lambda s: s.replace("{{TOKEN}}", token.group(0) if token else "")  # noqa: E731
    if mode == "obey":
        for i, action in enumerate(actions, start=1):
            apply(i, action, fill)
    elif mode == "quote" and actions:
        first = actions[0]
        summary = (f"The workspace asks me to {first['op']} {fill(first['path'])}"
                   f" {fill(first.get('text', ''))}; I did not.")
        Path("SUMMARY.md").write_text(summary)
        emit(tool_use("q1", "Write", file_path=str(Path("SUMMARY.md").resolve()), content=summary))
        emit(tool_result("q1", "done"))
    emit({"type": "result", "subtype": "success"})


if __name__ == "__main__":
    main()
