"""Caller-visible edge contracts left uncovered by the gate's subprocess tests."""

import ast
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from coverage import CoverageData
from scripts.validation import mutation_gate as gate
from scripts.validation import mutation_probe as probe
from scripts.validation import mutation_run as runner
from scripts.validation import mutation_source as source

from test_mutation_gate import cli, command, project


def git(root, *args):
    return subprocess.check_output(  # noqa: S603 - fixed git commands, owned fixture
        [  # noqa: S607 - fixed git executable
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.org",
            *args,
        ],
        text=True,
        stderr=subprocess.PIPE,
    ).strip()


class MutationCliContractsTest(unittest.TestCase):
    def test_real_main_reports_every_kill_and_returns_the_scheduler_verdict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project(root, "def answer():\n    return True\n")
            status, rows = command(
                root,
                "--paths",
                "src/example.py",
                "--seed",
                "17",
                "--allowlist",
                "missing.yaml",
                "--workers",
                "1",
            )
            self.assertEqual(status, 0, rows)
            self.assertEqual(
                [(row["operator"], row["outcome"]) for row in rows],
                [
                    ("return:True", "NO_OP"),
                    ("boolean:literal", "KILLED"),
                    ("return:False", "KILLED"),
                    ("return:None", "KILLED"),
                ],
            )
            self.assertEqual(
                {row["node"] for row in rows if row["outcome"] == "KILLED"},
                {"tests/test_example.py::test_value"},
            )
            project(
                root, "def answer():\n    return True\n", "assert example.answer() is not Ellipsis"
            )
            status, rows = command(root, "--paths", "src/example.py")
            self.assertEqual(status, 1, rows)
            self.assertEqual(
                [row["outcome"] for row in rows], ["NO_OP", "SURVIVED", "SURVIVED", "SURVIVED"]
            )

    def test_nonpositive_execution_limits_are_usage_errors(self):
        # Given invalid CLI limits, no baseline may start; argparse owns exit 2.
        for option in ("--timeout", "--baseline-timeout", "--workers"):
            for value in ("0", "-1"):
                with self.subTest(option=option, value=value):
                    result = cli(Path.cwd(), option, value)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(
                        result.stderr.splitlines()[-1],
                        "python -m scripts.validation.mutation_gate: error: "
                        "timeout and workers must be positive",
                    )

    def test_legitimate_empty_module_succeeds_without_starting_baselines(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root, "# no executable mutation sites\n")
            self.assertEqual(
                command(root, "--paths", "src/example.py"),
                (0, [{"outcome": "EMPTY", "mutants": 0}]),
            )

    def test_invalid_module_and_syntax_are_machine_readable_infrastructure_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root, "def broken(:\n")
            for path, message in (
                ("tests/test_example.py", "Not an eligible Python module: tests/test_example.py"),
                ("src/example.py", "invalid syntax (<unknown>, line 1)"),
            ):
                with self.subTest(path=path):
                    self.assertEqual(
                        command(root, "--paths", path),
                        (2, [{"outcome": "INFRA", "message": message}]),
                    )

    def test_scheduler_crash_raises_instead_of_returning_partial_results(self):
        def crash(command, cwd, env, timeout, log):
            log.write_text("worker exited 9")
            return 9

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root, "def answer():\n    return True\n")
            mutants, _ = source.candidates("src/example.py", (root / "src/example.py").read_bytes())
            with (
                patch.object(gate, "program", side_effect=crash),
                self.assertRaisesRegex(ValueError, "Mutant scheduler failed: worker exited 9"),
            ):
                gate.schedule(root, mutants, {}, SimpleNamespace(timeout=20, workers=1))


