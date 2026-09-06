"""P4 guard 基础设施单元测试（标准库 unittest，无 pytest 依赖）。

覆盖：L1 参数校验、L3 DataPathAuditor、分层配置、隐私哈希日志。
所有测试都是纯逻辑，不 import pystata/sfi、不碰 Stata 引擎。
运行：``python -m unittest tests.test_guard -v``（在 .venv 里）。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from stata_mcp.config import get, get_security, load_config
from stata_mcp.guard import DataPathAuditor
from stata_mcp.guard.validate import (
    is_safe_filename,
    is_valid_identifier,
    is_valid_varname,
    validate_varname,
)
from stata_mcp.output.privacy import redact_path, redact_url


# ---- L1 参数校验 -----------------------------------------------------------

class VarnameTests(unittest.TestCase):
    def test_valid_varnames(self) -> None:
        for name in ("mpg", "x1", "_x", "A1_b2", "make", "a" * 32, "X"):
            self.assertTrue(is_valid_varname(name), name)

    def test_invalid_varnames(self) -> None:
        # "_" 单独一个下划线按"下划线开头 + 0 个后续字符"的规则是结构合法的
        # （规则只管字符集，不管 Stata 保留名），所以不放这里
        for name in ("", "1x", "x y", "x-y", "var name", "a" * 33, "x.dta", None, 42):
            self.assertFalse(is_valid_varname(name), repr(name))

    def test_validate_varname_ok_passes(self) -> None:
        validate_varname("mpg")  # 不抛即通过

    def test_validate_varname_raises_value_error(self) -> None:
        for bad in ("1x", "x y", "a" * 33):
            with self.assertRaises(ValueError):
                validate_varname(bad)

    def test_validate_varname_raises_type_error_on_non_str(self) -> None:
        with self.assertRaises(TypeError):
            validate_varname(42)

    def test_identifier(self) -> None:
        self.assertTrue(is_valid_identifier("estout"))
        self.assertTrue(is_valid_identifier("reghdfe"))
        self.assertTrue(is_valid_identifier("a" * 64))
        self.assertFalse(is_valid_identifier("1pkg"))
        self.assertFalse(is_valid_identifier("a b"))
        self.assertFalse(is_valid_identifier("a" * 65))


class FilenameTests(unittest.TestCase):
    def test_safe_filenames(self) -> None:
        for name in ("foo.dta", "auto_2020.csv", "a b.txt", ".hidden"):
            self.assertTrue(is_safe_filename(name), name)

    def test_unsafe_filenames(self) -> None:
        for name in (
            "", "..", ".", "a/b.csv", "a\\b.csv", "*.dta", "a?b", "a:b",
            'a"b', "a|b", "a<b", "a>b", "CON", "con.txt", "NUL.dta",
            "COM1", "foo ", "foo.", "bad\nname",
        ):
            self.assertFalse(is_safe_filename(name), repr(name))


# ---- L3 数据路径审计 --------------------------------------------------------

class DataPathAuditorLocalTests(unittest.TestCase):
    def setUp(self) -> None:
        # 两个真实存在的独立临时目录，作为"授权目录内 / 外"的参照
        self._d1 = tempfile.TemporaryDirectory()
        self._d2 = tempfile.TemporaryDirectory()
        self.addCleanup(self._d1.cleanup)
        self.addCleanup(self._d2.cleanup)
        self.d1 = self._d1.name
        self.d2 = self._d2.name
        self.aud = DataPathAuditor(allowed_dirs=[self.d1])

    def test_inside_allowed_dir(self) -> None:
        inside = os.path.join(self.d1, "sub", "x.dta")  # 文件可不实际存在
        self.assertTrue(self.aud.check_local_path(inside))
        self.assertTrue(self.aud.check(inside))

    def test_outside_allowed_dir(self) -> None:
        outside = os.path.join(self.d2, "x.dta")
        self.assertFalse(self.aud.check_local_path(outside))
        self.assertFalse(self.aud.check(outside))

    def test_root_of_allowed_dir_is_allowed(self) -> None:
        # 授权目录自身也在边界内
        self.assertTrue(self.aud.check_local_path(self.d1))

    def test_path_traversal_inside_is_normalized_allowed(self) -> None:
        # .. 拉回授权目录内：归一后应放行
        inside_via_dotdot = os.path.join(self.d1, "sub", "..", "x.dta")
        self.assertTrue(self.aud.check_local_path(inside_via_dotdot))

    def test_path_traversal_escape_is_denied(self) -> None:
        # 用 .. 逃到兄弟目录：归一后落在授权目录外，必须拒绝
        escape = os.path.join(self.d1, "..", os.path.basename(self.d2), "x.dta")
        self.assertFalse(self.aud.check_local_path(escape))

    def test_sibling_prefix_dir_is_denied(self) -> None:
        # d1 的"同前缀假兄弟"（若存在）不得算在 d1 内；用 d1+"_x" 验证字符串前缀误判被堵
        fake = self.d1 + "_sibling"
        self.assertFalse(self.aud.check_local_path(os.path.join(fake, "x.dta")))

    def test_empty_allowed_dirs_fails_closed(self) -> None:
        aud = DataPathAuditor(allowed_dirs=[])
        self.assertFalse(aud.check_local_path(self.d1))

    def test_garbage_input_denied(self) -> None:
        self.assertFalse(self.aud.check_local_path(""))
        self.assertFalse(self.aud.check_local_path(None))  # type: ignore[arg-type]
        self.assertFalse(self.aud.check(None))  # type: ignore[arg-type]
        self.assertFalse(self.aud.check(""))  # type: ignore[arg-type]
        self.assertFalse(self.aud.check(42))  # type: ignore[arg-type]


class DataPathAuditorUrlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.aud = DataPathAuditor(allowed_dirs=[], enable_url_guard=True)

    def test_https_allowed_without_userinfo_and_ip(self) -> None:
        self.assertTrue(self.aud.check_url("https://example.com/data/file.dta"))
        self.assertTrue(self.aud.check_url("https://sub.example.com:8443/a/b.csv"))

    def test_http_rejected(self) -> None:
        self.assertFalse(self.aud.check_url("http://example.com/a.dta"))

    def test_ip_literal_rejected(self) -> None:
        self.assertFalse(self.aud.check_url("https://192.168.1.1/a.dta"))
        self.assertFalse(self.aud.check_url("https://10.0.0.5/x"))
        self.assertFalse(self.aud.check_url("https://[::1]/x"))

    def test_userinfo_rejected(self) -> None:
        self.assertFalse(self.aud.check_url("https://user:pass@example.com/a.dta"))
        self.assertFalse(self.aud.check_url("https://alice@example.com/a.dta"))

    def test_guard_disabled_allows_all(self) -> None:
        lax = DataPathAuditor(allowed_dirs=[], enable_url_guard=False)
        self.assertTrue(lax.check_url("http://anywhere.example/x"))
        self.assertTrue(lax.check_url("https://192.168.0.1/x"))
        self.assertTrue(lax.check_url("not-a-url"))

    def test_host_whitelist_exact_and_subdomain(self) -> None:
        aud = DataPathAuditor(allowed_dirs=[], allowed_hosts=["example.com"])
        self.assertTrue(aud.check_url("https://example.com/a.dta"))
        self.assertTrue(aud.check_url("https://stats.example.com/a.dta"))
        self.assertFalse(aud.check_url("https://notexample.com/a.dta"))
        self.assertFalse(aud.check_url("https://other.org/a.dta"))

    def test_host_whitelist_case_insensitive(self) -> None:
        aud = DataPathAuditor(allowed_dirs=[], allowed_hosts=["Example.COM"])
        self.assertTrue(aud.check_url("https://stats.example.com/a.dta"))

    def test_check_auto_routes_url(self) -> None:
        self.assertTrue(self.aud.check("https://example.com/a.dta"))
        self.assertFalse(self.aud.check("http://example.com/a.dta"))

    def test_bad_url_denied(self) -> None:
        self.assertFalse(self.aud.check_url("https://"))
        self.assertFalse(self.aud.check_url(""))
        self.assertFalse(self.aud.check_url(None))  # type: ignore[arg-type]


# ---- 分层配置 ---------------------------------------------------------------

class ConfigPrecedenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.missing = str(self.base / "nope" / "config.toml")  # 恒不存在的路径

    def _write(self, name: str, content: str) -> str:
        p = self.base / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return str(p)

    def test_defaults_when_no_sources(self) -> None:
        cfg = load_config(environ={}, project_path=self.missing, user_path=self.missing)
        self.assertTrue(get_security(cfg, "enable_url_guard"))
        self.assertEqual(get_security(cfg, "allowed_data_dirs"), [])
        self.assertEqual(get_security(cfg, "allowed_hosts"), [])
        # get() 缺省兜底
        self.assertEqual(get(cfg, "security", "no_such_key", "fallback"), "fallback")

    def test_security_user_precedes_project_and_ordinary_project_precedes_user(self) -> None:
        # [security]：user 压过 project；[general]（普通配置）：project 压过 user
        user = self._write(
            "user/config.toml",
            "[general]\ntimeout = 60\n\n"
            "[security]\nenable_url_guard = false\n"
            "allowed_data_dirs = ['C:/userdata']\n",
        )
        proj = self._write(
            "proj/config.toml",
            "[general]\ntimeout = 30\n\n"
            "[security]\nenable_url_guard = true\n"
            "allowed_data_dirs = ['C:/projdata']\n",
        )
        cfg = load_config(environ={}, project_path=proj, user_path=user)

        # security 区：user 优先于 project
        self.assertFalse(get_security(cfg, "enable_url_guard"))
        self.assertEqual(get_security(cfg, "allowed_data_dirs"), ["C:/userdata"])
        # 普通区：project 优先于 user
        self.assertEqual(cfg["general"]["timeout"], 30)

    def test_env_overrides_everything(self) -> None:
        # 文件全设 false，env 设 true → env 赢
        both_false = self._write(
            "both_false.toml",
            "[security]\nenable_url_guard = false\nallowed_data_dirs = ['C:/x']\n",
        )
        cfg = load_config(
            environ={"STATAMCP_ENABLE_URL_GUARD": "true"},
            project_path=both_false,
            user_path=both_false,
        )
        self.assertTrue(get_security(cfg, "enable_url_guard"))

        # env 的 list 键整体替换文件里的 list
        cfg2 = load_config(
            environ={"STATAMCP_DATA_DIRS": "C:/e1;C:/e2"},
            project_path=both_false,
            user_path=self.missing,
        )
        self.assertEqual(get_security(cfg2, "allowed_data_dirs"), ["C:/e1", "C:/e2"])

    def test_broken_toml_silently_ignored(self) -> None:
        broken = self._write("broken/config.toml", "[security\nthis is not toml ]][")
        # 不 crash，坏文件当不存在 → 默认值仍在
        cfg = load_config(environ={}, project_path=broken, user_path=self.missing)
        self.assertTrue(get_security(cfg, "enable_url_guard"))

    def test_missing_file_silently_ignored(self) -> None:
        cfg = load_config(environ={}, project_path=self.missing, user_path=self.missing)
        self.assertTrue(get_security(cfg, "enable_url_guard"))


# ---- 隐私哈希日志 -----------------------------------------------------------

class PrivacyRedactTests(unittest.TestCase):
    def test_redact_path_hides_dirs_keeps_drive_and_file(self) -> None:
        p = r"C:\Users\alice\data\foo.dta"
        r = redact_path(p)
        self.assertNotIn("Users", r)
        self.assertNotIn("alice", r)
        self.assertNotIn("data", r)
        self.assertNotIn(p, r)          # 完整原路径绝不出现在脱敏串里
        self.assertTrue(r.startswith("C:"))
        self.assertTrue(r.endswith("foo.dta"))
        self.assertIn("<", r)           # 哈希 token 有标记

    def test_redact_path_deterministic(self) -> None:
        p = r"C:\Users\bob\data\survey.dta"
        self.assertEqual(redact_path(p), redact_path(p))

    def test_redact_path_different_paths_differ(self) -> None:
        self.assertNotEqual(
            redact_path(r"C:\Users\bob\data\a.dta"),
            redact_path(r"C:\Users\bob\data\b.dta"),
        )
        # 不同目录哈希应不同
        self.assertNotEqual(
            redact_path(r"C:\Users\bob\aaa\x.dta"),
            redact_path(r"C:\Users\bob\bbb\x.dta"),
        )

    def test_redact_path_directory_form_hashes_last_name_too(self) -> None:
        # 目录形态（尾分隔符）：最后一段也是目录名，一并哈希
        r = redact_path("C:\\Users\\alice\\data\\")
        self.assertNotIn("data", r)
        self.assertNotIn("alice", r)

    def test_redact_path_relative_keeps_only_file(self) -> None:
        r = redact_path(r"data\sub\foo.dta")
        self.assertNotIn("data", r)
        self.assertTrue(r.endswith("foo.dta"))

    def test_redact_url_hides_credentials_host_and_query(self) -> None:
        u = "https://alice:hunter2@stats.example.com/data/file.csv?token=abc123"
        r = redact_url(u)
        self.assertNotIn("hunter2", r)
        self.assertNotIn("alice@", r)
        self.assertNotIn("stats.example.com", r)
        self.assertNotIn("token", r)
        self.assertNotIn("abc123", r)
        self.assertNotIn(u, r)
        self.assertTrue(r.startswith("https://"))
        self.assertTrue(r.endswith("file.csv"))

    def test_redact_url_deterministic_and_distinct_hosts(self) -> None:
        self.assertEqual(
            redact_url("https://stats.example.com/a.dta"),
            redact_url("https://stats.example.com/a.dta"),
        )
        self.assertNotEqual(
            redact_url("https://a.example.com/x.dta"),
            redact_url("https://b.example.com/x.dta"),
        )

    def test_redact_url_directory_path_hashed_fully(self) -> None:
        r = redact_url("https://example.com/private/reports/")
        self.assertNotIn("reports", r)
        self.assertNotIn("private", r)

    def test_redact_url_garbage_falls_back(self) -> None:
        self.assertEqual(redact_url("not a url at all"), "<redacted>")
        self.assertEqual(redact_url(""), "<redacted>")
        self.assertEqual(redact_url(None), "<redacted>")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
