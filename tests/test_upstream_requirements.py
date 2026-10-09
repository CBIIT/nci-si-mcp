"""Packages preserve observed evidence and never qualify a merely unrun live test."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from scripts import upstream_requirements as packages


def report(mode):
    return {
        "mode": mode,
        "suite": {"digest": "measured-suite"},
        "tools": {"lookup": {"outcome": "PASS"}},
        "tests": {
            f"tests/test_example.py::test_lookup[{name}]": {
                "tool": "lookup",
                "outcome": "passed" if mode == "fixture" else "not_live",
            }
            for name in ("one", "two")
        },
        "failed_gates": [],
        "unrun_gates": [],
    }


def catalogue():
    return {
        "live_failures": {},
        "entries": [
            {
                "id": "lookup-pin",
                "team": "evs",
                "title": "Verify the selected release",
                "platform": "OP-E05",
                "source": "https://example.org/issue",
                "requirements": ["X-1"],
                "tests": ["tests/test_example.py::test_lookup"],
                "evidence": ["fixture.json"],
                "observation": "The observed pin differs.",
                "reproduction": "Request the pinned concept.",
                "expected": "Echo the pin.",
                "impact": "The result cannot be reproduced.",
                "workaround": "Fail closed.",
                "acceptance": "The response names the requested pin.",
            }
        ],
    }


class PackageTests(unittest.TestCase):
    def test_checked_in_packages_are_current_and_do_not_claim_live_content_acceptance(self):
        generated = packages.generate()
        for name, text in generated.items():
            self.assertEqual(text, (packages.ROOT / packages.DIRECTORY / name).read_text())
        self.assertIn("0 formal PASS (fixture only)", generated["README.md"])
        self.assertIn("928 not_live, 16 passed", generated["README.md"])
        self.assertIn("C-1", generated["cadsr.md"])
        self.assertIn("E-4", generated["evs.md"])
        self.assertIn("S-5", generated["ssis.md"])

    def test_affected_tests_are_named_by_their_path_from_the_repository_root(self):
        source, fixture, live = catalogue(), report("fixture"), report("live")

        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "fixture.json").write_text("{}")
            lines = packages.entry_text(source["entries"][0], fixture, live, Path(directory))
        text = "\n".join(lines)

        self.assertIn(
            "| `acceptance/tests/test_example.py::test_lookup` | 2 passed | 2 not_live |", text
        )

    def test_an_exact_live_failure_is_qualified_but_another_failure_is_not(self):
        source, fixture, live = catalogue(), report("fixture"), report("live")
        first, second = live["tests"]
        live["tests"][first]["outcome"] = "failed"
        source["live_failures"][first] = "lookup-pin"
        excused = packages.qualifications(source, fixture, live)
        text = packages.overview(source, fixture, live, excused)
        self.assertIn("| `lookup` | PASS (fixture only) | lookup-pin |", text)
        # From docs/ a bare tests/ path would read as the unit suite; the suite is acceptance/.
        self.assertIn(f"`acceptance/{first}` → `lookup-pin`", text)
        live["tests"][second]["outcome"] = "failed"
        text = packages.overview(source, fixture, live, excused)
        self.assertIn("| `lookup` | FAIL |", text)
        self.assertIn("0 formal PASS (fixture only)", text)

    def test_stale_unrun_unrelated_or_fixture_failure_cannot_be_a_live_exception(self):
        for change, message in (
            ("unknown-test", "Unknown exact"),
            ("unknown-entry", "Unknown exact"),
            ("unrun", "observed failure"),
            ("fixture-failure", "fixture failure"),
            ("unrelated", "affected tests"),
        ):
            with self.subTest(change=change):
                source, fixture, live = catalogue(), report("fixture"), report("live")
                first = next(iter(live["tests"]))
                live["tests"][first]["outcome"] = "failed"
                source["live_failures"][first] = "lookup-pin"
                self.alter_qualification(change, source, fixture, live, first)
                with self.assertRaisesRegex(ValueError, message):
                    packages.qualifications(source, fixture, live)

    @staticmethod
    def alter_qualification(change, source, fixture, live, first):
        if change == "unknown-test":
            source["live_failures"] = {"missing": "lookup-pin"}
        elif change == "unknown-entry":
            source["live_failures"][first] = "missing"
        elif change == "unrun":
            live["tests"][first]["outcome"] = "not_live"
        elif change == "fixture-failure":
            fixture["tests"][first]["outcome"] = "failed"
        else:
            source["entries"][0]["tests"] = ["unrelated"]

    def test_incompatible_report_pairs_are_rejected(self):
        variants = [
            {"mode": "fixture"},
            {"suite": {"digest": "different"}},
            {"tests": {}},
            {"tools": {}},
        ]
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                packages.validate_reports(report("fixture"), report("live") | variant)

    def test_invalid_catalogue_references_and_fields_fail_before_rendering(self):
        fixture = json.loads(
            (packages.ROOT / packages.EVIDENCE / "acceptance-fixture.json").read_text()
        )
        source = yaml.safe_load((packages.ROOT / packages.DIRECTORY / "catalogue.yaml").read_text())
        for changes in (
            {"requirements": ["unknown"]},
            {"tests": ["unknown"]},
            {"evidence": ["missing"]},
            {"evidence": []},
            {"tests": "not-a-list"},
            {"team": "unknown"},
            {"impact": ""},
            {"extra": "not supported"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                packages.validate_entry(source["entries"][0] | changes, fixture, packages.ROOT)

    def test_evidence_links_preserve_kind_date_hash_and_request_but_not_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = {
                "kind": "recorded",
                "recorded_on": "2026-10-03",
                "request": {
                    "method": "GET",
                    "surface": "evs",
                    "path": "/concept/pinned",
                    "headers": {"Authorization": "fixture-only-marker"},
                },
            }
            (root / "fixture.json").write_text(json.dumps(record))
            text = packages.evidence_line(root, "fixture.json")
            self.assertIn("recorded on 2026-10-03; GET evs /concept/pinned", text)
            self.assertIn("../../fixture.json", text)
            self.assertNotIn("fixture-only-marker", text)
            (root / "fixture.json").write_text(json.dumps(record | {"kind": "crafted"}))
            changed = packages.evidence_line(root, "fixture.json")
            self.assertIn("— crafted;", changed)
            self.assertNotEqual(text.split("SHA-256")[1], changed.split("SHA-256")[1])

    def test_entry_renders_every_requested_field_and_actual_test_counts(self):
        source, fixture, live = catalogue(), report("fixture"), report("live")
        entry = source["entries"][0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fixture.json").write_text('{"kind":"crafted"}')
            text = "\n".join(packages.entry_text(entry, fixture, live, root))
        for field in packages.PARAGRAPHS:
            self.assertIn(entry[field], text)
        self.assertIn("| 2 passed | 2 not_live |", text)
        self.assertIn("**OP-E05**", text)
        self.assertIn("`X-1`", text)

    def test_duplicate_ids_are_rejected_and_check_detects_changed_generated_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_inputs(root)
            with patch.object(packages, "ROOT", root), patch("sys.argv", ["generate"]):
                packages.main()
            with patch.object(packages, "ROOT", root), patch("sys.argv", ["generate", "--check"]):
                packages.main()
                (root / packages.DIRECTORY / "evs.md").write_text("stale")
                with self.assertRaisesRegex(SystemExit, "Regenerate"):
                    packages.main()
            source = catalogue()
            source["entries"].append(copy.deepcopy(source["entries"][0]))
            (root / packages.DIRECTORY / "catalogue.yaml").write_text(yaml.safe_dump(source))
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                packages.generate(root)

    @staticmethod
    def write_inputs(root):
        for path in (packages.DIRECTORY, packages.EVIDENCE, Path("spec")):
            (root / path).mkdir(parents=True)
        (root / "spec/requirements.yaml").write_text("X-1: {}\n")
        (root / "fixture.json").write_text('{"kind":"crafted"}')
        (root / packages.DIRECTORY / "catalogue.yaml").write_text(yaml.safe_dump(catalogue()))
        for mode in ("fixture", "live"):
            (root / packages.EVIDENCE / f"acceptance-{mode}.json").write_text(
                json.dumps(report(mode))
            )
