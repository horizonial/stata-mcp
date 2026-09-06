"""P12 受限模式单元测试：防有害文件注入（shell 逃逸/文件删除/越权路径）。"""
from __future__ import annotations

import os
import tempfile
import unittest

from stata_mcp.guard.data_path import DataPathAuditor
from stata_mcp.guard.restrict import check_dangerous, check_file_paths, restrict


def _auditor() -> DataPathAuditor:
    # 允许 cwd + 一个临时目录
    return DataPathAuditor(allowed_dirs=[os.getcwd()])


class DangerousCommandTests(unittest.TestCase):
    def test_shell_blocked(self):
        ok, _ = check_dangerous("shell del C:\\important.txt")
        self.assertFalse(ok)

    def test_winexec_blocked(self):
        ok, _ = check_dangerous("winexec notepad.exe")
        self.assertFalse(ok)

    def test_erase_blocked(self):
        ok, _ = check_dangerous("erase C:\\data.dta")
        self.assertFalse(ok)

    def test_shell_escape_bang_blocked(self):
        ok, _ = check_dangerous("! del C:\\important.txt")
        self.assertFalse(ok)

    def test_bysort_prefix_bypass_fixed(self):
        # mcp-for-stata 的已知绕过：bysort 前缀让 shell 逃过检测。我们的实现剥
        # by varlist: 前缀，所以这里必须拦截。
        ok, _ = check_dangerous("bysort foreign: shell rm -rf /")
        self.assertFalse(ok)

    def test_capture_prefix_bypass_fixed(self):
        ok, _ = check_dangerous("capture noisily shell del C:\\x")
        self.assertFalse(ok)

    def test_normal_regress_allowed(self):
        ok, _ = check_dangerous("regress mpg weight price")
        self.assertTrue(ok)

    def test_use_command_not_blocked_by_dangerous(self):
        # use 不是"危险命令"，由 check_file_paths 单独审计
        ok, _ = check_dangerous('use "auto.dta"')
        self.assertTrue(ok)

    def test_comment_only_line_ignored(self):
        ok, _ = check_dangerous("* shell del C:\\x")
        self.assertTrue(ok)


class FilePathTests(unittest.TestCase):
    def test_outside_path_blocked(self):
        outside = os.path.join(tempfile.gettempdir(), "evil.dta")
        ok, _ = check_file_paths(f'use "{outside}"', _auditor())
        self.assertFalse(ok)

    def test_inside_cwd_allowed(self):
        ok, _ = check_file_paths('use "data.dta"', _auditor())
        self.assertTrue(ok)

    def test_save_outside_blocked(self):
        outside = os.path.join(tempfile.gettempdir(), "out.dta")
        ok, _ = check_file_paths(f'save "{outside}"', _auditor())
        self.assertFalse(ok)

    def test_import_delimited_path_checked(self):
        outside = os.path.join(tempfile.gettempdir(), "x.csv")
        ok, _ = check_file_paths(f'import delimited "{outside}"', _auditor())
        self.assertFalse(ok)


class RestrictTests(unittest.TestCase):
    def test_restrict_combines_both(self):
        ok, _ = restrict('use "data.dta"\nregress mpg weight', _auditor())
        self.assertTrue(ok)

    def test_restrict_blocks_shell(self):
        ok, reason = restrict("shell dir", _auditor())
        self.assertFalse(ok)
        self.assertIn("shell", reason)


