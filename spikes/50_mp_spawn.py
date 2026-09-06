"""P10 spike：验证 multiprocessing spawn + pystata 在 Windows 的可行性。

这是"会话隔离"的地基：每个 session 一个子进程，子进程内 init pystata。
关键不确定点：
1. Windows spawn 子进程能否 init pystata（license/路径/重新 import）；
2. 子进程执行命令、通过 Queue 回传结果能否工作；
3. 子进程 init 耗时（影响会话创建体验）。
"""
import sys, os, time, multiprocessing as mp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def worker_main(request_q, response_q):
    """worker 子进程：init pystata，循环处理命令。"""
    from stata_mcp.stata.pystata_backend import PystataBackend

    backend = PystataBackend()
    t0 = time.time()
    backend.init()
    print(f"[worker] init 耗时 {time.time()-t0:.1f}s", file=sys.stderr)

    while True:
        msg = request_q.get()
        if msg["type"] == "execute":
            r = backend.execute(msg["code"])
            response_q.put({"id": msg["id"], "text": r.text, "rc": r.rc})
        elif msg["type"] == "break":
            backend.interrupt()
            response_q.put({"id": msg["id"], "ok": True})
        elif msg["type"] == "close":
            break


def main():
    request_q = mp.Queue()
    response_q = mp.Queue()

    # Windows 用 spawn
    p = mp.Process(target=worker_main, args=(request_q, response_q))
    p.start()
    print("[main] worker 已启动，pid:", p.pid)

    # 等 worker init 完（发第一条命令）
    t0 = time.time()
    request_q.put({"id": 1, "type": "execute", "code": "display 2+2"})
    resp = response_q.get(timeout=60)
    print(f"[main] 第一条命令耗时 {time.time()-t0:.1f}s，rc={resp['rc']} text={resp['text'].strip()!r}")

    # 持久状态：worker 内数据跨调用保留
    request_q.put({"id": 2, "type": "execute", "code": "scalar _x = 40 + 2"})
    response_q.get(timeout=30)
    request_q.put({"id": 3, "type": "execute", "code": "display _x"})
    resp = response_q.get(timeout=30)
    print(f"[main] 持久状态 _x = {resp['text'].strip()!r} (期望 42)")

    # 中断
    import threading
    def run_long():
        request_q.put({"id": 4, "type": "execute", "code": "sleep 8000"})
        try:
            response_q.get(timeout=30)
        except Exception:
            pass
    t = threading.Thread(target=run_long, daemon=True)
    t.start()
    time.sleep(0.8)
    request_q.put({"id": 5, "type": "break"})
    response_q.get(timeout=30)
    print("[main] break 已发送")

    # 关闭
    request_q.put({"id": 99, "type": "close"})
    p.join(timeout=10)
    print("[main] worker 退出，exitcode:", p.exitcode)
    print("MP_SPAWN_OK")


if __name__ == "__main__":
    main()
