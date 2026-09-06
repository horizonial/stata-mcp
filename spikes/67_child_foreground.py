import sys, os
sys.path.insert(0, os.path.abspath("src"))
from stata_mcp.session import SessionManager
print("child starting", flush=True)
mgr = SessionManager(max_sessions=1)
s = mgr.get_or_create("default")
print("child executing display", flush=True)
r = s.execute("display 1", timeout=40)
print("child rc", r.rc, "text", repr(r.text[:60]), flush=True)
print("child pid", s._proc.pid, flush=True)
os._exit(0)
