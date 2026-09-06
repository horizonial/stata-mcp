import sys
sys.path.insert(0, "src")
from stata_mcp.stata.pystata_backend import get_backend
from stata_mcp.tools.run import stata_run

b = get_backend()
b.execute("sysuse auto, clear")

# 这些命令都不在"名单"里（名单已删），纯靠"e(b) 存在"的通用机制
CMDS = [
    "anova mpg rep78 foreign",                # 方差分析
    "sureg (mpg weight) (price displacement)", # 联立方程
    "regress mpg i.rep78##c.weight",           # 因子×连续交互
    "nbreg rep78 weight",                      # 负二项（计数，非 poisson）
    "frmlogit mpg weight",                     # 不存在的命令，测错误处理
]

for cmd in CMDS:
    r = stata_run({"code": cmd}, None)
    if r.rc != 0:
        print(f"[FAIL rc={r.rc}] {cmd[:42]}")
    elif r.structured is None:
        print(f"[NO-STRUCT] {cmd[:42]}")
    else:
        vars_ = [c["var"] for c in r.structured.get("coefs", [])][:6]
        print(f"[OK {len(r.structured.get('coefs',[]))}coef] {cmd[:38]} -> {vars_}")
