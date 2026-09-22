"""Isolate which post-estimation action destabilizes repeated PyStata runs."""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "src")

from stata_mcp.results import regression
from stata_mcp.stata.pystata_backend import PystataBackend
from stata_mcp.stata.worker import _data_signature


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=("execute", "sample", "parse_without_sample", "parse", "signature"),
    )
    parser.add_argument("--iterations", type=int, default=100)
    args = parser.parse_args()

    backend = PystataBackend()
    backend.init()
    assert backend.execute("sysuse auto, clear").rc == 0
    original_sample = regression._estimation_sample_manifest
    if args.mode == "parse_without_sample":
        regression._estimation_sample_manifest = lambda _backend: None
    try:
        for iteration in range(1, args.iterations + 1):
            result = backend.execute("regress price length")
            assert result.rc == 0
            if args.mode == "sample":
                assert original_sample(backend) is not None
            elif args.mode in {"parse_without_sample", "parse"}:
                parsed = regression.parse_coef_table(backend)
                assert parsed["N"] == 74.0
            elif args.mode == "signature":
                assert _data_signature(backend)
            print(f"{args.mode}:{iteration}", flush=True)
    finally:
        regression._estimation_sample_manifest = original_sample
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
