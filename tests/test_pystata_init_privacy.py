from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stata_mcp.stata.pystata_backend import _ensure_sfi_importable, _initialize_pystata


class _ModernConfig:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def init(self, **kwargs) -> None:
        self.calls.append(kwargs)
        print("sensitive modern banner")
        print("sensitive modern stderr", file=__import__("sys").stderr)


class _LegacyConfig:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def init(self, **kwargs) -> None:
        self.calls.append(kwargs)
        if "splash" in kwargs:
            raise TypeError("splash unsupported")
        print("sensitive legacy banner")


class TestPystataInitPrivacy(unittest.TestCase):
    def test_modern_init_disables_splash_and_emits_nothing(self) -> None:
        config = _ModernConfig()
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            _initialize_pystata(config)

        self.assertEqual(config.calls, [{"edition": "mp", "splash": False}])
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_frozen_import_fallback_loads_sfi_only_from_stata_home(self) -> None:
        with TemporaryDirectory() as directory:
            source = Path(directory) / "ado" / "base" / "py" / "sfi.py"
            source.parent.mkdir(parents=True)
            source.write_text("PROBE = 'loaded'\n", encoding="utf-8")
            sys.modules.pop("sfi", None)
            with patch(
                "stata_mcp.stata.pystata_backend.importlib.import_module",
                side_effect=ModuleNotFoundError("No module named 'sfi'", name="sfi"),
            ):
                _ensure_sfi_importable(directory)
            try:
                self.assertEqual(sys.modules["sfi"].PROBE, "loaded")
                self.assertEqual(Path(sys.modules["sfi"].__file__).resolve(), source)
            finally:
                sys.modules.pop("sfi", None)

    def test_legacy_fallback_also_emits_nothing(self) -> None:
        config = _LegacyConfig()
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            _initialize_pystata(config)

        self.assertEqual(
            config.calls,
            [
                {"edition": "mp", "splash": False},
                {"edition": "mp"},
            ],
        )
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
