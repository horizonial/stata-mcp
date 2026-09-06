import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stata_mcp.stata.pystata_backend import get_backend
from stata_mcp.tools.run import stata_run

b = get_backend()
b.execute("sysuse auto, clear")

CMDS = [
    "regress mpg weight price displacement",
    "logit foreign weight price",
    "probit foreign weight price",
    "oprobit rep78 weight price",
    "mlogit rep78 weight price",
    "poisson rep78 weight price",
    "areg mpg weight price, absorb(rep78)",
    "ivregress 2sls mpg weight (price = displacement)",
    "tobit mpg weight price, ll(10)",
]

for cmd in CMDS:
    r = stata_run({"code": cmd}, None)
    if r.rc != 0:
        print(f"[FAIL rc={r.rc}] {cmd[:45]}")
        print(f"    {r.text.strip()[:80]}")
    elif r.structured is None:
        print(f"[NO-STRUCT] {cmd[:45]}")
    else:
        ncoef = len(r.structured.get("coefs", []))
        varnames = [c["var"] for c in r.structured.get("coefs", [])][:5]
        keys = [k for k in r.structured.keys() if k not in ("coefs", "cmd", "depvar")]
        print(f"[OK {ncoef}coef] {cmd[:38]} vars={varnames} stats={keys}")
