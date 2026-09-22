from __future__ import annotations

import types
import unittest

from stata_mcp.session import SessionResult
from stata_mcp.tools.run import build_execution_receipt, enrich_structured, stata_run
import stata_mcp.tools.task_status as task_status_module
from stata_mcp.contract import validate_execution_receipt
from stata_mcp.stata.backend import ExecutionResult
from stata_mcp.stata.worker import _data_signature, capture_return_state, parse_execution_result


class _Backend:
    id = "receipt_test"
    _generation = 7

    def __init__(self, result: SessionResult):
        self.result = result

    def execute(self, code: str):
        return self.result


class ExecutionReceiptTests(unittest.TestCase):
    def test_pure_r_class_catalog_preserves_primary_scalar_keys(self) -> None:
        result = SessionResult(
            text="ok",
            rc=0,
            structured=None,
            return_state={"r_scalars": {"N": 74.0, "mean": 6165.0}},
        )

        enriched = enrich_structured(None, "summarize price", result)
        by_key = {
            item["source_key"]: item
            for item in enriched["result_catalog"]["elements"]
        }
        self.assertEqual(set(by_key), {"scalar.N", "scalar.mean"})
        self.assertEqual(
            by_key["scalar.mean"]["locator"],
            {"locator_type": "R_SCALAR", "name": "r(mean)"},
        )

    def test_enrichment_preserves_postestimation_r_scalars_beside_e_catalog(self) -> None:
        result = SessionResult(
            text="ok",
            rc=0,
            structured={
                "result_catalog": {
                    "schema_version": "stata.result-catalog/v1",
                    "elements": [
                        {
                            "source_key": "scalar.p",
                            "value": 0.5,
                            "statistic_kind": "stata_returned_scalar",
                            "locator": {"locator_type": "E_SCALAR", "name": "e(p)"},
                            "primitive_locators": [],
                        }
                    ],
                }
            },
            return_state={"r_scalars": {"p": 0.08}},
        )

        enriched = enrich_structured(result.structured, "test mpg weight", result)
        by_key = {
            item["source_key"]: item
            for item in enriched["result_catalog"]["elements"]
        }
        self.assertEqual(by_key["scalar.p"]["locator"]["name"], "e(p)")
        self.assertEqual(by_key["return.scalar.p"]["value"], 0.08)
        self.assertEqual(
            by_key["return.scalar.p"]["locator"],
            {"locator_type": "R_SCALAR", "name": "r(p)"},
        )

    def test_success_receipt_has_explicit_identity_and_statuses(self) -> None:
        result = SessionResult(
            text="ok",
            rc=0,
            structured={"cmd": "regress"},
            command_hash="abc",
            data_signature="sig",
            exec_seq=2,
            session_id="receipt_test",
            session_generation=7,
            structured_result_status="complete",
            runtime_environment={"stata_mcp_version": "1.0.0"},
            supervision_proof={"worker_ready_acknowledged": True},
            executor_instance_id="executor-1",
        )
        env = stata_run({"code": "regress y x"}, types.SimpleNamespace(backend=_Backend(result)))
        receipt = env.execution_receipt
        self.assertEqual(env.schema_version, "stata-mcp.envelope/v1")
        self.assertEqual(receipt["schema_version"], "stata.execution-receipt/v1alpha1")
        self.assertEqual(receipt["session_id"], "receipt_test")
        self.assertEqual(receipt["executor_instance_id"], "executor-1")
        self.assertEqual(receipt["session_generation"], 7)
        self.assertEqual(receipt["execution_status"], "succeeded")
        self.assertEqual(receipt["raw_output_status"], "complete")
        self.assertEqual(receipt["structured_result_status"], "complete")
        self.assertEqual(validate_execution_receipt(receipt), [])

    def test_command_error_cannot_look_like_structured_success(self) -> None:
        result = SessionResult(
            text="variable missing not found",
            rc=111,
            structured=None,
            session_id="receipt_test",
            session_generation=7,
            structured_result_status="not_applicable",
        )
        receipt = build_execution_receipt(_Backend(result), result, truncated=False)
        self.assertEqual(receipt["execution_status"], "command_failed")
        self.assertEqual(receipt["structured_result_status"], "not_applicable")

    def test_parse_failure_is_explicit_even_when_rc_zero(self) -> None:
        result = SessionResult(
            text="estimation output",
            rc=0,
            structured=None,
            session_id="receipt_test",
            session_generation=7,
            structured_result_status="parse_failed",
        )
        receipt = build_execution_receipt(_Backend(result), result, truncated=True)
        self.assertEqual(receipt["execution_status"], "succeeded")
        self.assertEqual(receipt["raw_output_status"], "truncated")
        self.assertEqual(receipt["structured_result_status"], "parse_failed")

    def test_completed_background_task_reuses_execution_receipt(self) -> None:
        result = SessionResult(
            text="ok",
            rc=0,
            structured={"cmd": "regress"},
            session_id="receipt_test",
            session_generation=7,
            structured_result_status="complete",
            executor_instance_id="executor-1",
        )

        class _Runner:
            def status(self, job_id):
                return {
                    "status": "done",
                    "result": result,
                    "code": "regress y x",
                    "session": _Backend(result),
                    "elapsed_ms": 1.0,
                }

        original = task_status_module.get_runner
        task_status_module.get_runner = lambda: _Runner()
        self.addCleanup(setattr, task_status_module, "get_runner", original)
        env = task_status_module.stata_task_status({"job_id": "job-1"})
        self.assertEqual(env.execution_receipt["execution_status"], "succeeded")
        self.assertEqual(env.execution_receipt["session_generation"], 7)

    def test_empty_background_failure_reports_stata_return_code(self) -> None:
        result = SessionResult(
            text="",
            rc=603,
            structured=None,
            session_id="receipt_test",
            session_generation=7,
            structured_result_status="not_applicable",
            executor_instance_id="executor-1",
        )

        class _Runner:
            def status(self, job_id):
                return {
                    "status": "done",
                    "result": result,
                    "code": "graph save output.gph",
                    "session": _Backend(result),
                    "elapsed_ms": 1.0,
                }

        original = task_status_module.get_runner
        task_status_module.get_runner = lambda: _Runner()
        self.addCleanup(setattr, task_status_module, "get_runner", original)
        env = task_status_module.stata_task_status({"job_id": "job-1"})

        self.assertEqual(env.rc, 603)
        self.assertIn("r(603)", env.text)
        self.assertIn("file could not be opened", env.text)
        self.assertEqual(env.error["kind"], "command_failed")

    def test_additive_unknown_field_is_compatible(self) -> None:
        result = SessionResult(
            text="ok", rc=0, structured={}, session_id="receipt_test",
            session_generation=7, structured_result_status="complete",
            executor_instance_id="executor-1", runtime_environment={},
            supervision_proof={},
        )
        receipt = build_execution_receipt(_Backend(result), result, truncated=False)
        receipt["future_optional_field"] = {"value": 1}
        self.assertEqual(validate_execution_receipt(receipt), [])

    def test_missing_required_field_is_rejected(self) -> None:
        result = SessionResult(
            text="ok", rc=0, structured={}, session_id="receipt_test",
            session_generation=7, structured_result_status="complete",
            executor_instance_id="executor-1", runtime_environment={},
            supervision_proof={},
        )
        receipt = build_execution_receipt(_Backend(result), result, truncated=False)
        del receipt["session_generation"]
        self.assertIn("missing required fields", " ".join(validate_execution_receipt(receipt)))

    def test_worker_parser_exception_maps_to_parse_failed(self) -> None:
        execution = ExecutionResult(text="ok", rc=0, e_changed=True)

        def fail(_backend):
            raise RuntimeError("injected parser fault")

        structured, status = parse_execution_result(object(), execution, fail)
        self.assertIsNone(structured)
        self.assertEqual(status, "parse_failed")

    def test_return_state_is_captured_before_later_internal_commands(self) -> None:
        class _ReturnBackend:
            def execute(self, code):
                self.asserted = code
                return ExecutionResult(
                    text="scalars:\n              r(N) =  74\n"
                    "           r(mean) =  6165.257\n",
                    rc=0,
                )

        state = capture_return_state(
            _ReturnBackend(), ExecutionResult(text="summary", rc=0)
        )
        self.assertEqual(state["r_scalars"]["N"], 74.0)
        self.assertEqual(state["r_scalars"]["mean"], 6165.257)

    def test_data_signature_retries_empty_internal_capture_without_losing_contract(self) -> None:
        class _SignatureBackend:
            def __init__(self):
                self.calls = 0

            def execute(self, code):
                self.calls += 1
                self.code = code
                if self.calls == 1:
                    return ExecutionResult(text=".", rc=0)
                return ExecutionResult(text="74:12(71728):3831085005:1395876116\n", rc=0)

        backend = _SignatureBackend()
        self.assertEqual(
            _data_signature(backend),
            "74:12(71728):3831085005:1395876116",
        )
        self.assertEqual(backend.calls, 2)
        self.assertIn("_return hold", backend.code)
        self.assertIn("_return restore", backend.code)


if __name__ == "__main__":
    unittest.main()
