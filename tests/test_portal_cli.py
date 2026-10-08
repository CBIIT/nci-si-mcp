"""Repository users can import evidence without an account or a shell command template."""

import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.portal import main
from scripts.portal_http import create_server
from scripts.portal_store import EvidenceStore

from test_portal_store import run_bundle


class PortalCLITest(unittest.TestCase):
    def test_serve_command_closes_its_listener_on_local_interrupt(self):
        with TemporaryDirectory() as temporary:
            database = Path(temporary) / "history.sqlite"
            server = create_server(EvidenceStore(database), port=0)
            output = io.StringIO()
            with (
                patch("sys.argv", ["portal", "--store", str(database), "serve", "--port", "0"]),
                patch("scripts.portal.create_server", return_value=server),
                patch.object(server, "serve_forever", side_effect=KeyboardInterrupt),
                redirect_stdout(output),
            ):
                main()
            self.assertEqual(server.fileno(), -1)
            self.assertIn("http://127.0.0.1:", output.getvalue())

    def test_legacy_symlink_is_rejected_without_reading_target(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "private.json"
            target.write_text("PRIVATE-CANARY")
            alias = root / "alias.json"
            alias.symlink_to(target)
            with (
                patch(
                    "sys.argv",
                    [
                        "portal",
                        "--store",
                        str(root / "history.sqlite"),
                        "legacy",
                        str(alias),
                        "--kind",
                        "acceptance",
                    ],
                ),
                self.assertRaisesRegex(ValueError, "symlink"),
            ):
                main()
            self.assertEqual(EvidenceStore(root / "history.sqlite").history(), [])

    def test_import_command_keeps_original_binding_and_prints_only_run_id(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle"
            bundle.mkdir()
            for name, raw in run_bundle().items():
                (bundle / (name + ".json")).write_bytes(raw)
            database = root / "history.sqlite"
            result = subprocess.run(  # noqa: S603 - fixed module and owned fixture paths
                [
                    sys.executable,
                    "-m",
                    "scripts.portal",
                    "--store",
                    str(database),
                    "import",
                    str(bundle),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "1" * 32)
            self.assertEqual(
                EvidenceStore(database).get("1" * 32)["evidence"]["kind"], "acceptance"
            )

    def test_legacy_command_does_not_interpret_claimed_success(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = root / "old.json"
            report.write_text('{"outcome":"PASS","secret":"PRIVATE-CANARY"}')
            database = root / "history.sqlite"
            result = subprocess.run(  # noqa: S603 - fixed module and owned fixture paths
                [
                    sys.executable,
                    "-m",
                    "scripts.portal",
                    "--store",
                    str(database),
                    "legacy",
                    str(report),
                    "--kind",
                    "benchmark",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            record = EvidenceStore(database).get(result.stdout.strip())
            self.assertEqual(record["evidence"]["state"], "unverified")
            self.assertNotIn("PRIVATE-CANARY", json.dumps(record) + result.stdout + result.stderr)
