import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stata_mcp.stata.pystata_backend import get_backend
from stata_mcp.tools.inspect_data import stata_inspect_data

b = get_backend()
b.execute("sysuse auto, clear")

# describe 结构化（修复后）
r = stata_inspect_data({"action": "describe"}, None)
print("=== describe 结构化 ===")
print("rc:", r.rc)
print("structured:", json.dumps(r.structured, ensure_ascii=False)[:300])

# summarize 单变量（真实变量名 mpg）
r = stata_inspect_data({"action": "summarize", "variables": ["mpg"]}, None)
print("\n=== summarize mpg 结构化 ===")
print("rc:", r.rc)
print("structured:", json.dumps(r.structured, ensure_ascii=False))

# 变量名注入测试：非法变量名应拒绝
r = stata_inspect_data({"action": "summarize", "variables": ["mpg; drop _all"]}, None)
print("\n=== 非法变量名（应拒绝） ===")
print("rc:", r.rc, "text:", r.text[:80])
