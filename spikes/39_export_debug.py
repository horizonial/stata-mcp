import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stata_mcp.stata.pystata_backend import get_backend

b = get_backend()
b.execute("sysuse auto, clear")
b.execute("twoway (scatter mpg weight), name(g1)")

# 手写命令，看哪种语法对
cmds = [
    'graph export "g1.png", name(g1) replace',
    'graph export "g1.png", replace name(g1)',
    'graph export "g1.png", replace',
    'graph export g1 "g1.png", replace',
]
for c in cmds:
    r = b.execute(c)
    print(f"[{c!r}] rc={r.rc}")
    if r.rc != 0:
        print("   ", r.text.strip()[-150:])
