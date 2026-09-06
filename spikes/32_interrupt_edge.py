import sys, os, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stata_mcp.stata.pystata_backend import get_backend

b = get_backend()
b.execute("display 1")

# 1) 无命令运行时调 interrupt（边界：误发 break）
print("=== 1) 无命令运行时 interrupt ===")
b.interrupt()
r = b.execute('display "after idle break: " 2+2')
print("   rc:", r.rc, "text:", repr(r.text[:40]))

# 2) interrupt() 打断正在跑的命令（封装后验证）
print("\n=== 2) interrupt() 打断长命令 ===")
holder = {}
def run():
    holder["r"] = b.execute("sleep 10000")
t = threading.Thread(target=run, daemon=True)
t.start(); time.sleep(0.8); b.interrupt(); t.join(timeout=5)
r = holder.get("r")
print("   rc:", getattr(r, 'rc', '?'), "线程存活:", t.is_alive())

# 3) 打断后继续正常
r = b.execute('display "final " 2+2')
print("\n=== 3) 打断后继续 ===")
print("   rc:", r.rc, "text:", repr(r.text[:40]))
print("DONE")