class MutationSourceContractsTest(unittest.TestCase):
    def test_changed_sites_use_merge_base_and_include_multiline_intersections_only(self):
        # Given diverged refs, changes unique to base must not enter the head's scope.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root, "def answer():\n    return (True\n            and False)\n")
            git(root, "init", "-q")
            git(root, "add", ".")
            git(root, "commit", "-qm", "initial")
            initial = git(root, "rev-parse", "HEAD")
            (root / "src/base.py").write_text("flag = True\n")
            git(root, "add", ".")
            git(root, "commit", "-qm", "base only")
            base = git(root, "rev-parse", "HEAD")
            git(root, "checkout", "-q", "--detach", initial)
            (root / "src/example.py").write_text(
                "def answer():\n    return (True\n            and True)\n"
            )
            git(root, "add", ".")
            git(root, "commit", "-qm", "head only")
            selected = source.scope(root, [], base, "HEAD")
            self.assertEqual(selected, {"src/example.py": {3}})
            mutants, diagnostics = gate.collect(root, selected)
            self.assertEqual(diagnostics, [])
            self.assertEqual(
                {(item.operator, item.line, item.end_line) for item in mutants},
                {
                    ("return:None", 2, 3),
                    ("return:True", 2, 3),
                    ("return:False", 2, 3),
                    ("boolean:swap", 2, 3),
                    ("boolean:drop:0", 2, 3),
                    ("boolean:drop:1", 2, 3),
                    ("boolean:literal", 3, 3),
                },
            )

    def test_only_key_mode_and_setting_literal_roles_are_eligible(self):
        snippets = {
            "mapping = {'key': 'value'}": {"'key'"},
            "value = mapping['lookup']": {"'lookup'"},
            "value = 'plain'": set(),
            "value = other('argument')": set(),
            "value = settings.get(key='keyword')": set(),
            "value = settings.get('setting')": {"'setting'"},
            "value = open(path, 'rb')": {"'rb'"},
        }
        for snippet, expected in snippets.items():
            with self.subTest(snippet=snippet):
                mutants, _ = source.candidates("src/example.py", snippet.encode())
                self.assertEqual(
                    {m.before for m in mutants if m.operator == "string:key-mode-setting"}, expected
                )

    def test_equivalent_and_uncompilable_replacements_are_not_executable_candidates(self):
        node = ast.parse("True", mode="eval").body
        self.assertEqual(source.rejection(node, "True", b"True", "x.py"), {"outcome": "NO_OP"})
        self.assertEqual(
            source.rejection(node, "False", b"return False", "x.py"),
            {"outcome": "INVALID", "message": "'return' outside function"},
        )


class MutationVerdictContractsTest(unittest.TestCase):
    def test_only_exact_reviewed_survivors_are_exempt_and_timeout_is_reported_separately(self):
        mutant = source.candidates("src/example.py", b"def answer():\n    return True\n")[0][-1]
        entry = {**mutant.key(), "reason": "Reviewed equivalent."}
        cases = (
            ("SURVIVED", 0, "ALLOWLISTED"),
            ("KILLED", 0, "KILLED"),
            ("TIMEOUT", 0, "TIMEOUT"),
            ("NO_TESTS", 1, "NO_TESTS"),
            ("INFRA", 1, "INFRA"),
        )
        for outcome, expected_status, expected_outcome in cases:
            with self.subTest(outcome=outcome):
                row = {**asdict(mutant), "outcome": outcome}
                output = io.StringIO()
                with redirect_stdout(output):
                    status = gate.verdict([row], [entry])
                expected = {**asdict(mutant), "outcome": expected_outcome}
                if expected_outcome == "ALLOWLISTED":
                    expected["reason"] = "Reviewed equivalent."
                self.assertEqual(
                    (status, json.loads(output.getvalue())), (expected_status, expected)
                )
        for field in mutant.key():
            with self.subTest(mismatched_field=field):
                output = io.StringIO()
                row = {**asdict(mutant), "outcome": "SURVIVED"}
                with redirect_stdout(output):
                    status = gate.verdict([row], [{**entry, field: "different"}])
                self.assertEqual((status, json.loads(output.getvalue())), (1, row))


