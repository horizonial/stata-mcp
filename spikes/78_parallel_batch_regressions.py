"""Stress official Stata /e batch processes as a parallel executor profile."""
from __future__ import annotations

import concurrent.futures
import os
import subprocess
import tempfile
import time
from pathlib import Path


STATA_HOME = Path(os.environ.get("STATA_HOME", r"C:\Program Files\Stata18"))
STATA = STATA_HOME / "StataMP-64.exe"
AUTO = STATA_HOME / "auto.dta"


def _do_text(*, dependent: str, independent: str, marker: str) -> str:
    auto = AUTO.as_posix()
    return f'''clear all
set more off
capture noisily {{
    use "{auto}", clear
    regress {dependent} {independent}
}}
local __rc = _rc
file open __result using "{marker}.result", write text replace
file write __result "rc=`__rc''" _n
if `__rc' == 0 {{
    file write __result "N=" %21.0g (e(N)) _n
    file write __result "b=" %21.0g (_b[{independent}]) _n
}}
file close __result
exit, clear
'''


def _run(
    root: Path, label: str, dependent: str, independent: str
) -> tuple[int, float, str]:
    directory = root / label
    directory.mkdir(parents=True, exist_ok=True)
    do_file = directory / f"{label}.do"
    do_file.write_text(
        _do_text(dependent=dependent, independent=independent, marker=label),
        encoding="utf-8",
    )
    started = time.perf_counter()
    completed = subprocess.run(
        [str(STATA), "/e", "/q", "do", str(do_file)],
        cwd=directory,
        timeout=30,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    elapsed = time.perf_counter() - started
    result = directory / f"{label}.result"
    detail = result.read_text(encoding="utf-8") if result.is_file() else "missing result"
    if not result.is_file() or "rc=0" not in detail.replace(" ", ""):
        log = directory / f"{label}.log"
        if log.is_file():
            detail += "\n" + log.read_text(encoding="utf-8", errors="replace")[-2000:]
        return 1, elapsed, detail
    return completed.returncode, elapsed, detail


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="stata-mcp-batch-parallel-") as raw:
        root = Path(raw)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            for iteration in range(1, 16):
                started = time.perf_counter()
                fa = pool.submit(_run, root, f"a-{iteration}", "mpg", "weight")
                fb = pool.submit(_run, root, f"b-{iteration}", "price", "length")
                a, b = fa.result(timeout=40), fb.result(timeout=40)
                wall = time.perf_counter() - started
                print(
                    f"iteration={iteration} a={a} b={b} wall={wall:.3f}s",
                    flush=True,
                )
                if a[0] != 0 or b[0] != 0:
                    print(a[2])
                    print(b[2])
                    return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
