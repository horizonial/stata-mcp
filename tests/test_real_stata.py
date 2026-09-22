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
        source_map = r.structured["stored_result_source_map"]
        capability = r.structured["result_source_capability"]
        self.assertEqual(capability["capability_id"], "stata.generic-result-source.v1")
        self.assertEqual(capability["capability_version"], 1)
        self.assertEqual(len(capability["extractor_contract_hash"]), 64)
        self.assertEqual(source_map["schema_version"], "stata.stored-result-source-map/v1alpha1")
        sources = {item["display_key"]: item for item in source_map["terms"]}
        self.assertEqual(sources["weight"]["coefficient"]["matrix"], "e(b)")
        self.assertEqual(sources["weight"]["coefficient"]["column_key"], "weight")
        self.assertEqual(sources["weight"]["variance"]["matrix"], "e(V)")
        self.assertEqual(source_map["scalars"]["N"]["name"], "e(N)")
        self.assertEqual(source_map["scalars"]["r2"]["name"], "e(r2)")
        self.assertEqual(r.structured["scalars"]["N"], 74)
        self.assertEqual(r.structured["scalars"]["df_r"], 72)
        sample = r.structured["estimation_sample_manifest"]
        self.assertEqual(sample["source"], "e(sample)")
        self.assertEqual(sample["row_count"], 74)
        self.assertEqual(sample["included_count"], 74)
        self.assertTrue(sample["mask_hex"])
        self.assertEqual(len(sample["mask_sha256"]), 64)

    def test_logit_uses_z_distribution(self):
        """logit 走 z 分布：e(df_r) 缺失，p 值按 normal 而非 ttail。"""
        r = self.s.execute("logit foreign weight")
        self.assertEqual(r.rc, 0)
        w = {c["var"]: c for c in r.structured["coefs"]}["weight"]
        # weight 系数 logit 已知值（引擎 ground truth，单变量 spec）
        self.assertAlmostEqual(w["coef"], -0.002587388, places=8)
        # p 值（引擎实测 2.18e-05，极显著）——断言显著即可，具体值由 z 分布给出
        self.assertLess(w["p"], 0.001)

    def test_logit_advertises_generic_source_metadata(self):
        result = self.s.execute("logit foreign mpg weight, vce(robust)")

        self.assertEqual(result.rc, 0)
        self.assertIsNotNone(result.structured)
        structured = result.structured
        self.assertEqual(structured["cmd"], "logit")
        self.assertEqual(structured["depvar"], "foreign")
        self.assertEqual(structured["vce"], "robust")
        self.assertEqual(
            structured["result_source_capability"]["capability_id"],
            "stata.generic-result-source.v1",
        )
        self.assertEqual(
            structured["stored_result_source_map"]["scalars"]["r2_p"]["name"],
            "e(r2_p)",
        )
        self.assertNotIn("df_r", structured["stored_result_source_map"]["scalars"])
        self.assertAlmostEqual(structured["r2_p"], 0.3965529780, places=8)

    def test_repeated_identical_regression_is_a_fresh_structured_result(self):
        """相同规格重复执行仍是新的 Run，不能因 e() 数值相同而丢失结果。"""
        first = self.s.execute("regress mpg weight")
        second = self.s.execute("regress mpg weight")
        self.assertIsNotNone(first.structured)
        self.assertIsNotNone(second.structured)
        self.assertEqual(first.structured["coefs"], second.structured["coefs"])

    def test_reghdfe_advertises_generic_source_metadata_when_installed(self):
        available = self.s.execute("capture which reghdfe")
        if available.rc != 0:
            self.skipTest("reghdfe is not installed")
        result = self.s.execute(
            "reghdfe price mpg, absorb(foreign) vce(robust)"
        )

        self.assertEqual(result.rc, 0)
        self.assertIsNotNone(result.structured)
        structured = result.structured
        self.assertEqual(structured["cmd"], "reghdfe")
        self.assertEqual(structured["absorbed_effects"], ["foreign"])
        self.assertEqual(structured["cluster_variables"], [])
        self.assertEqual(structured["vce"], "robust")
        self.assertAlmostEqual(structured["r2_within"], 0.2821435687, places=8)
        self.assertEqual(
            structured["result_source_capability"]["capability_id"],
            "stata.generic-result-source.v1",
        )
        self.assertEqual(
            structured["stored_result_source_map"]["scalars"]["r2_within"]["name"],
            "e(r2_within)",
        )
        self.assertEqual(
            structured["stored_result_source_map"]["macros"]["absvars"]["name"],
            "e(absvars)",
        )
        self.assertEqual(structured["profile_environment"]["version"], "6.12.5")

    def test_reghdfe_cluster_identity_is_structured(self):
        result = self.s.execute(
            "reghdfe price mpg weight, absorb(foreign) vce(cluster rep78)"
        )

        self.assertEqual(result.rc, 0)
        self.assertEqual(result.structured["vce"], "cluster")
        self.assertEqual(result.structured["cluster_variables"], ["rep78"])
        self.assertEqual(result.structured["absorbed_effects"], ["foreign"])
        self.assertEqual(result.structured["N"], 69)

    def test_ivregress_captures_instruments_and_first_stage(self):
        result = self.s.execute(
            "ivregress 2sls price weight (mpg = displacement), vce(robust)"
        )

        self.assertEqual(result.rc, 0)
        structured = result.structured
        self.assertEqual(structured["iv_estimator"], "2sls")
        self.assertEqual(structured["endogenous_variables"], ["mpg"])
        self.assertEqual(structured["included_exogenous_variables"], ["weight"])
        self.assertEqual(structured["excluded_instruments"], ["displacement"])
        first_stage = structured["first_stage"][0]
        self.assertEqual(first_stage["endogenous_variable"], "mpg")
        self.assertAlmostEqual(first_stage["statistics"]["partial_r2"], 0.00401599)
        self.assertAlmostEqual(first_stage["statistics"]["f_statistic"], 0.50368579)
        self.assertAlmostEqual(first_stage["statistics"]["p_value"], 0.48020944)
        self.assertEqual(
            structured["result_source_capability"]["capability_id"],
            "stata.generic-result-source.v1",
        )

    def test_unregistered_estimator_still_gets_generic_catalog(self):
        result = self.s.execute("probit foreign mpg weight, vce(robust)")

        self.assertEqual(result.rc, 0)
        catalog = result.structured["result_catalog"]["elements"]
        keys = {item["source_key"] for item in catalog}
        self.assertIn("term.mpg.coefficient", keys)
        self.assertIn("term.mpg.se", keys)
        self.assertIn("scalar.N", keys)
        self.assertNotIn("result_profile_capability", result.structured)

    def test_postestimation_return_scalar_coexists_with_estimation_catalog(self):
        from stata_mcp.tools.run import stata_run

        result = stata_run(
            {"code": "regress price mpg weight\ntest mpg weight"},
            _ctx(self.s),
        )

        self.assertEqual(result.rc, 0)
        catalog = result.structured["result_catalog"]["elements"]
        by_key = {item["source_key"]: item for item in catalog}
        self.assertIn("term.mpg.coefficient", by_key)
        self.assertIn("return.scalar.p", by_key)
        self.assertEqual(
            by_key["return.scalar.p"]["locator"],
            {"locator_type": "R_SCALAR", "name": "r(p)"},
        )

    def test_separate_postestimation_call_captures_atomic_return_state(self):
        self.s.execute("regress price mpg weight")
        result = self.s.execute("test mpg weight")

        self.assertEqual(result.rc, 0)
        self.assertIn("p", (result.return_state or {}).get("r_scalars", {}))

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
