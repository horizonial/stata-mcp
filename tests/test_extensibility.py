"""P6 扩展性压力测试：证明 ARCHITECTURE §5 的三个抽象真的可插拔。

验证三个承诺，全部不碰 Stata 引擎（用 stub）：
1. ExecutionBackend 可插拔：工具 handler 通过 ctx.backend 取后端，不硬编码 pystata。
2. Tool 可扩展：加工具 = 加模块 + 注册，不改 core。
3. ResultParser 可扩展：加命令解析器 = 加模块 + register，不改 core。
"""
from __future__ import annotations

import unittest

from stata_mcp.session import SessionResult
from stata_mcp.envelope import Envelope


class StubBackend:
    """模拟 Session：实现 execute/interrupt/snapshot，不碰 Stata。"""

    def __init__(self):
        self.calls: list[str] = []

    def init(self) -> None:
        pass

    def execute(self, code: str, *, timeout=None) -> SessionResult:
        self.calls.append(code)
        return SessionResult(text=f"[stub] {code}", rc=0)

    def snapshot(self) -> dict:
        return {}

    def interrupt(self) -> None:
        pass

    def close(self) -> None:
        pass

    def capabilities(self) -> dict:
        return {"name": "stub", "persistent_session": True}


class _Ctx:
    """模拟未来 Session 上下文：携带 backend。"""

    def __init__(self, backend):
        self.backend = backend


class TestBackendPluggability(unittest.TestCase):
    """承诺 1：工具层 backend 无关，可注入任意 backend。"""

    def test_stata_run_uses_injected_backend(self):
        from stata_mcp.tools.run import stata_run

        stub = StubBackend()
        env = stata_run({"code": "display 1"}, _Ctx(stub))
        self.assertEqual(env.rc, 0)
        self.assertIn("[stub] display 1", env.text)
        self.assertEqual(stub.calls, ["display 1"])

    def test_stata_break_calls_injected_backend_interrupt(self):
        from stata_mcp.tools.break_cmd import stata_break

        class BreakRecordingBackend(StubBackend):
            def __init__(self):
                super().__init__()
                self.interrupted = False

            def interrupt(self):
                self.interrupted = True

        stub = BreakRecordingBackend()
        stata_break({}, _Ctx(stub))
        self.assertTrue(stub.interrupted)


class TestToolRegistryExtensibility(unittest.TestCase):
    """承诺 2：加工具 = 加模块 + 注册，不改 core。"""

    def test_register_new_tool_dynamically(self):
        from stata_mcp.tools import TOOLS, register

        schema = {"type": "object", "properties": {}, "required": []}

        @register("__test_echo", schema)
        def __test_echo(arguments, ctx=None) -> Envelope:
            return Envelope(text="echo", structured=None, rc=0,
                            error_class=None, graphs=[], meta={})

        self.assertIn("__test_echo", TOOLS)
        # 直接调用 handler，验证注册的是同一个对象
        self.assertEqual(TOOLS["__test_echo"].handler({}, None).text, "echo")
        # 清理，避免污染其它测试
        del TOOLS["__test_echo"]

    def test_all_real_tools_registered(self):
        from stata_mcp.tools import TOOLS

        expected = {
            "stata_run", "stata_load_data", "stata_inspect_data",
            "stata_get_results", "stata_export_graph",
            "stata_break", "stata_task_status",
        }
        self.assertTrue(expected.issubset(TOOLS.keys()))


class TestResultParserExtensibility(unittest.TestCase):
    """承诺 3：加结果解析器 = 加模块 + register，不改 core。"""

    def test_register_new_parser_dynamically(self):
        from stata_mcp.results.parser import PARSERS, register

        @register(("__fakecmd",))
        class _FakeParser:
            command_types = ("__fakecmd",)

            def parse(self, ctx):
                return {"cmd": "__fakecmd", "fake": True}

        self.assertIn("__fakecmd", PARSERS)
        self.assertEqual(PARSERS["__fakecmd"].parse(None)["fake"], True)
        del PARSERS["__fakecmd"]


if __name__ == "__main__":
    unittest.main()
