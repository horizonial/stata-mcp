"""P1 执行核验证：do 文件 + 命名日志包裹 -> 读回文本 + rc；实测中文编码。

验证 3 件事（对应 DESIGN D3/D4/D5 的落地前提）：
  a) 命名日志包裹是否能把用户输出捕获进 log 文件，且不被用户代码 log close _all 误伤；
  b) log 文件真实编码是什么（utf-8 / gbk / latin1?），中文能否无损读回；
  c) 出错命令的 rc 能否用「全局 scalar」稳定拿到（规避 mcp-stata 的 local 作用域 bug）。

跑法: python spikes/02_exec_core.py
"""
import os
import sys
import tempfile

STATA_ROOT = r"C:\Program Files\Stata18"
sys.path.insert(0, os.path.join(STATA_ROOT, "utilities"))
for mod in list(sys.modules):
    if mod == "pystata" or mod.startswith("pystata."):
        del sys.modules[mod]

TMP = tempfile.mkdtemp(prefix="stata_mcp_spike_")

def run_with_log(stata, code: str, log_path: str) -> bytes:
    """(我们规划的执行核雏形) do 文件 + 命名日志包裹，返回 log 原文 bytes。"""
    p = log_path.replace("\\", "/")
    header = f'capture log close __mcp_log\nlog using "{p}", replace text name(__mcp_log)\n'
    footer = "capture log close __mcp_log\n"
    stata.run(header + code + footer, echo=False)
    with open(log_path, "rb") as f:
        return f.read()

def probe_decode(data: bytes, needle: str):
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            txt = data.decode(enc)
            hit = needle in txt
            print(f"  decode {enc:8s}: ok   contains {needle!r}: {hit}")
        except Exception:
            print(f"  decode {enc:8s}: FAIL")

def main() -> int:
    from pystata import config
    config.init(edition="mp")
    from pystata import stata
    from sfi import Scalar

    log_path = os.path.join(TMP, "mcp_test.log")
    print(f"[P1] log_path = {log_path}")

    # a) 成功命令 + 中文输出
    code_ok = 'display "中文测试ABC123"\ndisplay "another line 2+2=" 2+2\n'
    data = run_with_log(stata, code_ok, log_path)
    print("[P1-a] 成功命令读回字节数:", len(data))
    probe_decode(data, "中文测试")

    # a2) 用户代码里调用 log close _all，验证我们的命名日志是否被误关
    data2 = run_with_log(stata, 'log close _all\ndisplay "after user log close _all"\n', log_path)
    print("[P1-a2] 用户 log close _all 后读回字节数:", len(data2), "| 含目标行:",
          b"after user log close _all" in data2)

    # c) 出错命令 rc 捕获（全局 scalar）
    stata.run("capture noisily qui regress no_such_var_dummy x")
    stata.run("scalar _last_rc = _rc")
    rc = Scalar.getValue("_last_rc")
    print(f"[P1-c] 出错命令 rc = {rc}  (期望非 0，例如 111/198/2000 等)")

    # c2) 正常命令 rc 应为 0
    stata.run("qui regress mpg weight")
    stata.run("scalar _last_rc2 = _rc")
    rc2 = Scalar.getValue("_last_rc2")
    print(f"[P1-c2] 正常命令 rc = {rc2}  (期望 0)")

    print("P1_DONE")
    return 0

if __name__ == "__main__":
    sys.exit(main())
