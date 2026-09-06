"""P16 验证：真多会话隔离（session_id 路由）+ 启动握手耗时。"""
import sys, time
sys.path.insert(0, "src")
from stata_mcp.session import SessionManager


def main():
    mgr = SessionManager(max_sessions=2)
    try:
        t0 = time.time()
        a = mgr.get_or_create("ideaA")
        b = mgr.get_or_create("ideaB")
        r = a.execute("display 2+2")  # 首次：含启动握手
        print("[1] ideaA 首执行:", repr(r.text.strip()), f"耗时 {time.time()-t0:.1f}s")
        # 隔离：a 里建 scalar，b 里应没有
        a.execute("scalar _x = 42")
        rb = b.execute("display _x")
        print("[2] a 建 _x，b 读 _x → rc:", rb.rc, "(期望非0=隔离正确)")
        # b 也能正常跑
        rb2 = b.execute("display 3*3")
        print("[3] ideaB 独立执行:", repr(rb2.text.strip()))
        print("    两会话 live:", mgr.stats())
    finally:
        mgr.close_all()


if __name__ == "__main__":
    main()
