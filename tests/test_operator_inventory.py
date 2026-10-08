"""Original evidence inventories are captured before a validation job runs."""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.evidence_benchmark_common import fingerprint_value


class OperatorInventoryTest(unittest.TestCase):
    def snapshots(self, directory, kind):
        result = subprocess.run(  # noqa: S603 - fixed inventory collector, no server execution
            [
                sys.executable,
                "-m",
                "scripts.operator_inventory",
                "--output",
                str(directory),
                "--kind",
                kind,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return {
            name: json.loads((directory / f"{name}.json").read_text())
            for name in ("catalogue", "stories", "expectations", "selection")
        }

    def test_acceptance_snapshots_cover_original_cases_and_their_story_and_gate_attribution(self):
        with TemporaryDirectory() as temporary:
            snapshots = self.snapshots(Path(temporary), "acceptance")
        cases = snapshots["catalogue"]["cases"]
        self.assertGreater(len(cases), 0)
        self.assertEqual(set(cases), set(snapshots["selection"]))
        self.assertEqual(set(cases), set(snapshots["stories"]))
        self.assertEqual(set(cases), set(snapshots["expectations"]))
        self.assertTrue(any(row["gate"] for row in cases.values()))
        self.assertTrue(any(row["tool"] == "get_concept" for row in cases.values()))

    def test_benchmark_selection_binds_fixed_calls_and_keeps_remote_identity_unknown(self):
        with TemporaryDirectory() as temporary:
            snapshots = self.snapshots(Path(temporary), "benchmark")
        selection = snapshots["selection"]
        self.assertEqual(len(selection["cases"]), 10)
        self.assertIn("harmonize_data_dictionary", [case["tool"] for case in selection["cases"]])
        self.assertEqual(
            selection["fingerprint"]["workload"], fingerprint_value(selection["cases"])
        )
        self.assertEqual(
            selection["fingerprint"]["transport"], fingerprint_value("streamable-http")
        )
        self.assertIsNone(selection["fingerprint"]["release"])
