"""P5a 数据工具静态验证（标准库 unittest，无 pytest 依赖；不跑 Stata 引擎）。

用假 backend（RecordingBackend）做 handler 逻辑验证：
- 审计拒绝 / 变量名非法 / action 非法等"不进引擎"路径；
- 命令构造（use/import delimited/import excel、clear 开关、varlist 拼装）；
- 成功路径结构化静默降级（本机无 pystata/sfi → 结构化为 None，不崩溃）；
- get_results 的 return list 文本解析（纯函数）。

运行：``.venv/Scripts/python.exe -m unittest tests.test_tools_p5a -v``
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest

import stata_mcp.tools.load_data as ld
import stata_mcp.tools.inspect_data as idata
import stata_mcp.tools.get_results as gr
from stata_mcp.session import SessionResult
from stata_mcp.tools.load_data import stata_load_data
from stata_mcp.tools.inspect_data import stata_inspect_data
from stata_mcp.tools.get_results import stata_get_results

# 置入 data dir 的临时目录父路径（放系统 temp，必然不在 cwd 内）
TMP_ROOT = tempfile.mkdtemp(prefix="p5a_tools_")


def tearDownModule() -> None:
    """模块级清理：删掉父临时目录（各用例已用 addCleanup 清掉自己那层）。"""
    try:
        os.rmdir(TMP_ROOT)
    except OSError:
        pass


class RecordingBackend:
    """模拟 Session：记录 execute，返回 SessionResult；snapshot 返回可配置 dict。"""

    def __init__(self, text: str = "", rc: int = 0, structured=None, snap: dict | None = None) -> None:
        self.calls: list[str] = []
        self._text = text
        self._rc = rc
        self._structured = structured
        self._snap = snap or {}

    def execute(self, code: str, *, timeout: float | None = None) -> SessionResult:
        self.calls.append(code)
        return SessionResult(text=self._text, rc=self._rc, structured=self._structured)

    def snapshot(self) -> dict:
        return dict(self._snap)

    def interrupt(self) -> None:
        pass


def _ctx(backend) -> types.SimpleNamespace:
    return types.SimpleNamespace(backend=backend)


def _in_cwd_dir() -> str:
    """在服务器工作目录(cwd)内建一个临时子目录，用于"允许目录内"的用例。"""
    return tempfile.mkdtemp(prefix="p5a_inside_cwd_", dir=os.getcwd())


def _cfg(dirs=(), hosts=(), url_guard=True) -> dict:
    return {
        "security": {
            "allowed_data_dirs": list(dirs),
            "enable_url_guard": url_guard,
            "allowed_hosts": list(hosts),
        }
    }


class NoEngineImportTests(unittest.TestCase):
    def test_import_does_not_trigger_pystata_or_sfi(self) -> None:
        self.assertNotIn("pystata", sys.modules)
        self.assertNotIn("sfi", sys.modules)

    def test_tools_registered(self) -> None:
        from stata_mcp.tools import TOOLS

        for name in ("stata_load_data", "stata_inspect_data", "stata_get_results"):
            self.assertIn(name, TOOLS, name)


# ---- stata_load_data -------------------------------------------------------

class LoadDataTests(unittest.TestCase):
    def setUp(self) -> None:
        # 固定配置，绕开用户机器上真实 config.toml / 环境变量，保证可复现
        self._saved_cfg = ld._cfg
        ld._cfg = _cfg()
        self.addCleanup(self._restore_cfg)

    def _restore_cfg(self) -> None:
        ld._cfg = self._saved_cfg

    def test_rejects_outside_cwd_local_path(self) -> None:
        outside = os.path.join(TMP_ROOT, "auto.dta")
        backend = RecordingBackend()
        env = stata_load_data({"source": outside}, _ctx(backend))
        self.assertEqual(env.rc, 1)
        self.assertIn("outside the allowed data directories", env.text)
        self.assertEqual(backend.calls, [])  # 审计拒绝 → 不构造命令

    def test_rejects_http_url(self) -> None:
        backend = RecordingBackend()
        env = stata_load_data({"source": "http://example.com/auto.dta"}, _ctx(backend))
        self.assertEqual(env.rc, 1)
        self.assertIn("data path guard", env.text)
        self.assertEqual(backend.calls, [])

    def test_rejects_embedded_quote(self) -> None:
        backend = RecordingBackend()
        env = stata_load_data({'source': 'C:/data/a"b.dta'}, _ctx(backend))
        self.assertEqual(env.rc, 1)
        self.assertIn("double quotes", env.text)
        self.assertEqual(backend.calls, [])

    def test_rejects_empty_source(self) -> None:
        env = stata_load_data({"source": "   "}, _ctx(RecordingBackend()))
        self.assertEqual(env.rc, 1)

    def test_dta_use_no_clear_and_shape_read_on_success(self) -> None:
        d = _in_cwd_dir()
        self.addCleanup(os.rmdir, d)
        src = os.path.join(d, "auto.dta")
        backend = RecordingBackend(rc=0, snap={"shape": {"N": 74, "k": 12}})
        env = stata_load_data({"source": src}, _ctx(backend))
        self.assertEqual(env.rc, 0)
        self.assertEqual(backend.calls[0], f'use "{os.path.abspath(src)}"')
        self.assertEqual(backend.calls[0].count(", clear"), 0)
        # rc==0 → 读形状（走 session.snapshot），structured 拼 source + shape
        self.assertEqual(env.structured, {"source": src, "N": 74, "k": 12})

    def test_dta_use_clear_true(self) -> None:
        d = _in_cwd_dir()
        self.addCleanup(os.rmdir, d)
        src = os.path.join(d, "auto.dta")
        backend = RecordingBackend(rc=0)
        stata_load_data({"source": src, "clear": True}, _ctx(backend))
        self.assertTrue(backend.calls[0].endswith(", clear"))

    def test_csv_import_delimited(self) -> None:
        d = _in_cwd_dir()
        self.addCleanup(os.rmdir, d)
        src = os.path.join(d, "x.csv")
        backend = RecordingBackend(rc=0)
        env = stata_load_data({"source": src}, _ctx(backend))
        self.assertEqual(backend.calls[0], f'import delimited "{os.path.abspath(src)}", clear')
        self.assertEqual(env.rc, 0)

    def test_xlsx_import_excel_firstrow(self) -> None:
        d = _in_cwd_dir()
        self.addCleanup(os.rmdir, d)
        src = os.path.join(d, "x.XLSX")  # 大小写不敏感
        backend = RecordingBackend(rc=0)
        stata_load_data({"source": src}, _ctx(backend))
        self.assertEqual(
            backend.calls[0], f'import excel "{os.path.abspath(src)}", firstrow clear'
        )

    def test_unknown_extension_falls_back_to_use(self) -> None:
        d = _in_cwd_dir()
        self.addCleanup(os.rmdir, d)
        src = os.path.join(d, "x.dat")
        backend = RecordingBackend(rc=0)
        stata_load_data({"source": src}, _ctx(backend))
        self.assertEqual(backend.calls[0], f'use "{os.path.abspath(src)}"')

    def test_https_url_dta_uses_url_as_is(self) -> None:
        backend = RecordingBackend(rc=0)
        stata_load_data({"source": "https://example.com/auto.dta"}, _ctx(backend))
        self.assertEqual(backend.calls[0], 'use "https://example.com/auto.dta"')

    def test_url_extension_uses_path_ignoring_query(self) -> None:
        backend = RecordingBackend(rc=0)
        stata_load_data(
            {"source": "https://example.com/auto.dta?raw=1", "clear": True}, _ctx(backend)
        )
        self.assertEqual(
            backend.calls[0], 'use "https://example.com/auto.dta?raw=1", clear'
        )
        backend2 = RecordingBackend(rc=0)
        stata_load_data({"source": "https://example.com/x.csv?raw=1"}, _ctx(backend2))
        self.assertEqual(
            backend2.calls[0], 'import delimited "https://example.com/x.csv?raw=1", clear'
        )

    def test_clear_string_false_not_truthy(self) -> None:
        # P16b #4：clear="false"（字符串）不得触发 `, clear` 覆盖数据
        d = _in_cwd_dir()
        self.addCleanup(os.rmdir, d)
        src = os.path.join(d, "auto.dta")
        backend = RecordingBackend(rc=0)
        stata_load_data({"source": src, "clear": "false"}, _ctx(backend))
        self.assertEqual(backend.calls[0].count(", clear"), 0)

    def test_clear_string_true_works(self) -> None:
        d = _in_cwd_dir()
        self.addCleanup(os.rmdir, d)
        src = os.path.join(d, "auto.dta")
        backend = RecordingBackend(rc=0)
        stata_load_data({"source": src, "clear": "true"}, _ctx(backend))
        self.assertTrue(backend.calls[0].endswith(", clear"))

    def test_rc_nonzero_no_shape_read_and_error_class(self) -> None:
        d = _in_cwd_dir()
        self.addCleanup(os.rmdir, d)
        src = os.path.join(d, "missing.dta")
        backend = RecordingBackend(text="file not found", rc=111)
        env = stata_load_data({"source": src}, _ctx(backend))
        self.assertEqual(env.rc, 111)
        self.assertEqual(env.error_class, "not_found")
        self.assertIsNone(env.structured)
        self.assertEqual(len(backend.calls), 1)  # 失败不读形状

    def test_config_allowed_data_dirs_extends_authorization(self) -> None:
        d_allowed = tempfile.mkdtemp(prefix="p5a_allowed_", dir=TMP_ROOT)
        d_other = tempfile.mkdtemp(prefix="p5a_other_", dir=TMP_ROOT)
        ld._cfg = _cfg(dirs=[d_allowed])
        self.addCleanup(os.rmdir, d_allowed)
        self.addCleanup(os.rmdir, d_other)

        backend = RecordingBackend(rc=0)
        env = stata_load_data(
            {"source": os.path.join(d_allowed, "a.dta")}, _ctx(backend)
        )
        self.assertEqual(env.rc, 0)
        self.assertEqual(len(backend.calls), 1)  # load 只 execute 一次，形状走 snapshot

        backend2 = RecordingBackend()
        env2 = stata_load_data(
            {"source": os.path.join(d_other, "b.dta")}, _ctx(backend2)
        )
        self.assertEqual(env2.rc, 1)
        self.assertEqual(backend2.calls, [])


# ---- stata_inspect_data ----------------------------------------------------

class InspectDataTests(unittest.TestCase):
    def test_invalid_action_rejected(self) -> None:
        backend = RecordingBackend()
        env = stata_inspect_data({"action": "freq"}, _ctx(backend))
        self.assertEqual(env.rc, 1)
        self.assertIn("unknown action", env.text)
        self.assertIn("describe", env.text)
        self.assertEqual(backend.calls, [])

    def test_invalid_varname_rejected(self) -> None:
        backend = RecordingBackend()
        env = stata_inspect_data(
            {"action": "summarize", "variables": ["mpg", "1bad"]}, _ctx(backend)
        )
        self.assertEqual(env.rc, 1)
        self.assertIn("invalid Stata variable name", env.text)
        self.assertEqual(backend.calls, [])  # 非法名字绝不拼进命令

    def test_variables_must_be_list(self) -> None:
        backend = RecordingBackend()
        env = stata_inspect_data({"action": "describe", "variables": 42}, _ctx(backend))
        self.assertEqual(env.rc, 1)
        self.assertEqual(backend.calls, [])

    def test_describe_default_no_vars(self) -> None:
        backend = RecordingBackend(text="", rc=0)
        env = stata_inspect_data({}, _ctx(backend))
        self.assertEqual(backend.calls[0], "describe, short")
        self.assertEqual(env.rc, 0)

    def test_describe_with_varlist(self) -> None:
        backend = RecordingBackend(text="", rc=0)
        env = stata_inspect_data(
            {"action": "describe", "variables": ["mpg", "weight"]}, _ctx(backend)
        )
        self.assertEqual(backend.calls[0], "describe mpg weight, short")
        # describe 指定了变量：结构化回显所请求的名字（无需 sfi）
        self.assertEqual(env.structured, {"action": "describe", "variables": ["mpg", "weight"]})

    def test_summarize_single_var(self) -> None:
        backend = RecordingBackend(text="", rc=0)
        env = stata_inspect_data(
            {"action": "summarize", "variables": ["mpg"]}, _ctx(backend)
        )
        self.assertEqual(backend.calls[0], "summarize mpg")
        # 本机无 sfi → 结构化静默降级 None（不崩溃）
        self.assertIsNone(env.structured)
        self.assertEqual(env.rc, 0)

    def test_summarize_string_tolerated_as_single(self) -> None:
        backend = RecordingBackend(text="", rc=0)
        stata_inspect_data({"action": "summarize", "variables": "mpg"}, _ctx(backend))
        self.assertEqual(backend.calls[0], "summarize mpg")

    def test_summarize_multi_vars_structured_none(self) -> None:
        backend = RecordingBackend(text="", rc=0)
        env = stata_inspect_data(
            {"action": "summarize", "variables": ["mpg", "weight"]}, _ctx(backend)
        )
        self.assertEqual(backend.calls[0], "summarize mpg weight")
        self.assertIsNone(env.structured)

    def test_codebook_with_varlist(self) -> None:
        backend = RecordingBackend(text="", rc=0)
        env = stata_inspect_data(
            {"action": "codebook", "variables": ["mpg", "weight"]}, _ctx(backend)
        )
        self.assertEqual(backend.calls[0], "codebook mpg weight, compact")
        self.assertIsNone(env.structured)  # codebook 无稳定结构化

    def test_rc_nonzero_error_class(self) -> None:
        backend = RecordingBackend(text="no variables defined", rc=111)
        env = stata_inspect_data({"action": "describe"}, _ctx(backend))
        self.assertEqual(env.rc, 111)
        self.assertEqual(env.error_class, "not_found")


# ---- stata_get_results -----------------------------------------------------


class GetResultsTests(unittest.TestCase):
    def test_handler_estimation_results(self) -> None:
        # snapshot 提供 e() 估计结果 → structured 直接用它
        snap = {"structured": {"cmd": "regress", "coefs": []}}
        backend = RecordingBackend(snap=snap)
        env = stata_get_results({}, _ctx(backend))
        self.assertEqual(env.rc, 0)
        self.assertEqual(env.structured["cmd"], "regress")
        self.assertIn("regress", env.text)

    def test_handler_r_class_results(self) -> None:
        snap = {"r_scalars": {"N": 74}, "r_macros": {"name": "auto.dta"}}
        backend = RecordingBackend(snap=snap)
        env = stata_get_results({}, _ctx(backend))
        self.assertEqual(env.rc, 0)
        self.assertEqual(env.structured["r_scalars"]["N"], 74)
        self.assertEqual(env.structured["r_macros"]["name"], "auto.dta")

    def test_handler_empty_session_graceful(self) -> None:
        backend = RecordingBackend(snap={})
        env = stata_get_results({}, _ctx(backend))
        self.assertEqual(env.rc, 0)
        self.assertIsNone(env.structured)
        self.assertIn("no results", env.text)

    def test_handler_session_reset_marked(self) -> None:
        backend = RecordingBackend(snap={"reset": True})
        env = stata_get_results({}, _ctx(backend))
        self.assertTrue(env.meta.get("session_reset"))


if __name__ == "__main__":
    unittest.main()
