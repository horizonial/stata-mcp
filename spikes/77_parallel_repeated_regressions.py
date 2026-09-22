"""Stress repeated structured regressions in two real PyStata workers."""
from __future__ import annotations

import concurrent.futures
import argparse
import json
import sys

sys.path.insert(0, "src")

from stata_mcp.session import SessionManager


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=30)
    args = parser.parse_args()
    manager = SessionManager(max_sessions=2)
    try:
        a = manager.get_or_create("workspace-a")
        b = manager.get_or_create("workspace-b")
        assert a.execute("sysuse auto, clear", timeout=20).rc == 0
        assert b.execute("sysuse auto, clear", timeout=20).rc == 0
        records = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            for iteration in range(1, args.iterations + 1):
                fa = pool.submit(a.execute, "regress mpg weight", 30)
                fb = pool.submit(b.execute, "regress price length", 30)
                ra, rb = fa.result(timeout=40), fb.result(timeout=40)
                record = {
                    "iteration": iteration,
                    "a_rc": ra.rc,
                    "b_rc": rb.rc,
                    "a_status": ra.error_kind,
                    "b_status": rb.error_kind,
                    "a_structured": ra.structured is not None,
                    "b_structured": rb.structured is not None,
                }
                records.append(record)
                print(json.dumps(record), flush=True)
                if (
                    ra.rc != 0
                    or rb.rc != 0
                    or ra.structured is None
                    or rb.structured is None
                ):
                    return 1
        return 0
    finally:
        manager.close_all()


if __name__ == "__main__":
    raise SystemExit(main())
