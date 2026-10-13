"""Original inventory imports identify the rejected snapshot without exposing node IDs."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.evidence_envelope import EvidenceError
from scripts.operator_evidence import _bound_original, bind_original

from test_evidence_acceptance import bundle, encoded, project


class InventoryImportTest(unittest.TestCase):
    def assert_invalid(self, context, path):
        with self.assertRaises(EvidenceError) as failure:
            project(context=context)
        self.assertEqual(str(failure.exception), f"Invalid inventory field: {path}")
        self.assertNotIn("PRIVATE-CANARY", str(failure.exception))
        self.assertNotIn("test_example", str(failure.exception))

    def test_catalogue_fields_and_attribution_are_checked_at_import(self):
        base = json.loads(bundle()["catalogue"])
        cases = [
            (base | {name: value}, name)
            for name, value in (
                ("schema", True),
                ("schema", 2),
                ("suite_digest", None),
                ("suite_digest", "PRIVATE-CANARY"),
                ("tools", []),
                ("cases", []),
            )
        ]
        cases += [(base | {"PRIVATE-CANARY": True}, ""), (None, "")]
        cases += [
            (base | {"tools": {"PRIVATE-CANARY": "evs"}}, "tools[0].name"),
            (base | {"tools": {"lookup": "PRIVATE-CANARY"}}, "tools[0].group"),
        ]
        for name in base:
            changed = base.copy()
            del changed[name]
            cases.append((changed, name))
        for name, value in (
            ("tool", []),
            ("tool", "PRIVATE-CANARY"),
            ("gate", 1),
            ("PRIVATE-CANARY", True),
        ):
            row = {"tool": "lookup", "gate": False} | {name: value}
            cases.append(
                (
                    base | {"cases": {"PRIVATE-CANARY": row}},
                    "cases[0]" + (f".{name}" if name != "PRIVATE-CANARY" else ""),
                )
            )
        cases.append((base | {"cases": {"": {"tool": None, "gate": True}}}, "cases[0]"))
        for value, path in cases:
            with self.subTest(path=path):
                self.assert_invalid(
                    bundle() | {"catalogue": encoded(value)},
                    "catalogue" + (f".{path}" if path else ""),
                )

    def test_story_expectation_and_selection_fields_name_safe_positions(self):
        key = "tests/test_example.py::test_lookup"
        for name in ("stories", "expectations"):
            original = json.loads(bundle()[name])
            for value, path in (
                ({}, name),
                ([], name),
                (original | {"PRIVATE-CANARY": "passed"}, name),
                (original | {key: "PRIVATE-CANARY"}, f"{name}[0]"),
            ):
                self.assert_invalid(bundle() | {name: encoded(value)}, path)
        for selected, path in (
            (None, "selection"),
            ([], "selection"),
            ([1], "selection[0]"),
            ([key, key], "selection"),
            (["PRIVATE-CANARY"], "selection"),
        ):
            self.assert_invalid(bundle() | {"selection": encoded(selected)}, path)

    def test_identifier_length_and_digest_boundaries_preserve_valid_snapshots(self):
        original = json.loads(bundle()["stories"])
        self.assertTrue(
            project(context=bundle() | {"stories": encoded(dict.fromkeys(original, "a" * 100))})[
                "inventory_complete"
            ]
        )
        self.assert_invalid(
            bundle() | {"stories": encoded(dict.fromkeys(original, "a" * 101))}, "stories[0]"
        )
        for value in ("a" * 63, "a" * 65, "A" * 64):
            catalogue = json.loads(bundle()["catalogue"]) | {"suite_digest": value}
            self.assert_invalid(
                bundle() | {"catalogue": encoded(catalogue)}, "catalogue.suite_digest"
            )


class BindingImportTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "bundle").mkdir()
        for name, raw in bundle().items():
            (self.root / "bundle" / f"{name}.json").write_bytes(raw)
        self.job = {"commit": "a" * 40}
        bind_original(self.root, self.job["commit"])

    def test_binding_rejections_name_the_changed_field_or_snapshot(self):
        path = self.root / "binding.json"
        original = json.loads(path.read_text())
        cases = [
            (original | {"commit": "PRIVATE-CANARY"}, "commit"),
            (original | {"PRIVATE-CANARY": True}, "binding"),
            ({"digests": original["digests"]}, "commit"),
            ({"commit": original["commit"]}, "digests"),
            ([], "binding"),
        ]
        for name in original["digests"]:
            cases.append(
                (
                    original | {"digests": original["digests"] | {name: "PRIVATE-CANARY"}},
                    f"digests.{name}",
                )
            )
        cases.append(
            (original | {"digests": original["digests"] | {"PRIVATE-CANARY": "x"}}, "digests")
        )
        for value, field in cases:
            with self.subTest(field=field):
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError) as failure:
                    _bound_original(self.job, self.root)
                self.assertEqual(str(failure.exception), f"Invalid inventory field: {field}")
                self.assertNotIn("PRIVATE-CANARY", str(failure.exception))