class AuditRegressionTests(unittest.TestCase):
    """P16 审计修复回归测试（分号/无引号绕过、background 绕过、localhost SSRF）。"""

    def test_semicolon_hides_shell_in_middle_of_line(self):
        # 审计#4：`use x; shell rm` 分号藏命令——必须拦
        ok, _ = check_dangerous('use "auto.dta"; shell rm -rf /')
        self.assertFalse(ok)

    def test_semicolon_after_capture_prefix(self):
        ok, _ = check_dangerous("capture noisily use x; winexec notepad")
        self.assertFalse(ok)

    def test_shell_as_midword_not_false_positive(self):
        # 不把正常文本/命令里的子串当 shell（词边界要求）
        ok, _ = check_dangerous('display "shell is a word here"')
        self.assertTrue(ok)

    def test_unquoted_outside_path_blocked(self):
        # 审计#4：无引号路径 `use C:\evil.dta` 也要审计
        outside = os.path.join(tempfile.gettempdir(), "evil.dta")
        ok, _ = check_file_paths(f"use {outside}", _auditor())
        self.assertFalse(ok)

    def test_unquoted_relative_in_cwd_allowed(self):
        ok, _ = check_file_paths("use auto.dta", _auditor())
        self.assertTrue(ok)

    def test_background_restricted_blocks_shell(self):
        # 审计#1：background=True 提交 shell，restricted 必须拦截（不触引擎）
        from stata_mcp.tools.run import stata_run

        env = stata_run({"code": "shell del x", "background": True, "restricted": True}, None)
        self.assertEqual(env.rc, 1)
        self.assertIn("blocked", env.text)

    def test_background_restricted_allows_normal(self):
        from stata_mcp.tools.run import stata_run

        # 正常命令走后台提交（不执行，仅验证没被误拦到 error）
        env = stata_run({"code": "display 1", "background": True, "restricted": True}, None)
        self.assertNotEqual(env.text, "")  # 未被拦
        self.assertIn("background", env.text)


class BoolStrictTests(unittest.TestCase):
    """P16b #4：bool 参数严格校验（字符串 'false' 不得当 True）。"""

    def _as_bool(self, *a):
        from stata_mcp.tools.run import _as_bool

        return _as_bool(*a)

    def test_false_strings_are_false(self):
        self.assertFalse(self._as_bool("false"))
        self.assertFalse(self._as_bool("False"))
        self.assertFalse(self._as_bool("0"))
        self.assertFalse(self._as_bool("no"))

    def test_true_strings_are_true(self):
        self.assertTrue(self._as_bool("true"))
        self.assertTrue(self._as_bool("True"))
        self.assertTrue(self._as_bool("1"))
        self.assertTrue(self._as_bool("on"))

    def test_real_bool_passthrough(self):
        self.assertFalse(self._as_bool(False))
        self.assertTrue(self._as_bool(True))

    def test_non_bool_type_not_trusted(self):
        self.assertFalse(self._as_bool(0))
        self.assertFalse(self._as_bool(1))  # int 不当 bool

    def test_none_uses_default(self):
        self.assertTrue(self._as_bool(None, True))
        self.assertFalse(self._as_bool(None, False))


class SsrfAuditTests(unittest.TestCase):
    """审计#6：URL 守卫补 localhost/内网域名。"""

    def test_localhost_https_blocked(self):
        a = DataPathAuditor(allowed_dirs=[os.getcwd()], enable_url_guard=True)
        self.assertFalse(a.check_url("https://localhost:4000/x.csv"))
        self.assertFalse(a.check_url("https://127.0.0.1/x.dta"))

    def test_local_domain_blocked(self):
        a = DataPathAuditor(allowed_dirs=[os.getcwd()], enable_url_guard=True)
        self.assertFalse(a.check_url("https://router.local/secret"))

    def test_cloud_metadata_blocked(self):
        a = DataPathAuditor(allowed_dirs=[os.getcwd()], enable_url_guard=True)
        self.assertFalse(a.check_url("https://metadata.google.internal/computeMetadata/v1/"))

    def test_public_https_without_whitelist_allowed(self):
        a = DataPathAuditor(allowed_dirs=[os.getcwd()], enable_url_guard=True)
        self.assertTrue(a.check_url("https://stats.oecd.org/data.csv"))

    def test_whitelist_still_respected(self):
        a = DataPathAuditor(
            allowed_dirs=[os.getcwd()], enable_url_guard=True,
            allowed_hosts=["example.com"],
        )
        self.assertTrue(a.check_url("https://data.example.com/x.csv"))
        self.assertFalse(a.check_url("https://other.org/x.csv"))


if __name__ == "__main__":
    unittest.main()
