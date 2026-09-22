"""Profile two persistent Stata sessions against separate Workspace-local DTAs."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

from stata_mcp.session import SessionManager


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_a", type=Path)
    parser.add_argument("dataset_b", type=Path)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    dataset_a = args.dataset_a.resolve(strict=True)
    dataset_b = args.dataset_b.resolve(strict=True)
    if dataset_a == dataset_b or os.path.samefile(dataset_a, dataset_b):
        parser.error("dataset_a and dataset_b must be distinct physical files")

    def command_for(dataset: Path, expected_profile: int) -> str:
        return (
            f'use "{dataset.name}", clear\n'
            f"assert workspace_profile == {expected_profile}\n"
            "regress x1 x2 x3"
        )

    manager = SessionManager(max_sessions=2)
    try:
        session_a = manager.get_or_create("large-workspace-a")
        session_b = manager.get_or_create("large-workspace-b")
        binding_a = session_a.bind_working_directory(str(dataset_a.parent))
        binding_b = session_b.bind_working_directory(str(dataset_b.parent))
        if binding_a.rc != 0 or binding_b.rc != 0:
            raise RuntimeError("failed to bind one or more Workspace working directories")
        started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            future_a = pool.submit(
                session_a.execute, command_for(dataset_a, 1), args.timeout
            )
            future_b = pool.submit(
                session_b.execute, command_for(dataset_b, 2), args.timeout
            )
            result_a = future_a.result(timeout=args.timeout + 30)
            result_b = future_b.result(timeout=args.timeout + 30)
        elapsed = time.perf_counter() - started
        payload = {
            "datasets_are_distinct_physical_files": not os.path.samefile(
                dataset_a, dataset_b
            ),
            "elapsed_seconds": round(elapsed, 3),
            "a": {
                "dataset": str(dataset_a),
                "size_bytes": dataset_a.stat().st_size,
                "working_directory": str(dataset_a.parent),
                "rc": result_a.rc,
                "status": result_a.error_kind,
                "N": (result_a.structured or {}).get("N"),
                "worker_pid": (result_a.supervision_proof or {}).get("worker_pid"),
            },
            "b": {
                "dataset": str(dataset_b),
                "size_bytes": dataset_b.stat().st_size,
                "working_directory": str(dataset_b.parent),
                "rc": result_b.rc,
                "status": result_b.error_kind,
                "N": (result_b.structured or {}).get("N"),
                "worker_pid": (result_b.supervision_proof or {}).get("worker_pid"),
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
        if (
            result_a.rc != 0
            or result_b.rc != 0
            or payload["a"]["worker_pid"] == payload["b"]["worker_pid"]
            or payload["a"]["N"] != 5_500_000.0
            or payload["b"]["N"] != 5_500_000.0
        ):
            return 1
        return 0
    finally:
        manager.close_all()


if __name__ == "__main__":
    raise SystemExit(main())
