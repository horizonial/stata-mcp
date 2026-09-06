import sys
sys.path.insert(0, "src")
from stata_mcp.stata.pystata_backend import get_backend

b = get_backend()
b.execute("sysuse auto, clear")

# 测不同命令的 ereturn list，看 e() 标量是否是"统一的命名标量"
for cmd in ["tobit mpg weight, ll(10)", "nbreg rep78 weight", "oprobit rep78 weight"]:
    b.execute(cmd)
    r = b.execute("ereturn list")
    # 只看 scalars 段
    print(f"=== {cmd} 的 e() 标量 ===")
    for line in r.text.splitlines():
        s = line.strip()
        if s.startswith("e(") and "=" in s:
            print("  " + s[:70])
