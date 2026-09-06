from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "redmine-access"
    / "scripts"
    / "inspect_attachment.py"
)
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("redmine_attachment_inspector", SCRIPT_PATH)
assert SPEC and SPEC.loader
inspector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = inspector
SPEC.loader.exec_module(inspector)


class InspectAttachmentTests(unittest.TestCase):
    def test_png_metadata_reports_safe_dimensions(self) -> None:
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(
            ">II", 640, 480
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "screen.png"
            path.write_bytes(png_header)
            value = inspector.metadata(path.resolve())
        self.assertEqual(value["detected"]["kind"], "image")
        self.assertEqual(value["detected"]["width"], 640)
        self.assertEqual(value["detected"]["height"], 480)
        self.assertTrue(value["detected"]["safe_to_view"])

    def test_large_pixel_image_is_not_safe_to_view(self) -> None:
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(
            ">II", 100_000, 100_000
        )
        detected = inspector.detect_type(png_header)
        self.assertEqual(detected["kind"], "image")
        self.assertFalse(detected["safe_to_view"])

    def test_log_output_is_bounded_and_redacts_redmine_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "radio.log"
            path.write_text(
                "boot\nERROR top-secret-key radio timeout\nready\n",
                encoding="utf-8",
            )
            with (
                mock.patch.object(
                    inspector.client,
                    "load_context",
                    return_value=(
                        "writer",
                        {
                            "server_url": "https://redmine.example.test",
                            "api_key": "top-secret-key",
                        },
                        {},
                    ),
                ),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                result = inspector.main(
                    ["log", str(path), "--pattern", "ERROR", "--max-matches", "10"]
                )
        self.assertEqual(result, 0)
        raw = output.getvalue()
        self.assertNotIn("top-secret-key", raw)
        value = json.loads(raw)
        self.assertEqual(value["matches_returned"], 1)
        self.assertEqual(value["matches"][0]["line"], 2)
        self.assertIn("[REDACTED]", value["matches"][0]["text"])

    def test_archive_is_not_read_as_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "logs.zip"
            path.write_bytes(b"PK\x03\x04" + b"x" * 32)
            with self.assertRaisesRegex(inspector.InspectionError, "UTF-8"):
                inspector.inspect_log(path.resolve(), ["error"], 10, 5, 5)

    def test_symlink_is_rejected(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.log"
            target.write_text("ERROR\n", encoding="utf-8")
            link = root / "link.log"
            link.symlink_to(target)
            with self.assertRaisesRegex(inspector.InspectionError, "符号链接"):
                inspector.secure_file(str(link))


if __name__ == "__main__":
    unittest.main()
