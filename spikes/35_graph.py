import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stata_mcp.stata.pystata_backend import get_backend

b = get_backend()
b.execute("sysuse auto, clear")

tmp = tempfile.mkdtemp(prefix="stata_mcp_graph_")
print("tmp dir:", tmp)

# 1) 画图
r = b.execute("twoway (scatter mpg weight)")
print("\n=== [1] 画图 rc:", r.rc, "===")

# 2) graph dir 检测
r = b.execute("graph dir")
print("\n=== [2] graph dir ===")
print("rc:", r.rc, "text:", repr(r.text[:200]))

# 3) graph export PNG（关键：是否崩）
png = os.path.join(tmp, "test.png")
r = b.execute(f'graph export "{png}", replace')
print("\n=== [3] export PNG ===")
print("rc:", r.rc, "text:", repr(r.text[:150]))
print("文件存在:", os.path.exists(png), "大小:", os.path.getsize(png) if os.path.exists(png) else 0)

# 4) graph export SVG
svg = os.path.join(tmp, "test.svg")
r = b.execute(f'graph export "{svg}", replace')
print("\n=== [4] export SVG ===")
print("rc:", r.rc, "text:", repr(r.text[:150]))
print("文件存在:", os.path.exists(svg), "大小:", os.path.getsize(svg) if os.path.exists(svg) else 0)

print("\nDONE")
