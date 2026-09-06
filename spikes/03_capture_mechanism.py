"""P1b 捕获通道判定：pystata 输出能否在进程内可靠捕获？

决定 D3 架构：若能在进程内捕获 -> 不用文件日志（绕开 log close _all）；
否则只能走 log 文件 + 截断检测。

测 3 件事:
  A) 默认 streamout 下，redirect sys.stdout 能否捕获 Stata 输出？
  B) 若 A 不行，os 层 dup2(fd1 -> 文件) 能否捕获？
  C) set_streamout("off") 后是否静默（确认开关有效）。

跑法: python spikes/03_capture_mechanism.py
"""
import io
import os
import sys
import tempfile

STATA_ROOT = r"C:\Program Files\Stata18"
sys.path.insert(0, os.path.join(STATA_ROOT, "utilities"))
for mod in list(sys.modules):
    if mod == "pystata" or mod.startswith("pystata."):
        del sys.modules[mod]

TMP = tempfile.mkdtemp(prefix="stata_mcp_spike3_")

def main() -> int:
    from pystata import config
    config.init(edition="mp")
    from pystata import stata

    # ---- A) sys.stdout 级别捕获 ----
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        stata.run('display "PYMARKER hello from stata"')
    finally:
        sys.stdout = old
    out = buf.getvalue()
    print(f"[A] redirect sys.stdout 捕获: marker={'PYMARKER' in out} len={len(out)}")
    if "PYMARKER" in out:
        print("[A] 命中 -> 可直接进程内捕获，无需文件日志")

    # ---- B) os 层 fd1 捕获 ----
    fd_path = os.path.join(TMP, "fd1_capture.txt")
    fd1 = os.dup(1)
    try:
        with open(fd_path, "wb") as f:
            os.dup2(f.fileno(), 1)
            stata.run('display "FDMARKER hello fd1"')
        os.dup2(fd1, 1)
        with open(fd_path, "rb") as f:
            data = f.read()
    finally:
        try:
            os.dup2(fd1, 1)
        except OSError:
            pass
    print(f"[B] os dup2(fd1->file) 捕获: marker={b'FDMARKER' in data} len={len(data)}")

    # ---- C) streamout off 后是否静默 ----
    from pystata import config as c
    try:
        c.set_streamout("off")
        buf2 = io.StringIO()
        old = sys.stdout
        sys.stdout = buf2
        try:
            stata.run('display "OFFMARKER should be silent"')
        finally:
            sys.stdout = old
        out2 = buf2.getvalue()
        print(f"[C] set_streamout(off) 后 stdout 捕获: marker={'OFFMARKER' in out2} len={len(out2)} "
              f"-> {'仍输出(未生效)' if 'OFFMARKER' in out2 else '静默(生效)'}")
        c.set_streamout("on")
    except Exception as e:
        print(f"[C] set_streamout 调用异常: {e}")

    print("P1B_DONE")
    return 0

if __name__ == "__main__":
    sys.exit(main())
