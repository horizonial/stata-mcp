import sys, os, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stata_mcp.stata.pystata_backend import get_backend

b = get_backend()
b.execute("display 1")
import pystata.config as cfg
SetBreak = cfg.stlib.StataSO_SetBreak
SetBreak.argtypes = []; SetBreak.restype = None

def break_once(label):
    holder = {}
    def run():
        holder["r"] = b.execute("sleep 10000")
    t = threading.Thread(target=run, daemon=True)
    t.start(); time.sleep(0.8); SetBreak(); t.join(timeout=5)
    r = holder.get("r")
    print(f"  [{label}] 线程存活={t.is_alive()} rc={getattr(r,'rc','?')}")
    return t.is_alive()

print("=== 连续 3 次中断测试 ===")
for i in range(3):
    break_once(f"break{i+1}")

print("\n=== 中断 3 次后引擎还活着吗 ===")
try:
    r = b.execute('display "final " 2+2')
    print("  final rc:", r.rc, "text:", repr(r.text[:40]))
except Exception as e:
    print("  final 崩溃:", type(e).__name__, str(e)[:120])
print("DONE")
