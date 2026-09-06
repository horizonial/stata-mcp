"""真 Stata 正确性回归台（P2a）：比对结构化提取 vs Stata 官方输出。

无 pystata/Stata 的环境自动 skip（CI mock 测试照跑）；本机有 Stata 时跑真引擎，
用已知"官方答案"校验我们的结构化提取没悄悄给错数字（防 P8/P11 那类回归）。

运行：``python -m unittest tests.test_real_stata -v``（需本机有 Stata 18）。
"""
from __future__ import annotations

import os
import unittest

from stata_mcp.session import Session, SessionManager

# Stata 是否安装（决定跑真引擎还是 skip）
_STATA_UTILITIES = r"C:\Program Files\Stata18\utilities"
HAS_STATA = os.path.isdir(_STATA_UTILITIES)


def _make_session() -> Session:
    mgr = SessionManager(max_sessions=1)
    return mgr.get_or_create("default")


def _ctx(s):
    import types

    return types.SimpleNamespace(backend=s)


@unittest.skipUnless(HAS_STATA, "Stata not installed; skipping real-engine test")
class TestRealStataCorrectness(unittest.TestCase):
    def setUp(self):
        self._mgr = SessionManager(max_sessions=1)
        self.s = self._mgr.get_or_create("default")
        self.s.execute("sysuse auto, clear")

    def tearDown(self):
        self._mgr.close_all()

    def test_regress_mpg_weight_matches_stata(self):
        """regress mpg weight 的系数/SE/t 必须匹配 Stata 官方输出（引擎 ground truth）。"""
        r = self.s.execute("regress mpg weight")
        self.assertEqual(r.rc, 0)
        self.assertIsNotNone(r.structured)
        coefs = {c["var"]: c for c in r.structured["coefs"]}
        w = coefs["weight"]
        self.assertAlmostEqual(w["coef"], -0.006008687, places=8)
        self.assertAlmostEqual(w["se"], 0.0005178782, places=9)
        self.assertAlmostEqual(w["t"], -11.60251, places=4)
        c = coefs["_cons"]
        self.assertAlmostEqual(c["coef"], 39.440283531, places=6)
        self.assertEqual(r.structured["N"], 74)
        self.assertAlmostEqual(r.structured["r2"], 0.65153125, places=5)

    def test_logit_uses_z_distribution(self):
        """logit 走 z 分布：e(df_r) 缺失，p 值按 normal 而非 ttail。"""
        r = self.s.execute("logit foreign weight")
        self.assertEqual(r.rc, 0)
        w = {c["var"]: c for c in r.structured["coefs"]}["weight"]
        # weight 系数 logit 已知值（引擎 ground truth，单变量 spec）
        self.assertAlmostEqual(w["coef"], -0.002587388, places=8)
        # p 值（引擎实测 2.18e-05，极显著）——断言显著即可，具体值由 z 分布给出
        self.assertLess(w["p"], 0.001)

    def test_provenance_do_file_roundtrip(self):
        """provenance 应含 data_signature + 可复现 do_file（载入前缀 + 命令）。

        走工具路径（stata_run）：provenance 由 run.py 组装，session.execute 直连
        只有 worker 返回的 raw structured。
        """
        from stata_mcp.tools.run import stata_run

        r = stata_run({"code": "regress mpg weight"}, _ctx(self.s))
        prov = (r.structured or {}).get("provenance", {})
        self.assertIn("data_signature", prov)
        self.assertIn("do_file", prov)
        self.assertIn("sysuse auto, clear", prov["do_file"])  # setUp 载入被记作前缀
        self.assertIn("regress mpg weight", prov["do_file"])
        self.assertIsNotNone(prov.get("exec_seq"))

    def test_data_rows_reads_values(self):
        """data_rows 应能读 auto 数据首行的 make。"""
        p = self.s.preview(2)
        self.assertEqual(p["N"], 74)
        self.assertIn("make", p["variables"])
        self.assertEqual(p["rows"][0][p["variables"].index("make")], "AMC Concord")


if __name__ == "__main__":
    unittest.main()
