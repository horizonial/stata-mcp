"""Declared Artifact outputs settle after the Stata completion signal."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from stata_mcp.tools.run import _artifact_output_contracts, _capture_artifact_outputs


def test_capture_waits_for_delayed_declared_output(tmp_path: Path) -> None:
    target = tmp_path / "table.rtf"

    def delayed_writer() -> None:
        time.sleep(0.075)
        target.write_bytes(b"{\\rtf1 delayed}")

    writer = threading.Thread(target=delayed_writer)
    writer.start()
    captured, missing = _capture_artifact_outputs(
        [
            {
                "output_slot": "table.baseline",
                "relative_staging_path": "table.rtf",
                "source_path": str(target),
                "artifact_kind": "table",
                "media_type": "application/rtf",
                "required": True,
            }
        ],
        command_hash="a" * 64,
        operation_attempt_id="attempt_test",
    )
    writer.join()

    assert missing == []
    assert captured[0]["size_bytes"] == len(b"{\\rtf1 delayed}")
    assert captured[0]["output_slot"] == "table.baseline"


def test_artifact_contract_resolves_below_session_working_directory(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace-a"
    workspace.mkdir()

    contracts, error = _artifact_output_contracts(
        {
            "artifact_outputs": [
                {
                    "output_slot": "table.baseline",
                    "relative_staging_path": "outputs/table.rtf",
                    "artifact_kind": "table",
                    "media_type": "application/rtf",
                }
            ]
        },
        root=workspace,
    )

    assert error is None
    assert contracts[0]["source_path"] == str(
        (workspace / "outputs" / "table.rtf").resolve()
    )
