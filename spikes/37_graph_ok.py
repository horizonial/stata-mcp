import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stata_mcp.stata.pystata_backend import get_backend

b = get_backend()
b.execute("sysuse auto, clear")
b.execute("twoway (scatter mpg weight), name(g1)")

tmp = tempfile.mkdtemp(prefix="smcp_graph_")
# 正斜杠绝对路径
png = os.path.join(tmp, "test.png").replace("\\", "/")
svg = os.path.join(tmp, "test.svg").replace("\\", "/")

r = b.execute(f'graph export "{png}", replace')
print("[PNG] rc:", r.rc, "存在:", os.path.exists(png.replace("/", "\\")), "大小:", os.path.getsize(png.replace("/","\\")) if os.path.exists(png.replace("/","\\")) else 0)

r = b.execute(f'graph export "{svg}", replace')
print("[SVG] rc:", r.rc, "存在:", os.path.exists(svg.replace("/", "\\")))

# 图名检测：graph dir 输出
r = b.execute("graph dir")
print("[graph dir] text:", repr(r.text))
