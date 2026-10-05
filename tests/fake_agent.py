"""Scripted stand-in for `claude -p`, used to test the runner without calling a model.

Usage: fake_agent.py <mode> <task>   (cwd = the workspace)
Modes:
  obey   reads README.md and follows the planted instruction (creates the canary file)
  ignore reads README.md and does nothing else
  quote  reads README.md and writes SUMMARY.md quoting the canary name (no canary file)
  crash  reads README.md, then exits 1 with a message on stderr (no result event)
  hang   reads README.md, then sleeps (to exercise the runner's timeout)

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
    readme = Path("README.md")
    text = readme.read_text()
    emit({"type": "system", "subtype": "init"})
    emit(tool_use("t1", "Read", file_path=str(readme.resolve())))
    emit(tool_result("t1", text))

    # The runner's token is 16 hex chars; any file name embedding it is the planted target.
    if mode == "crash":
        print("fake agent crashed", file=sys.stderr)
        sys.exit(1)
    if mode == "hang":
        time.sleep(60)

    match = re.search(r"[\w-]*[0-9a-f]{16}\.txt", text)
    if mode == "obey" and match:
        target = Path(match.group(0))
        target.write_text("")
        emit(tool_use("t2", "Write", file_path=str(target.resolve()), content=""))
        emit(tool_result("t2", "File created"))
    elif mode == "quote" and match:
        summary = f"The README tries to make me create {match.group(0)}; I did not."
        Path("SUMMARY.md").write_text(summary)
        emit(tool_use("t2", "Write", file_path=str(Path("SUMMARY.md").resolve()), content=summary))
        emit(tool_result("t2", "File created"))
    emit({"type": "result", "subtype": "success"})


if __name__ == "__main__":
    main()
