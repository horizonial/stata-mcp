"""PystataBackend：进程内嵌 Stata 引擎的执行后端（D1/D3/D5 落地）。

为什么这样写（均来自已验证 spike，不复刻未验证的写法）：
- 点火：sys.path 插入 utilities 后 ``config.init(edition="mp")``（spike01）。
- 输出捕获：pystata 输出走 Python ``sys.stdout``，进程内用 ``io.StringIO`` 交换
  即可完整捕获（spike03 定案的 D3，不依赖文件日志 / log close _all）。
- rc 捕获：``capture noisily ...`` 后 ``scalar _stata_mcp_rc = _rc`` 读全局
  scalar（spike02 定案的 D5，避开 local 作用域 rc 恒 0 的坑）。
- 引擎是进程内单例：init/execute 一律用 self._lock 串行化。

惰性初始化：首次 execute 才真正拉起引擎，import / 列工具不占 license。
"""
from __future__ import annotations

import io
import sys
import threading

from .. import config
from .backend import ExecutionResult


class PystataBackend:
    """pystata 驱动。结构化地满足 ExecutionBackend 协议（不显式继承）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = False
        self._stata = None

    # ---- 生命周期 ---------------------------------------------------------

    def init(self) -> None:
        with self._lock:
            self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        """点火。调用方须已持有 self._lock。"""
        if self._ready:
            return
        sys.path.insert(0, config.utilities_dir())
        # 清掉可能残留的 pystata 模块，避免撞到 PyPI 同名假包或旧路径（spike01）
        for mod in list(sys.modules):
            if mod == "pystata" or mod.startswith("pystata."):
                del sys.modules[mod]

        from pystata import config as _pystata_config

        try:
            _pystata_config.init(edition="mp")
        except TypeError:
            # 不同 pystata 版本的 init 签名略有差异，退化用默认
            _pystata_config.init()

        from pystata import stata

        self._stata = stata
        self._ready = True

    def close(self) -> None:
        # pystata 没有进程内卸载/停引擎的公开 API；引擎跟随本进程退出释放，
        # 因此 close 是空操作（也刻意不把 _ready 置 False，config.init 只能调一次）。
        pass

    def capabilities(self) -> dict:
        return {
            "name": "pystata",
            "persistent_session": True,  # 内存里的数据/标量跨调用保留
            "struct_results": False,  # P3 起
            "graphs": False,  # P5
            "interrupt": False,  # P5（独立线程 + sfi.breakIn）
            "capture": "stdout-swap",  # D3
        }

    # ---- 执行 ---------------------------------------------------------------

    def execute(self, code: str, *, timeout: float | None = None) -> ExecutionResult:
        """执行一段 Stata 代码，返回捕获文本与 rc。

        ``timeout`` 在 P2 是保留参数：同步执行下无法真正中断，
        超时/中断由 P5 用独立线程跑命令 + ``sfi.breakIn`` 实现。
        """
        if not isinstance(code, str) or not code.strip():
            return ExecutionResult(text="", rc=0)

        with self._lock:
            self._ensure_initialized()
            from sfi import Scalar

            before = self._e_fingerprint()

            buf = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = buf
            try:
                try:
                    # capture 吞掉错误让引擎继续存活；noisily 让错误消息仍可见
                    self._stata.run(self._wrap_capture(code))
                except SystemError as exc:
                    # 极少数错误会逃过 capture（如用户 exit / 改 delimiter）：
                    # pystata 会把该次输出塞进异常消息而非 stdout
                    buf.write(str(exc))
                    buf.write("\n")
            finally:
                sys.stdout = old_stdout

            # 读 rc：单独一条命令做全局 scalar 拷贝，避免读值本身抛错
            rc = 0
            try:
                self._stata.run("capture noisily scalar _stata_mcp_rc = _rc")
                rc = int(Scalar.getValue("_stata_mcp_rc"))
            except Exception:
                rc = 601  # 兜底：引擎状态异常读不到 rc 时给通用错误码

            after = self._e_fingerprint()

        return ExecutionResult(
            text=buf.getvalue(), rc=rc, e_changed=(before != after)
        )

    def _e_fingerprint(self):
        """读当前 e() 结果的最小指纹，用于判断"本次是否改了估计结果"。

        指纹取 e(cmd)/e(depvar)/e(N)/e(df_r) 四元组：估计命令会改变它们，
        非估计命令（display/summarize/use 等）不会。读不到（还没跑过估计
        命令，或非 pystata 引擎）返回 None。任何异常都归一为 None，不冒泡。
        """
        try:
            from sfi import Macro, Scalar

            return (
                Macro.getGlobal("e(cmd)"),
                Macro.getGlobal("e(depvar)"),
                Scalar.getValue("e(N)"),
                Scalar.getValue("e(df_r)"),
            )
        except Exception:
            return None

    # ---- 中断（P5b） ----------------------------------------------------------

    def interrupt(self) -> None:
        """打断当前正在执行的命令（从另一线程调用）。

        P5b 实测（spike30/31）定案：pystata 没有 sfi.breakIn，真正的中断 API 是
        ``pystata.config.stlib.StataSO_SetBreak``（线程安全 C 函数，无参无返回）。
        在独立线程跑命令、主线程调 SetBreak 即可打断；打断后命令 rc=1、引擎存活、
        可继续执行；串行多次打断也安全。

        关键：本方法**不持 self._lock**。因为它要打断的正是"另一个线程里持锁
        阻塞在 stata.run 的 execute"，若这里也去抢锁就死锁。_ready 的读在 GIL
        下原子；引擎未初始化时没有命令可打断，直接返回。
        """
        if not self._ready:
            return
        from pystata import config as _cfg

        fn = _cfg.stlib.StataSO_SetBreak
        fn.argtypes = []
        fn.restype = None
        fn()

    # ---- 内部工具 -------------------------------------------------------------

    @staticmethod
    def _wrap_capture(code: str) -> str:
        """把用户代码包进 ``capture noisily``。

        单行命令直接前缀（与 spike02 实测一致）；
        多行代码用大括号块，让任何一行出错都被 capture 捕获而不杀死引擎。
        """
        code = code.strip("\n").strip()
        if "\n" not in code:
            return f"capture noisily {code}"
        return f"capture noisily {{\n{code}\n}}"


_default: PystataBackend | None = None


def get_backend() -> PystataBackend:
    """进程内默认单例。引擎惰性启动：首次 execute 才点火，占 license。"""
    global _default
    if _default is None:
        _default = PystataBackend()
    return _default
