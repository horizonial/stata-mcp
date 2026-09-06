"""隔离测试 watchdog：用必死 pid，确认 _parent_watchdog 能让进程自退。"""
import os
import sys

sys.path.insert(0, os.path.abspath("src"))
from stata_mcp.stata.worker import _parent_alive


def _watchdog_target():
    from stata_mcp.stata.worker import _parent_watchdog

    _parent_watchdog(999999999, 0.5)


def main():
    print("alive(self):", _parent_alive(os.getpid()), flush=True)
    print("alive(dead 999999999):", _parent_alive(999999999), flush=True)

    import multiprocessing as mp

    ctx = mp.get_context("spawn")
    p = ctx.Process(target=_watchdog_target)
    p.start()
    p.join(timeout=5)
    print("watchdog exitcode:", p.exitcode, "(期望 0 = 自退成功)", flush=True)
    sys.exit(0 if p.exitcode == 0 else 1)


if __name__ == "__main__":
    main()