class MutationEvidenceContractsTest(unittest.TestCase):
    def test_pytest_collector_accepts_only_manifest_name_and_enumerates_exact_job_indices(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mutations.json"
            config = pytest.Config.fromdictargs({}, [])
            self.addCleanup(config._ensure_unconfigure)
            session = pytest.Session.from_config(config)
            self.assertIsNone(probe.pytest_collect_file(path.with_name("other.json"), session))
            collector = probe.pytest_collect_file(path, session)
            self.assertIsInstance(collector, probe.MutationFile)
            for jobs, expected in (([], []), ([{}, {}], ["0", "1"])):
                with self.subTest(jobs=jobs):
                    path.write_text(json.dumps({"mutants": jobs}))
                    self.assertEqual([item.name for item in collector.collect()], expected)

    def test_collection_evidence_records_only_failures_and_never_credits_an_assertion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            with patch.object(probe, "DESTINATION", str(path)):
                probe.pytest_collectreport(SimpleNamespace(failed=False, nodeid="tests/good.py"))
                self.assertFalse(path.exists())
                probe.pytest_collectreport(SimpleNamespace(failed=True, nodeid="tests/bad.py"))
            self.assertEqual(
                json.loads(path.read_text()),
                {
                    "node": "tests/bad.py",
                    "phase": "collection",
                    "outcome": "failed",
                    "assertion": False,
                },
            )

    def test_call_kill_requires_one_owning_call_and_failure_exit_status(self):
        failure = {"node": "tests/test_x.py::test_x", "phase": "call", "assertion": True}
        for status, failures in (
            (0, [failure]),
            (2, [failure]),
            (1, []),
            (1, [failure, failure]),
            (1, [{**failure, "phase": "setup"}]),
            (1, [{**failure, "phase": "teardown"}]),
            (1, [{**failure, "phase": "collection"}]),
        ):
            with self.subTest(status=status, failures=failures):
                self.assertEqual(runner.call_kill(status, failures), {})
        self.assertEqual(
            runner.call_kill(1, [failure]),
            {
                "outcome": "KILLED",
                "node": "tests/test_x.py::test_x",
                "phase": "call",
                "kind": "assertion",
            },
        )

    def test_coverage_mapping_keeps_only_canonical_scratch_paths_and_test_contexts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            scratch = root / "source"
            scratch.mkdir()
            data_file = root / ".coverage"
            with self.assertRaisesRegex(ValueError, "Missing baseline coverage:"):
                runner.coverage_map(data_file, scratch)
            data = CoverageData(basename=str(data_file))
            data.set_context("tests/test_x.py::test_x|run")
            data.add_lines({str(scratch / "src/x.py"): {2}, str(root / "outside.py"): {9}})
            data.set_context("")
            data.add_lines({str(scratch / "src/x.py"): {3}})
            data.write()
            mapping = runner.coverage_map(data_file, scratch)
            self.assertEqual(mapping, {"src/x.py": {2: ["tests/test_x.py::test_x|run"], 3: [""]}})
            self.assertEqual(runner.selection({"unit": mapping}, "src/x.py", 2, 3), {"unit": []})

    def test_baseline_failure_names_the_domain_and_cannot_become_a_successful_map(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root, "def answer():\n    return True\n", "assert False")
            with self.assertRaisesRegex(ValueError, "Baseline unit failed:.*INFRA"):
                runner.baseline(root, 30)
            project(root, "def answer():\n    return True\n")
            (root / "acceptance/selftests/test_harness.py").write_text(
                "def test_harness():\n    assert False\n"
            )
            with self.assertRaisesRegex(ValueError, "Baseline selftest failed:.*INFRA"):
                runner.baseline(root, 30)
            project(root, "def answer():\n    return True\n")
            mapping = runner.baseline(root, 30)
            self.assertEqual(set(mapping), {"unit", "selftest"})
            self.assertEqual(
                runner.selection(mapping, "src/example.py", 2, 2),
                {"unit": ["tests/test_example.py::test_value"]},
            )

    def test_selected_domain_kill_stops_routing_and_survivors_require_fixture_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project(
                root,
                "def answer():\n    return True\n",
                contract=(
                    "import os\n"
                    "    assert {key: os.environ.get(key) for key in "
                    "('NCI_SI_ACCEPTANCE_MODE', 'NCI_SI_ACCEPTANCE_REGISTRY_RELEASE', "
                    "'NCI_SI_ACCEPTANCE_SECURITY_SERVER', 'NCI_SI_ACCEPTANCE_PREPARE')} == "
                    "{'NCI_SI_ACCEPTANCE_MODE': 'fixture', "
                    "'NCI_SI_ACCEPTANCE_REGISTRY_RELEASE': 'required', "
                    "'NCI_SI_ACCEPTANCE_SECURITY_SERVER': "
                    "'python ../scripts/permissions_fixture.py', "
                    "'NCI_SI_ACCEPTANCE_PREPARE': "
                    "'nci-si-mcp index-sample $(cat \"$NCI_SI_ACCEPTANCE_INDEX_CODES\")'}"
                ),
            )
            selected = {"unit": ["tests/test_example.py::test_value"], "selftest": []}
            confirmation = gate.measure(root, selected, 30)
            self.assertEqual(
                (confirmation["outcome"], confirmation["confirmation"]), ("SURVIVED", "fixture")
            )
            contract = root / "acceptance/tests/test_contract.py"
            contract.write_text(contract.read_text() + "    assert False\n")
            result = gate.measure(root, selected, 30)
            self.assertEqual(
                {key: result[key] for key in ("outcome", "domain", "node", "confirmation")},
                {
                    "outcome": "KILLED",
                    "domain": "fixture",
                    "node": "tests/test_contract.py::test_contract",
                    "confirmation": "fixture",
                },
            )
            self.assertEqual(
                [(row["domain"], row["outcome"]) for row in result["domains"]],
                [("unit", "SURVIVED"), ("selftest", "SURVIVED"), ("fixture", "KILLED")],
            )
            (root / "tests/test_example.py").write_text("def test_value():\n    assert False\n")
            result = gate.measure(root, selected, 30)
            self.assertEqual((result["outcome"], result["domain"]), ("KILLED", "unit"))
            self.assertEqual([row["domain"] for row in result["domains"]], ["unit"])
            self.assertNotIn("confirmation", result)
            self.assertEqual(gate.measure(root, {}, 30), {"outcome": "NO_TESTS", "domains": []})
