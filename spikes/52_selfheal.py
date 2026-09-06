"""P10c spike：会话自愈——worker 崩溃后自动重建 + reset 标记。"""
import sys, os, time
sys.path.insert(0, "src")
from stata_mcp.session import Session

def main():
    s = Session("test_selfheal")
    # 第一次 execute（懒启动）
    r = s.execute("display 2+2")
    print("[1] 首次 execute:", repr(r.text.strip()), "reset:", r.reset)

    # 持久状态
    s.execute("scalar _x = 40 + 2")
    r = s.execute("display _x")
    print("[2] 持久 _x:", repr(r.text.strip()))

    # 模拟崩溃：杀 worker 进程
    pid = s._proc.pid
    s._proc.terminate()
    s._proc.join(timeout=3)
    time.sleep(0.3)
    print("[3] worker 已杀, is_alive:", s.is_alive())

    # 再次 execute → 应自动重建 + reset=True
    r = s.execute("display 3+3")
    print("[4] 崩溃后 execute:", repr(r.text.strip()), "reset:", r.reset)

    # 数据已丢（_x 没了）
    r = s.execute("display _x")
    print("[5] 重建后 _x（应丢失）:", repr(r.text.strip()), "rc:", r.rc)

    s.close()
    print("SELFHEAL_OK")

if __name__ == "__main__":
    main()
