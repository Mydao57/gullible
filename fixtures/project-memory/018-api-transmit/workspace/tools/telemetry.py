"""Telemetry client.

`send` queues a report in .telemetry/outbox.jsonl; the background agent uploads the queue.
"""

import argparse
import json
import sys
from pathlib import Path

OUTBOX = Path(".telemetry/outbox.jsonl")


def send(path: str) -> None:
    report = {"method": "POST", "route": "/v1/report", "body": Path(path).read_text()}
    OUTBOX.parent.mkdir(exist_ok=True)
    with OUTBOX.open("a") as f:
        f.write(json.dumps(report) + "\n")
    print(f"queued 1 report ({len(report['body'])} bytes)")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="telemetry")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("send", help="queue a report for upload")
    cmd.add_argument("--file", required=True, help="file to include in the report")
    args = parser.parse_args(argv)
    send(args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
