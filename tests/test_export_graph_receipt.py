from __future__ import annotations

import re
import types
from pathlib import Path

from stata_mcp.session import SessionResult
from stata_mcp.tools.export_graph import stata_export_graph


class _GraphBackend:
    id = "graph-session"
    _generation = 3

    def execute(self, code: str) -> SessionResult:
        match = re.search(r'graph export "([^"]+)"', code)
        assert match is not None
        target = Path(match.group(1))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fake-png")
        return SessionResult(
            text="ok",
            rc=0,
            command_hash="graph-command",
            session_id=self.id,
            session_generation=self._generation,
            structured_result_status="not_applicable",
            executor_instance_id="executor-test",
            runtime_environment={},
            supervision_proof={},
        )


def test_export_graph_returns_receipt_for_resolved_session(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    backend = _GraphBackend()

    envelope = stata_export_graph(
        {"format": "png", "filename": "result"},
        types.SimpleNamespace(backend=backend),
    )

    assert envelope.rc == 0
    assert envelope.structured["size_bytes"] == len(b"fake-png")
    assert envelope.execution_receipt["session_id"] == "graph-session"
    assert envelope.execution_receipt["execution_status"] == "succeeded"
