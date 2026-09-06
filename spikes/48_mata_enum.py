import sys
sys.path.insert(0, "src")
from stata_mcp.stata.pystata_backend import get_backend

b = get_backend()
b.execute("sysuse auto, clear")
b.execute("tobit mpg weight, ll(10)")

# 探测 Mata 有没有枚举 e() 标量的方法
mata = r'''
mata:
    "st_dir('escalars'):", st_dir("escalars")
    "st_dir('scalars'):", st_dir("scalars")
    "st_dir('ereturns'):", st_dir("ereturns")
end
'''
r = b.execute(mata)
print("rc:", r.rc)
print(r.text[:800])
