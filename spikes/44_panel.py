import sys
sys.path.insert(0, "src")
from stata_mcp.stata.pystata_backend import get_backend
from stata_mcp.tools.run import stata_run

b = get_backend()
# 构造一个简单面板：id × time
b.execute("clear")
b.execute("set obs 200")
b.execute("gen id = mod(_n-1, 20) + 1")
b.execute("gen t = ceil(_n/20)")
b.execute("gen y = 2 + 0.5*id + rnormal()")
b.execute("gen x = 1 + 0.3*id + rnormal()")
b.execute("xtset id t")

for cmd in [
    "xtreg y x, fe",           # 固定效应
    "xtreg y x, re",           # 随机效应
    "xtreg y x, fe cluster(id)",  # 聚类标准误
]:
    r = stata_run({"code": cmd}, None)
    if r.rc != 0:
        print(f"[FAIL rc={r.rc}] {cmd}  {r.text.strip()[:60]}")
    elif r.structured is None:
        print(f"[NO-STRUCT] {cmd}")
    else:
        vars_ = [c["var"] for c in r.structured.get("coefs", [])]
        print(f"[OK] {cmd} vars={vars_}")
