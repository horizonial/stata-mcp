"""隔离测 job.py：创建 kill-on-close job、assign 一个 sleep 子进程、关句柄看它死没死。"""
import ctypes
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.abspath("src"))
from stata_mcp.platform.job import JobObject, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION


def main():
    # 起一个会睡 30s 的子进程
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    print("child pid:", child.pid, flush=True)

    job = JobObject()
    print("job handle:", job._handle, flush=True)
    job.assign(child.pid)
    time.sleep(1)
    print("assigned; child alive:", child.poll() is None, flush=True)

    # 关闭 job 句柄 → kill-on-close 应杀 child
    job.close()
    time.sleep(2)
    print("after job.close, child alive:", child.poll() is None,
          "| rc:", child.poll(), "(期望 None→已死)", flush=True)

    sys.exit(0 if child.poll() is not None else 1)


if __name__ == "__main__":
    main()
