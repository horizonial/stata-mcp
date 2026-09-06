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


if __name__ == "__main__":
    unittest.main()
