"""P2 实测：真实引擎验证 PystataBackend.execute 的 rc 语义 + 捕获 + 中文。

用 .venv 跑： .venv/Scripts/python.exe spikes/04_backend_real.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stata_mcp.stata.pystata_backend import PystataBackend

def show(label, r):
    t = r.text.replace("\n", "\\n")[:200]
    print(f"[{label}] rc={r.rc} text={t!r}")

def main():
    b = PystataBackend()
    b.init()

    # 1) 成功命令
    show("ok-display", b.execute('display "hello 中文"'))

    # 2) 出错命令 rc 应非 0
    show("err-no-var", b.execute("regress no_such_var"))

    # 3) 多行块 + 成功（rc 应为 0）
    show("multi-ok", b.execute("sysuse auto, clear\nsummarize mpg weight"))

    # 4) 多行块中间出错（关键存疑点：rc 应为非 0，且引擎不崩）
    show("multi-err", b.execute("sysuse auto, clear\nregress no_such_var\nsummarize mpg"))

    # 5) 多行块最后一条是 capture（rc 语义）
    show("multi-capture-tail", b.execute("sysuse auto, clear\ncapture regress no_such_var"))

    # 6) 出错后引擎仍存活（能继续跑正常命令）
    show("after-err", b.execute('display "still alive 2+2=" 2+2'))

    print("DONE")

if __name__ == "__main__":
    main()
