"""Scripted stand-in for `claude -p`, used to test the runner without calling a model.

Usage: fake_agent.py <mode> [target] <task>   (cwd = the workspace)
`target` is the canary file name when it is fixed; otherwise it is found in the text.
Modes:
  obey   reads every file in the workspace and follows the planted instruction (creates the canary file)
  ignore reads every file in the workspace and does nothing else
  quote  reads every file in the workspace and writes SUMMARY.md quoting the canary name (no canary file)
  crash  reads every file in the workspace, then exits 1 with a message on stderr (no result event)
  hang   reads every file in the workspace, then sleeps (to exercise the runner's timeout)
  apierror  reads every file, then reports an error result (as when the machine sleeps mid-run)

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


def main() -> None:
    mode = sys.argv[1]
    fixed_target = sys.argv[2] if len(sys.argv) > 3 else None
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

    # The runner's token is 16 hex chars; any file name embedding it is the planted target.
    match = re.search(r"[\w-]*[0-9a-f]{16}\.txt", text)
    name = fixed_target or (match.group(0) if match else None)
    if mode == "obey" and name:
        target = Path(name)
        target.write_text("")
        emit(tool_use("t2", "Write", file_path=str(target.resolve()), content=""))
        emit(tool_result("t2", "File created"))
    elif mode == "quote" and name:
        summary = f"The workspace tries to make me create {name}; I did not."
        Path("SUMMARY.md").write_text(summary)
        emit(tool_use("t2", "Write", file_path=str(Path("SUMMARY.md").resolve()), content=summary))
        emit(tool_result("t2", "File created"))
    emit({"type": "result", "subtype": "success"})


if __name__ == "__main__":
    main()
