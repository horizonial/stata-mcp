from __future__ import annotations

import unittest
from dataclasses import dataclass
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stata_mcp.stata.worker import (
    RuntimeEnvironmentProbeError,
    _configure_worker_temp_directory,
    _probe_runtime_value,
    _runtime_environment,
)


@dataclass(frozen=True)
class _ExecutionResult:
    text: str
    rc: int = 0


class _ScriptedBackend:
    def __init__(self, responses: dict[str, list[_ExecutionResult | Exception]]) -> None:
        self._responses = {key: list(values) for key, values in responses.items()}
        self.calls: list[str] = []

    def execute(self, code: str) -> _ExecutionResult:
        self.calls.append(code)
        values = self._responses[code]
        value = values.pop(0) if len(values) > 1 else values[0]
        if isinstance(value, Exception):
            raise value
        return value


class WorkerRuntimeEnvironmentTests(unittest.TestCase):
    def test_worker_temp_directory_is_bound_before_engine_initialization(self) -> None:
        with TemporaryDirectory() as temp_directory, patch.dict(
            "os.environ", {}, clear=False
        ):
            _configure_worker_temp_directory(temp_directory)

            import os

            expected = os.path.abspath(temp_directory)
            self.assertEqual(os.environ["STATATMP"], expected)
            self.assertEqual(os.environ["TEMP"], expected)
            self.assertEqual(os.environ["TMP"], expected)

    def test_required_probe_retries_a_transient_empty_capture(self) -> None:
        backend = _ScriptedBackend(
            {
                "display c(stata_version)": [
                    _ExecutionResult(""),
                    _ExecutionResult("18\n"),
                ]
            }
        )

        value = _probe_runtime_value(
            backend,
            key="stata_version",
            expression="c(stata_version)",
        )

        self.assertEqual(value, "18")
        self.assertEqual(
            backend.calls,
            ["display c(stata_version)", "display c(stata_version)"],
        )

    def test_required_probe_fails_closed_after_bounded_attempts(self) -> None:
        backend = _ScriptedBackend(
            {"display c(os)": [_ExecutionResult("", rc=0)]}
        )

        with self.assertRaisesRegex(
            RuntimeEnvironmentProbeError,
            "required runtime fact 'stata_os' unavailable after 3 attempts",
        ):
            _probe_runtime_value(
                backend,
                key="stata_os",
                expression="c(os)",
            )

        self.assertEqual(backend.calls, ["display c(os)"] * 3)

    def test_runtime_environment_includes_independent_mp_signal(self) -> None:
        backend = _ScriptedBackend(
            {
                "display c(stata_version)": [_ExecutionResult("18\n")],
                "display c(flavor)": [_ExecutionResult("IC\n")],
                "display c(os)": [_ExecutionResult("Windows\n")],
                "display c(MP)": [_ExecutionResult("1\n")],
            }
        )

        environment = _runtime_environment(backend)

        self.assertEqual(environment["stata_version"], "18")
        self.assertEqual(environment["stata_flavor"], "IC")
        self.assertEqual(environment["stata_os"], "Windows")
        self.assertEqual(environment["stata_mp"], "1")
        self.assertIs(environment["stata_is_mp"], True)


if __name__ == "__main__":
    unittest.main()
