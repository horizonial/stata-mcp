import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stata_mcp.stata.pystata_backend import get_backend
from stata_mcp.tools.run import stata_run
from stata_mcp.tools.break_cmd import stata_break
from stata_mcp.tools.task_status import stata_task_status

b = get_backend()
b.execute("display 1")

# 1) background 提交长命令
r = stata_run({"code": "sleep 8000", "background": True}, None)
job = r.meta.get("job_id")
print("=== [1] background 提交 ===")
print("job_id:", job, "text:", r.text[:50])

# 2) 立即查状态（应 running）
time.sleep(0.3)
r = stata_task_status({"job_id": job}, None)
print("\n=== [2] 状态（应 running） ===")
print("text:", r.text, "rc:", r.rc)

# 3) break 打断
r = stata_break({}, None)
print("\n=== [3] break ===")
print("rc:", r.rc, "text:", r.text[:40])

# 4) 查状态（应 done，rc=1）
time.sleep(0.5)
r = stata_task_status({"job_id": job}, None)
print("\n=== [4] 打断后状态 ===")
print("text:", r.text[:60], "rc:", r.rc)

# 5) 同步跑一个 background=false 的完整任务
r = stata_run({"code": "display 2+2", "background": True}, None)
job2 = r.meta["job_id"]
time.sleep(1.0)
r = stata_task_status({"job_id": job2}, None)
print("\n=== [5] 完整后台任务 ===")
print("text:", r.text, "rc:", r.rc)

print("\nDONE")
