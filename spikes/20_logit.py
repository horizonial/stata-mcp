import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stata_mcp.stata.pystata_backend import get_backend
from stata_mcp.tools.run import stata_run
b = get_backend()
b.execute("sysuse auto, clear")
r = stata_run({"code": "logit foreign weight price"}, None)
print("rc:", r.rc)
print(json.dumps(r.structured, ensure_ascii=False, indent=1) if r.structured else "structured=None")
