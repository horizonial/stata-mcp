import sys, time
sys.path.insert(0, "src")
from stata_mcp.stata.pystata_backend import get_backend

b = get_backend()
b.execute("sysuse auto, clear")

# 方法1：findfile 定位 .sthlp
print("=== findfile regress.sthlp ===")
r = b.execute("findfile regress.sthlp")
print("output:", repr(r.text[:200]))
from sfi import Macro
print("s(filename):", repr(Macro.getLocal("s(filename)")))
try:
    from sfi import Scalar
    print("r(fn) scalar?", "n/a")
except Exception:
    pass
# return list
r = b.execute("return list")
print("return list:", r.text[:300])

# 方法2：help 命令（看是否返回文本还是开窗）
print("\n=== help regress (带 timeout) ===")
import threading
holder = {}
def run():
    try:
        r2 = b.execute("help regress")
        holder["r"] = (r2.rc, r2.text[:300])
    except Exception as e:
        holder["r"] = ("exc", str(e)[:200])
t = threading.Thread(target=run, daemon=True)
t.start()
t.join(timeout=15)
print("结果:", holder.get("r", ("超时/卡住",)))
