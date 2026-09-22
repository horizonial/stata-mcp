"""Probe real parallel execution across two isolated PyStata sessions.

The probe deliberately runs repeated commands before and after a synchronized
two-second Stata sleep.  Wall-clock overlap distinguishes real concurrency from
mere multi-session routing.
"""
from __future__ import annotations

import concurrent.futures
import json
import sys
import time

sys.path.insert(0, "src")

from stata_mcp.session import SessionManager


def _run(session, label: str) -> dict:
    started = time.perf_counter()
    outputs: list[dict] = []
    for code in (
        f'display "@@{label}_BEFORE"',
        "sleep 2000",
        f'display "@@{label}_AFTER"',
    ):
        result = session.execute(code, timeout=15)
        outputs.append(
            {
                "code": code,
                "rc": result.rc,
                "text": result.text.strip(),
                "generation": result.session_generation,
                "pid": (result.supervision_proof or {}).get("worker_pid"),
            }
        )
    return {"label": label, "elapsed": time.perf_counter() - started, "outputs": outputs}


def main() -> int:
    manager = SessionManager(max_sessions=2)
    try:
        session_a = manager.get_or_create("workspace-a")
        session_b = manager.get_or_create("workspace-b")
        # Warm both engines before measuring command overlap.
        assert session_a.execute("display 1", timeout=15).rc == 0
        assert session_b.execute("display 2", timeout=15).rc == 0

        started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(_run, session_a, "A"),
                pool.submit(_run, session_b, "B"),
            ]
            results = [future.result(timeout=30) for future in futures]
        wall = time.perf_counter() - started
        print(json.dumps({"wall": wall, "results": results}, indent=2))

        all_ok = all(item["rc"] == 0 for result in results for item in result["outputs"])
        # Two 2s sleeps should finish substantially below the ~4s serialized floor.
        return 0 if all_ok and wall < 3.5 else 1
    finally:
        manager.close_all()


if __name__ == "__main__":
    raise SystemExit(main())
