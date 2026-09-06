"""P0 点火测试：确认 pystata 能在本机 Python 进程内拉起 Stata 18 MP 并执行命令。

跑法:  python spikes/01_ignition.py
只读验证，不碰用户数据。会启动真实 Stata 引擎（占一个 license 席位）。
"""
import os
import sys
import time

STATA_ROOT = r"C:\Program Files\Stata18"
utilities = os.path.join(STATA_ROOT, "utilities")

# 1) 把 Stata 自带的 pystata 放在 sys.path 最前，避免撞 PyPI 上同名假包
sys.path.insert(0, utilities)
for mod in list(sys.modules):
    if mod == "pystata" or mod.startswith("pystata."):
        del sys.modules[mod]

def main() -> int:
    t0 = time.time()
    # 2) 初始化 Stata 引擎
    from pystata import config

    try:
        config.init(edition="mp")
    except TypeError:
        # 不同版本 init 签名略不同，退化为默认
        config.init()
    print(f"[ignition] config.init() OK in {time.time() - t0:.1f}s", file=sys.stderr)

    # 3) 执行一条命令并拿到版本
    from pystata import stata

    stata.run("display 1")
    stata.run('display "stata version: " c(version) " flavor: " c(flavor)')
    stata.run('display "charset: " c(charset) " os: " c(os)')

    # 4) 用 scalar 把结果取回 Python 侧（验证能双向读写 Stata 内存）
    stata.run('scalar _t = 2 + 3 * 5')
    from sfi import Scalar
    val = Scalar.getValue("_t")
    assert val == 17, f"expected 17, got {val}"
    print(f"[ignition] scalar round-trip OK: _t = {val}", file=sys.stderr)

    print("IGNITION_OK", file=sys.stderr)
    return 0

if __name__ == "__main__":
    sys.exit(main())
