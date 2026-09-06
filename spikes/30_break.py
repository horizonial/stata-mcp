"""P5b 探测：StataSO_SetBreak 中断机制。

测三件事：
  A) 独立线程跑长命令，主线程调 SetBreak，能否打断？
  B) 打断后 stata.run 抛什么？引擎还活着吗？
  C) 打断后还能继续跑新命令吗？

注意 hanlulong 调研警告：SetBreak 只能调一次，多次 SIGSEGV。先只调一次。
"""
import sys, os, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stata_mcp.stata.pystata_backend import get_backend

b = get_backend()
b.execute("display 1")
import pystata.config as cfg
SetBreak = cfg.stlib.StataSO_SetBreak

# 设置 SetBreak 的参数签名（ctypes 需要）
SetBreak.argtypes = []
SetBreak.restype = None

def run_long():
    # 长命令：sleep 10000ms = 10秒（Stata 的 sleep 命令）
    try:
        b.execute("sleep 10000")
        return "completed normally"
    except Exception as e:
        return f"raised {type(e).__name__}: {e}"

print("=== A) 独立线程跑 sleep 10000，1 秒后 break ===")
t0 = time.time()
t = threading.Thread(target=run_long, daemon=True)
result_holder = {}
t.start()
time.sleep(1.0)
print(f"  主线程 {time.time()-t0:.1f}s 调 SetBreak")
SetBreak()
t.join(timeout=5)
print(f"  线程 join 后存活: {t.is_alive()}，耗时 {time.time()-t0:.1f}s")
print("  (结果无法从 daemon 线程拿回，看下面 B)")

print("\n=== B) 同步打断 + 看异常 ===")
def run_long_sync():
    try:
        r = b.execute("sleep 10000")
        return ("ok", r.rc)
    except Exception as e:
        return ("exc", type(e).__name__, str(e)[:120])

t2 = threading.Thread(target=lambda: result_holder.__setitem__("b", run_long_sync()), daemon=True)
result_holder.clear()
t2.start()
time.sleep(1.0)
SetBreak()
t2.join(timeout=5)
print("  结果:", result_holder.get("b"))
print("  线程存活:", t2.is_alive())

print("\n=== C) break 后引擎还能跑新命令吗 ===")
try:
    r = b.execute('display "after break: " 2+2')
    print("  新命令 rc:", r.rc, "text:", repr(r.text[:60]))
except Exception as e:
    print("  新命令失败:", type(e).__name__, str(e)[:120])

print("\nDONE")
