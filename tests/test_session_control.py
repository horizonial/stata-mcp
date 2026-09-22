from __future__ import annotations

import types
import unittest

from stata_mcp.session import Session, SessionManager, SessionResult
from stata_mcp.tools import TOOLS
from stata_mcp.tools.inspect_data import stata_inspect_data
from stata_mcp.tools.run import stata_run
from stata_mcp.tools.session_control import (
    stata_executor_capabilities,
    stata_session_open,
    stata_session_close,
    stata_session_status,
)


def _result(*, structured=None) -> SessionResult:
    return SessionResult(
        text="ok",
        rc=0,
        structured=structured,
        session_id="alpha",
        session_generation=7,
        structured_result_status=("complete" if structured is not None else "not_applicable"),
        runtime_environment={},
        supervision_proof={},
        executor_instance_id="executor-test",
    )


class _TimedBackend:
    id = "alpha"
    _generation = 7

    def __init__(self) -> None:
        self.timeout = None

    def execute(self, code: str, timeout=None):
        self.timeout = timeout
        return _result(structured={"cmd": "display"})


class _InspectBackend(_TimedBackend):
    def execute(self, code: str, timeout=None):
        result = super().execute(code, timeout=timeout)
        result.return_state = {
            "r_scalars": {"N": 74, "mean": 6165.257, "sd": 2949.496}
        }
        return result

    def snapshot(self):
        return {"variables": ["price", "mpg"]}


class SessionControlTests(unittest.TestCase):
    def test_control_tools_are_openly_registered(self) -> None:
        self.assertIn("stata_executor_capabilities", TOOLS)
        self.assertIn("stata_session_open", TOOLS)
        self.assertIn("stata_session_status", TOOLS)
        self.assertIn("stata_session_close", TOOLS)

    def test_status_does_not_create_session(self) -> None:
        manager = SessionManager()
        ctx = types.SimpleNamespace(manager=manager)
        env = stata_session_status({"session_id": "missing"}, ctx)
        self.assertEqual(env.rc, 0)
        self.assertFalse(env.structured["detail"]["tracked"])
        self.assertEqual(manager.stats()["total_tracked"], 0)

    def test_close_is_idempotent_and_isolated(self) -> None:
        manager = SessionManager()
        manager.get_or_create("alpha")
        manager.get_or_create("beta")
        ctx = types.SimpleNamespace(manager=manager)

        first = stata_session_close(
            {"session_id": "alpha", "reason": "test cleanup"}, ctx
        )
        second = stata_session_close({"session_id": "alpha"}, ctx)

        self.assertTrue(first.structured["detail"]["closed"])
        self.assertFalse(second.structured["detail"]["closed"])
        self.assertFalse(manager.session_status("alpha")["tracked"])
        self.assertTrue(manager.session_status("beta")["tracked"])

    def test_capabilities_do_not_start_a_session(self) -> None:
        manager = SessionManager(max_sessions=2)
        env = stata_executor_capabilities({}, types.SimpleNamespace(manager=manager))
        self.assertEqual(env.rc, 0)
        self.assertTrue(env.structured["different_sessions_parallel"])
        self.assertEqual(env.structured["max_parallel_commands"], 2)
        self.assertEqual(manager.stats()["total_tracked"], 0)

    def test_session_open_rejects_path_escape_before_start(self) -> None:
        manager = SessionManager()
        env = stata_session_open(
            {"session_id": "alpha", "working_directory": "../escape"},
            types.SimpleNamespace(manager=manager),
        )
        self.assertEqual(env.rc, 1)
        self.assertEqual(manager.stats()["total_tracked"], 0)

    def test_force_closed_session_cannot_restart(self) -> None:
        session = Session("alpha")
        session.force_close()
        result = session.execute("display 1")
        self.assertEqual(result.error_kind, "crashed")
        self.assertTrue(result.reset)
        self.assertEqual(session.status_snapshot()["state"], "closed")

    def test_run_passes_explicit_timeout_to_session(self) -> None:
        backend = _TimedBackend()
        env = stata_run(
            {"code": "display 1", "timeout_seconds": 1.25},
            types.SimpleNamespace(backend=backend),
        )
        self.assertEqual(env.rc, 0)
        self.assertEqual(backend.timeout, 1.25)

    def test_invalid_timeout_is_rejected_before_execution(self) -> None:
        backend = _TimedBackend()
        env = stata_run(
            {"code": "display 1", "timeout_seconds": True},
            types.SimpleNamespace(backend=backend),
        )
        self.assertEqual(env.rc, 1)
        self.assertIsNone(backend.timeout)

    def test_inspect_command_returns_execution_receipt(self) -> None:
        backend = _InspectBackend()
        env = stata_inspect_data(
            {"action": "summarize", "variables": ["price"]},
            types.SimpleNamespace(backend=backend),
        )
        self.assertEqual(env.rc, 0)
        self.assertEqual(env.execution_receipt["execution_status"], "succeeded")
        self.assertEqual(env.execution_receipt["structured_result_status"], "complete")


if __name__ == "__main__":
    unittest.main()
