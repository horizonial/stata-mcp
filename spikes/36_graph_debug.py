import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stata_mcp.stata.pystata_backend import get_backend

b = get_backend()
b.execute("sysuse auto, clear")
b.execute("twoway (scatter mpg weight)")

print("=== graph dir 完整 ===")
r = b.execute("graph dir")
print(r.text)

print("=== graph describe 完整 ===")
r = b.execute("graph describe")
print(r.text[:500])

print("=== 尝试 graph export 到当前目录相对名 ===")
r = b.execute('graph export "test2.png", replace')
print("rc:", r.rc)
print(r.text[:300])

print("=== 看当前目录有没有 test2.png ===")
print("cwd:", os.getcwd(), "存在:", os.path.exists(os.path.join(os.getcwd(), "test2.png")))
