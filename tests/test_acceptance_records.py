"""Native acceptance records preserve old evidence and reject fields without disclosing values."""

import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

from scripts.evidence_acceptance import project_acceptance
from scripts.evidence_envelope import EvidenceError

from nci_si_acceptance import report as report_module
from test_evidence_acceptance import bundle, encoded, project, report
from test_evidence_envelope import envelope


class AcceptanceRecordTest(unittest.TestCase):
    def test_malformed_report_names_report_without_echoing_json(self):
        raw = b'{"PRIVATE-CANARY":'
        context = bundle()
        metadata = envelope(report_sha256=hashlib.sha256(raw).hexdigest())
        metadata.update(
            {name + "_sha256": hashlib.sha256(value).hexdigest() for name, value in context.items()}
        )
        with self.assertRaises(EvidenceError) as failure:
            project_acceptance(encoded(metadata), raw, **context)
        self.assertEqual(str(failure.exception), "Invalid acceptance field: report")
        self.assertNotIn("PRIVATE-CANARY", str(failure.exception))

    def assert_invalid(self, native, path):
        with self.assertRaises(EvidenceError) as failure:
            project(native)
        self.assertEqual(str(failure.exception), f"Invalid acceptance field: {path}")
        self.assertNotIn("PRIVATE-CANARY", str(failure.exception))
        self.assertNotIn("test_example", str(failure.exception))

    def test_report_header_fields_are_checked_at_import(self):
        for name, value in (
            ("mode", "PRIVATE-CANARY"),
            ("transport", "PRIVATE-CANARY"),
            ("tools_list_bytes", True),
            ("tools_list_bytes", -1),
            ("tests", []),
            ("tools", []),
            ("failed_gates", ["PRIVATE-CANARY"]),
            ("unrun_gates", ["PRIVATE-CANARY"]),
        ):
            with self.subTest(name=name):
                self.assert_invalid(report() | {name: value}, name)
        self.assert_invalid(report() | {"PRIVATE-CANARY": True}, "report")
        self.assert_invalid([], "report")
        for name in report():
            native = report()
            del native[name]
            self.assert_invalid(native, name)

    def test_suite_case_and_tool_tables_name_safe_field_paths(self):
        cases = (
            ("suite", "version", None, "suite.version"),
            ("suite", "fixture_set", None, "suite.fixture_set"),
            ("suite", "digest", "PRIVATE-CANARY", "suite.digest"),
            ("suite", "PRIVATE-CANARY", True, "suite"),
            ("tests", "gate", 1, "tests[0].gate"),
            ("tests", "gate", True, "tests[0].gate"),
            ("tests", "tool", "PRIVATE-CANARY", "tests[0].tool"),
            ("tests", "outcome", "PRIVATE-CANARY", "tests[0].outcome"),
            ("tests", "outcome", [], "tests[0].outcome"),
            ("tests", "unmatched", None, "tests[0].unmatched"),
            ("tests", "unmatched", [1], "tests[0].unmatched"),
            ("tests", "PRIVATE-CANARY", True, "tests[0]"),
            ("tools", "group", "PRIVATE-CANARY", "tools[0].group"),
            ("tools", "outcome", "FAIL", "tools[0].outcome"),
            ("tools", "outcome", [], "tools[0].outcome"),
            ("tools", "counts", {"passed": True}, "tools[0].counts"),
            ("tools", "counts", {"passed": 0}, "tools[0].counts"),
            ("tools", "counts", {"passed": 2}, "tools[0].counts"),
            ("tools", "counts", [], "tools[0].counts"),
            ("tools", "gates_only", 0, "tools[0].gates_only"),
            ("tools", "gates_only", True, "tools[0].gates_only"),
            ("tools", "implemented_as", [], "tools[0].implemented_as"),
            ("tools", "PRIVATE-CANARY", True, "tools[0]"),
        )
        for group, name, value, path in cases:
            native = report()
            row = native[group] if group == "suite" else next(iter(native[group].values()))
            row[name] = value
            with self.subTest(path=path, value=value):
                self.assert_invalid(native, path)

    def test_existing_archives_roundtrip_without_changing_absent_fields(self):
        root = Path(__file__).resolve().parents[1]
        for path in sorted((root / "docs/evidence").rglob("acceptance-*.json")):
            original = path.read_bytes()
            native = json.loads(original)
            with self.subTest(file=path.name):
                self.assertEqual(report_module.NativeReport.from_dict(native).to_dict(), native)
                self.assertEqual(path.read_bytes(), original)

    def test_partial_completion_and_legacy_availability_are_preserved(self):
        self.assertTrue(project(report() | {"tools_list_bytes": 0})["inventory_complete"])
        for run in (None, {}, {"selected": "PRIVATE-CANARY"}):
            result = project(report() | {"run": run})
            self.assertFalse(result["inventory_complete"])
            self.assertEqual(result["completion_problem"], "invalid run completion metadata")
        run = {
            "exit_status": 0,
            "selected": 2,
            "finished": 2,
            "worker_crashes": 0,
            "extra": "PRIVATE-CANARY",
        }
        self.assertTrue(project(report() | {"run": run})["inventory_complete"])

    def test_acceptance_report_runs_with_scripts_imports_unavailable(self):
        code = """
import importlib.abc, sys
class BlockScripts(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'scripts' or fullname.startswith('scripts.'):
            raise ImportError('acceptance must run independently of scripts')
sys.meta_path.insert(0, BlockScripts())
from nci_si_acceptance.report import Collector
assert Collector().report('fixture')['tests'] == {}
"""
        result = subprocess.run(  # noqa: S603 -- fixed interpreter and inline independence check
            [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
