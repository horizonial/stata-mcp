import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stata_mcp.stata.pystata_backend import get_backend
from stata_mcp.tools.export_graph import stata_export_graph

b = get_backend()
b.execute("sysuse auto, clear")
b.execute("twoway (scatter mpg weight), name(g1)")

# 导出 png（自动文件名）
r = stata_export_graph({"format": "png", "name": "g1"}, None)
print("=== [1] 导出 g1.png ===")
print("rc:", r.rc, "structured:", r.structured)

# 导出 svg（指定文件名）
r = stata_export_graph({"format": "svg", "name": "g1", "filename": "myscatter"}, None)
print("\n=== [2] 导出 myscatter.svg ===")
print("rc:", r.rc, "structured:", r.structured)

# 非法图名注入
r = stata_export_graph({"name": "g1; drop _all"}, None)
print("\n=== [3] 非法图名（应拒绝） ===")
print("rc:", r.rc, "text:", r.text[:60])
