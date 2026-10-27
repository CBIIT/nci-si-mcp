"""The mutation command measures assertion sensitivity without editing its caller."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from scripts.validation import mutation_gate
from scripts.validation.mutation_gate import execute_job
from scripts.validation.mutation_run import coverage_map, run_domain, selection, snapshot
from scripts.validation.mutation_source import allowlist, candidates, diff_lines, scope

ROOT = Path(__file__).resolve().parents[1]


def project(root, source, unit="assert example.answer() is True", contract="assert 1 == 1"):
    files = {
        "src/example.py": source,
        "tests/test_example.py": f"import example\ndef test_value():\n    {unit}\n",
        "acceptance/selftests/test_harness.py": "def test_harness():\n    assert 2 == 2\n",
        "acceptance/tests/test_contract.py": (
            f"import example\ndef test_contract():\n    {contract}\n"
        ),
        "acceptance/conftest.py": (
            "def pytest_addoption(parser):\n    parser.addoption('--report')\n"
        ),
        "pyproject.toml": '[tool.pytest.ini_options]\npythonpath = ["src"]\n',
    }
    for name, value in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)


def command(root, *args):
    result = cli(root, *args)
    return result.returncode, [json.loads(line) for line in result.stdout.splitlines()]


def mutant_rows(rows):
    return [row for row in rows if "site" in row]


def domain_routes(rows):
    return [[run["domain"] for run in row["domains"]] for row in rows]


def json_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def cli(root, *args):
    return subprocess.run(  # noqa: S603 - fixed PDM command, owned paths
        ["pdm", "run", "mutation-gate", "--root", str(root), *args],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


class MutationOperatorsTest(unittest.TestCase):
    def test_fixed_function_context_hash_matches_the_ci_interpreter_contract(self):
        mutants, _ = candidates("src/example.py", b"def answer():\n    return True\n")
        self.assertEqual(
            {mutant.context for mutant in mutants},
            {"a0b3ebdc264a10cfed0053301d6d73c06f2e16170d397107ffbc7ba0a3a6be81"},
        )

    def test_multiline_expression_sites_compile_and_module_keys_survive_statement_line_shifts(self):
        source = b"flag = (True\n        and False)\n"
        mutants, _ = candidates("src/example.py", source)
        shifted, _ = candidates("src/example.py", b"# moved\n\n" + source)
        self.assertEqual([item.key() for item in mutants], [item.key() for item in shifted])
        self.assertEqual(
            {item.operator for item in mutants},
            {"boolean:literal", "boolean:swap", "boolean:drop:0", "boolean:drop:1"},
        )
        for item in mutants:
            changed = source[: item.start] + item.after.encode() + source[item.end :]
            self.assertIsNotNone(compile(changed, item.file, "exec"))

    def test_comparison_boolean_and_file_mode_variants_compile_without_touching_other_bytes(self):
        expressions = (
            "a == b",
            "a != b",
            "a < b",
            "a <= b",
            "a > b",
            "a >= b",
            "a is b",
            "a is not b",
            "a in b",
            "a not in b",
            "a < b < c",
            "a or b or c",
            "a and b and c",
            "not a",
            "False",
            "a < 2",
            "open('name', 'rb')",
            "settings.get('name')",
            "{'key': 1}",
        )
        for expression in expressions:
            with self.subTest(expression=expression):
                source = (
                    f"# é\ndef outer():\n    def inner(a, b, c):\n        return {expression}\n"
                )
                mutants, diagnostics = candidates("src/example.py", source.encode())
                self.assertTrue(mutants)
                self.assertLessEqual({row["outcome"] for row in diagnostics}, {"NO_OP"})
                for mutant in mutants:
                    changed = source.encode()[: mutant.start] + mutant.after.encode()
                    changed += source.encode()[mutant.end :]
                    compile(changed, "example.py", "exec")
                    self.assertEqual(mutant.qualname, "outer.inner")
                    self.assertTrue(changed.startswith(b"# \xc3\xa9\ndef outer():"))

    def test_ci_runs_every_pr_to_main_with_read_only_head_checkout_and_data_arguments(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        self.assertEqual(
            workflow["jobs"]["mutation"],
            {
                "name": "mutation (diff)",
                "if": "github.event_name == 'pull_request' && github.base_ref == 'main'",
                "permissions": {"contents": "read"},
                "runs-on": "ubuntu-24.04",
                "timeout-minutes": 300,
                "strategy": {"fail-fast": False, "matrix": {"shard": [1, 2, 3]}},
                "steps": [
                    {
                        "uses": "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
                        "with": {
                            "ref": "${{ github.event.pull_request.head.sha }}",
                            "fetch-depth": 0,
                            "persist-credentials": False,
                        },
                    },
                    {"uses": "$/.github/actions/setup"},
                    {
                        "name": "Mutate every changed Python site",
                        "env": {
                            "BASE_SHA": "${{ github.event.pull_request.base.sha }}",
                            "HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
                            "SHARD": "${{ matrix.shard }}/3",
                        },
                        "run": (
                            'pdm run mutation-gate --base "$BASE_SHA" --head "$HEAD_SHA" '
                            '--shard "$SHARD" -n 2'
                        ),
                    },
                ],
            },
        )

    def test_fixed_operators_change_only_valid_utf8_source_and_are_killed_by_real_assertions(self):
        source = (
            "events = []\n"
            "def answer(a=2, b=3):\n"
            "    café = 'é'; flag = True\n"
            "    events.append(a)\n"
            "    if a < b and not a == 0:\n"
            "        return {'key': a if flag else 99}.get('key')\n"
            "    raise ValueError('bad')\n"
        )
        test = (
            "import pytest\n"
            "    try:\n"
            "        value = example.answer()\n"
            "        other = example.answer(1)\n"
            "    except (ValueError, KeyError) as error:\n"
            "        pytest.fail(str(error))\n"
            "    assert value == 2\n"
            "    assert other == 1\n"
            "    assert example.events == [2, 1]\n"
            "    with pytest.raises(ValueError):\n"
            "        example.answer(0)\n"
            "    with pytest.raises(ValueError):\n"
            "        example.answer(3, 2)\n"
            "    example.answer(2); example.answer(2)\n"
            "    assert example.answer(2) == 2"
        )
        mutants, rejected = candidates("src/example.py", source.encode())
        self.assertEqual({row["outcome"] for row in rejected}, set())
        self.assertEqual(
            {mutant.operator.split(":")[0] for mutant in mutants},
            {"comparison", "boolean", "not", "return", "raise", "integer", "call", "string"},
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project(root, source, test)
            status, rows = command(root, "--paths", "src/example.py", "-n", "2", "--seed", "19")
            self.assertEqual(status, 0, rows)
            self.assertEqual(len(rows), len(mutants))
            self.assertEqual({row["outcome"] for row in rows}, {"KILLED"})
            self.assertEqual({row["domain"] for row in rows}, {"unit"})
            self.assertEqual({row["phase"] for row in rows}, {"call"})
            self.assertEqual((root / "src/example.py").read_bytes(), source.encode())

    def test_comment_shifts_keep_keys_but_function_changes_invalidate_full_source_exemptions(self):
        source = "def answer():\n    return True\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project(root, source)
            mutants, _ = candidates("src/example.py", source.encode())
            entry = {**mutants[-1].key(), "reason": "Equivalent for the documented caller."}
            path = root / "allow.yaml"
            path.write_text(yaml.safe_dump([entry]))
            (root / "src/example.py").write_text("# moved\n\n" + source)
            self.assertEqual(allowlist(root, path), [entry])
            (root / "src/example.py").write_text(source.replace("return", "unused = 2\n    return"))
            with self.assertRaisesRegex(ValueError, "Stale"):
                allowlist(root, path)
            status, rows = command(
                root, "--paths", "scripts/missing.py", "--allowlist", "allow.yaml"
            )
            self.assertEqual(status, 2)
            self.assertIn("Stale", rows[-1]["message"])

    def test_allowlist_rejects_duplicate_ambiguous_malformed_entries(self):
        source = "def answer():\n    return True\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project(root, source)
            mutants, _ = candidates("src/example.py", source.encode())
            entry = {**mutants[-1].key(), "reason": "Reviewed equivalent."}
            path = root / "allow.yaml"
            for entries in ([entry, entry], [{**entry, "extra": "bad"}], [{**entry, "reason": ""}]):
                with self.subTest(entries=entries):
                    path.write_text(yaml.safe_dump(entries))
                    with self.assertRaises(ValueError):
                        allowlist(root, path)
            (root / "src/example.py").write_text(source + source)
            path.write_text(yaml.safe_dump([entry]))
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                allowlist(root, path)

    def test_diff_scopes_exact_head_lines_and_explicit_paths_cover_whole_modules(self):
        diff = "+++ b/src/example.py\n@@ -2 +2 @@\n-x\n+y\n+++ b/tests/test_x.py\n@@ -1 +1 @@\n"
        self.assertEqual(diff_lines(diff), {"src/example.py": {2}})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project(root, "def answer():\n    return True\n")
            self.assertEqual(
                scope(root, ["src/example.py"], "unused", "unused"), {"src/example.py": None}
            )
            with self.assertRaises(ValueError):
                scope(root, ["tests/test_example.py"], "unused", "unused")

    def test_contexts_are_domain_qualified_and_only_unattributed_lines_use_whole_domain(self):
        mapping = {
            "unit": {"src/a.py": {2: ["tests/test_a.py::test_a|run"]}},
            "selftest": {"src/a.py": {2: ["tests/test_a.py::test_a|setup", ""]}},
        }
        self.assertEqual(
            selection(mapping, "src/a.py", 2, 2),
            {"unit": ["tests/test_a.py::test_a"], "selftest": []},
        )
        self.assertEqual(selection(mapping, "src/a.py", 9, 9), {})
        for contexts in (
            ["", "tests/test_a.py::test_a|run"],
            ["tests/test_a.py::test_a|setup", "tests/test_a.py::test_a|run"],
            ["tests/test_a.py::test_a|teardown", "tests/test_a.py::test_a|run"],
        ):
            with self.subTest(contexts=contexts):
                self.assertEqual(
                    selection({"unit": {"src/a.py": {2: contexts}}}, "src/a.py", 2, 2),
                    {"unit": ["tests/test_a.py::test_a"]},
                )
        with self.assertRaisesRegex(ValueError, "Unrecognized coverage context"):
            selection({"unit": {"src/a.py": {2: ["unknown"]}}}, "src/a.py", 2, 2)


class MutationExecutionTest(unittest.TestCase):
    def test_prepared_fixture_stdio_rpc_reaches_source_with_honest_setup_fallback(self):
        # A session-scoped MCP child keeps its spawn-time setup context even
        # when this test's later RPC executes a handler for the first time.
        node = (
            "tests/test_evs.py::test_a_discovered_release_identifies_the_concept_and_its_provenance"
        )
        with snapshot(ROOT) as scratch:
            result, data = run_domain(scratch, "fixture", [node], 120, baseline=True)
            self.assertEqual(result["outcome"], "SURVIVED", result)
            mapping = coverage_map(data, scratch)
            source = "src/nci_si_mcp/content.py"
            line = next(
                number
                for number, text in enumerate((scratch / source).read_text().splitlines(), 1)
                if text.strip() == "code = _code(code, terminology)"
            )
            self.assertEqual(set(mapping[source][line]), {f"{node}|setup"})
            self.assertEqual(selection({"fixture": mapping}, source, line, line), {"fixture": []})
            self.assertFalse(
                any(
                    mapping.get(f"scripts/validation/{name}.py", {})
                    for name in (
                        "mutation_gate",
                        "mutation_run",
                        "mutation_source",
                        "mutation_probe",
                    )
                )
            )

    def test_unreached_fixture_survivor_never_launches_a_confirmation_process(self):
        # Given a unit-only call, the full fixture baseline still runs, but no
        # mutant may launch a fixture process that cannot execute the changed line.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project(
                root, "def answer():\n    return True\n", "assert example.answer() is not Ellipsis"
            )
            (root / "acceptance/tests/test_contract.py").write_text(
                "def test_contract():\n    assert 1 == 1\n"
            )
            log = root / "fixture-calls.jsonl"
            (root / "acceptance/conftest.py").write_text(
                "import json, os\nfrom pathlib import Path\n"
                "def pytest_addoption(parser):\n    parser.addoption('--report')\n"
                "def pytest_sessionstart(session):\n"
                "    if os.environ.get('NCI_SI_ACCEPTANCE_MODE') != 'fixture':\n        return\n"
                f"    with Path({str(log)!r}).open('a') as stream:\n"
                "        stream.write(json.dumps(session.config.getoption('cov_context'))"
                " + '\\n')\n"
            )
            status, rows = command(root, "--paths", "src/example.py")
            survivors = mutant_rows(rows)
            self.assertEqual(status, 1, rows)
            self.assertEqual({row["outcome"] for row in survivors}, {"SURVIVED"})
            self.assertEqual({row.get("confirmation") for row in survivors}, {"unreached"})
            self.assertEqual(
                domain_routes(survivors),
                [["unit"]] * 3,
            )
            launches = json_lines(log)
            # One controller plus its four workers belong to the mandatory baseline.
            self.assertEqual(len(launches), 5, launches)
            self.assertEqual(launches, ["test"] * 5)

    def test_fixture_only_child_line_selects_its_one_owner_and_kills_without_unit_coverage(self):
        # Given a line reached only in a fixture test's real child program, run
        # that owner alone after the clean three-domain baseline, never NO_TESTS.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project(root, "def answer():\n    return True\n", "assert True")
            log = root / "fixture-calls.jsonl"
            (root / "acceptance/conftest.py").write_text(
                "import json\nfrom pathlib import Path\n"
                "def pytest_addoption(parser):\n    parser.addoption('--report')\n"
                "def pytest_runtest_logreport(report):\n"
                "    if report.when == 'call':\n"
                f"        with Path({str(log)!r}).open('a') as stream:\n"
                "            stream.write(json.dumps(report.nodeid) + '\\n')\n"
            )
            (root / "acceptance/tests/test_contract.py").write_text(
                "import subprocess, sys\n"
                "def test_contract():\n"
                "    child = subprocess.run([sys.executable, '-c', "
                "'import example; print(example.answer())'], capture_output=True, text=True)\n"
                "    assert child.returncode == 0, child.stderr\n"
                "    assert child.stdout.strip() == 'True'\n"
                "def test_unrelated():\n    assert True\n"
            )
            status, rows = command(root, "--paths", "src/example.py")
            results = mutant_rows(rows)
            self.assertEqual(status, 0, rows)
            self.assertEqual({row["outcome"] for row in results}, {"KILLED"})
            self.assertEqual(
                {row["node"] for row in results}, {"tests/test_contract.py::test_contract"}
            )
            self.assertEqual({row["confirmation"] for row in results}, {"fixture"})
            self.assertEqual([row["fallback_domains"] for row in results], [[], [], []])
            self.assertEqual(
                domain_routes(results),
                [["fixture"]] * 3,
            )
            calls = json_lines(log)
            # xdist's worker and controller both report baseline calls; serial
            # confirmations report once. Unrelated calls belong only to baseline.
            self.assertEqual(calls.count("tests/test_contract.py::test_unrelated"), 2)
            self.assertEqual(calls.count("tests/test_contract.py::test_contract"), 5)

    def test_subject_probe_plugin_labels_real_child_phases_and_restores_parent_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project(root, "def answer():\n    return True\n")
            shutil.copytree(ROOT / "scripts", root / "scripts")
            (root / "tests/test_nested.py").write_text(
                "import os, pytest\n"
                "@pytest.fixture\ndef phase():\n"
                "    assert os.environ['MUTATION_COVERAGE_CONTEXT'] == "
                "'tests/test_nested.py::test_phases|setup'\n"
                "    yield\n"
                "    assert os.environ['MUTATION_COVERAGE_CONTEXT'] == "
                "'tests/test_nested.py::test_phases|teardown'\n"
                "def test_phases(phase):\n"
                "    assert os.environ['MUTATION_COVERAGE_CONTEXT'] == "
                "'tests/test_nested.py::test_phases|run'\n"
            )
            (root / "tests/conftest.py").write_text(
                "import os\ndef pytest_runtest_logfinish(nodeid):\n"
                "    if nodeid.endswith('test_phases'):\n"
                "        assert os.environ['MUTATION_COVERAGE_CONTEXT'] == "
                "'tests/test_example.py::test_plugin|run'\n"
            )
            (root / "tests/test_example.py").write_text(
                "import subprocess, sys\ndef test_plugin():\n"
                "    result = subprocess.run([sys.executable, '-m', 'pytest', '-p', "
                "'scripts.validation.mutation_probe', '--no-cov', '-n', '0', "
                "'tests/test_nested.py'], capture_output=True, text=True)\n"
                "    assert result.returncode == 0, result.stdout + result.stderr\n"
            )
            result, data = run_domain(
                root, "unit", ["tests/test_example.py::test_plugin"], 30, baseline=True
            )
            self.assertEqual(result["outcome"], "SURVIVED", result)
            mapping = coverage_map(data, root)
            for name in ("pytest_runtest_setup", "pytest_runtest_call", "pytest_runtest_teardown"):
                mutants, _ = candidates(
                    "scripts/validation/mutation_probe.py",
                    (root / "scripts/validation/mutation_probe.py").read_bytes(),
                )
                routes = mutation_gate.manifest_data(
                    root, [m for m in mutants if m.qualname == name], {"unit": mapping}, 30
                )
                self.assertEqual(
                    [row["selected"] for row in routes["mutants"]],
                    [{"unit": ["tests/test_example.py::test_plugin"]}] * len(routes["mutants"]),
                )

    def test_python_module_children_keep_each_xdist_owners_phase_and_inprocess_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project(root, "def answer():\n    return True\n")
            (root / "src/child.py").write_text(
                "import sys\ndef first():\n    return True\n"
                "def second():\n    return True\n"
                "if __name__ == '__main__':\n    print(globals()[sys.argv[1]]())\n"
            )
            (root / "acceptance/selftests/test_harness.py").write_text(
                "import subprocess, sys, example\nimport pytest\n"
                "def launch(name):\n"
                "    result = subprocess.run([sys.executable, '-m', 'child', name], "
                "capture_output=True, text=True, check=True)\n"
                "    assert result.stdout.strip() == 'True'\n"
                "@pytest.fixture\ndef child_fixture():\n"
                "    launch('first')\n    yield\n    launch('second')\n"
                "def test_first():\n    launch('first')\n    assert example.answer()\n"
                "def test_second():\n    launch('second')\n"
                "def test_phases(child_fixture):\n    assert True\n"
            )
            result, data = run_domain(root, "selftest", [], 30, baseline=True)
            self.assertEqual(result["outcome"], "SURVIVED", result)
            mapping = coverage_map(data, root)
            self.assertEqual(
                selection({"selftest": mapping}, "src/child.py", 3, 3),
                {"selftest": ["selftests/test_harness.py::test_first"]},
            )
            self.assertEqual(
                selection({"selftest": mapping}, "src/child.py", 5, 5),
                {"selftest": ["selftests/test_harness.py::test_second"]},
            )
            self.assertEqual(
                selection({"selftest": mapping}, "src/example.py", 2, 2),
                {"selftest": ["selftests/test_harness.py::test_first"]},
            )
            self.assertEqual(
                set(mapping["src/child.py"][3]),
                {
                    "selftests/test_harness.py::test_first|run",
                    "selftests/test_harness.py::test_phases|setup",
                },
            )
            self.assertEqual(
                set(mapping["src/child.py"][5]),
                {
                    "selftests/test_harness.py::test_second|run",
                    "selftests/test_harness.py::test_phases|teardown",
                },
            )

    def test_call_typeerror_is_an_error_kill_but_setup_error_is_infrastructure(self):
        source = "def answer():\n    return True\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project(root, source, "example.answer() + 1")
            baseline, _ = run_domain(root, "unit", [], 20, baseline=True)
            self.assertEqual(baseline["outcome"], "SURVIVED", baseline)
            (root / "src/example.py").write_text(source.replace("True", "None"))
            result, _ = run_domain(root, "unit", [], 20)
            self.assertEqual(
                {key: result.get(key) for key in ("outcome", "kind", "node", "phase")},
                {
                    "outcome": "KILLED",
                    "kind": "error",
                    "node": "tests/test_example.py::test_value",
                    "phase": "call",
                },
            )
            (root / "tests/conftest.py").write_text(
                "import pytest, example\n@pytest.fixture(autouse=True)\n"
                "def setup():\n    example.answer() + 1\n"
            )
            result, _ = run_domain(root, "unit", [], 20)
            self.assertEqual(result["outcome"], "INFRA", result)
            self.assertEqual(result["failures"][0]["phase"], "setup")

    def test_mixed_subtests_keep_one_owning_error_kill(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project(root, "def answer():\n    return True\n")
            (root / "tests/test_example.py").write_text(
                "import unittest, example\nclass Mixed(unittest.TestCase):\n"
                "    def test_mixed(self):\n"
                "        with self.subTest(kind='assertion'):\n"
                "            self.assertIs(example.answer(), True)\n"
                "        with self.subTest(kind='error'):\n"
                "            example.answer() + 1\n"
            )
            baseline, _ = run_domain(root, "unit", [], 20, baseline=True)
            self.assertEqual(baseline["outcome"], "SURVIVED", baseline)
            (root / "src/example.py").write_text("def answer():\n    return None\n")
            result, data = run_domain(root, "unit", [], 20)
            self.assertEqual((result["outcome"], result.get("kind")), ("KILLED", "error"))
            rows = [
                json.loads(line) for line in data.with_name("events.jsonl").read_text().splitlines()
            ]
            self.assertEqual(
                [row for row in rows if row["outcome"] == "failed"],
                [
                    {
                        "node": "tests/test_example.py::Mixed::test_mixed",
                        "phase": "call",
                        "outcome": "failed",
                        "assertion": False,
                    }
                ],
            )

    def test_mutated_probe_is_subject_but_cannot_corrupt_controller_collection_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project(root, "def answer():\n    return True\n")
            package = root / "scripts/validation"
            package.mkdir(parents=True)
            (package.parent / "__init__.py").write_text("")
            (package / "__init__.py").write_text("")
            original = (ROOT / "scripts/validation/mutation_probe.py").read_text()
            subject = package / "mutation_probe.py"
            subject.write_text(original)
            (root / "tests/test_example.py").write_text(
                "from pathlib import Path\nfrom types import SimpleNamespace\n"
                "from scripts.validation import mutation_probe as subject\n"
                "def test_collection_contract(tmp_path):\n"
                "    destination = tmp_path / 'subject.jsonl'\n"
                "    original = subject.DESTINATION\n"
                "    try:\n"
                "        subject.DESTINATION = str(destination)\n"
                "        subject.pytest_collectreport(\n"
                "            SimpleNamespace(failed=False, nodeid='good'))\n"
                "    finally:\n"
                "        subject.DESTINATION = original\n"
                "    assert not destination.exists()\n"
            )
            baseline, _ = run_domain(root, "unit", [], 20, baseline=True)
            self.assertEqual(baseline["outcome"], "SURVIVED", baseline)
            subject.write_text(original.replace("destination and report.failed", "destination"))
            result, data = run_domain(root, "unit", [], 20)
            self.assertEqual(
                (result["outcome"], result.get("kind")), ("KILLED", "assertion"), result
            )
            events = [
                json.loads(line) for line in data.with_name("events.jsonl").read_text().splitlines()
            ]
            self.assertEqual(
                [(row["node"], row["phase"]) for row in events],
                [
                    ("tests/test_example.py::test_collection_contract", phase)
                    for phase in ("setup", "call", "teardown")
                ],
            )

    def test_baseline_maps_line_executed_only_in_pdm_script_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project(root, "def answer():\n    return True\n")
            (root / "pyproject.toml").write_text(
                '[project]\nname = "mapping-contract"\nversion = "0.0.0"\n'
                'requires-python = ">=3.14"\n'
                '[tool.pytest.ini_options]\npythonpath = ["src"]\n'
                '[tool.pdm.scripts]\nmutation-gate = "python -m scripts.validation.mutation_gate"\n'
            )
            (root / ".pdm-python").write_text(sys.executable)
            (root / "src/child.py").write_text("# no mutation sites\n")
            # Use the actual command helper in a snapshot of the invoking project;
            # its child runs mutation_gate.main while the inner gate remains uninstrumented.
            (root / "tests/test_example.py").write_text(
                "from test_mutation_gate import command\nfrom pathlib import Path\n"
                "def test_child():\n"
                "    status, rows = command(Path.cwd(), '--paths', 'src/child.py')\n"
                "    assert (status, rows) == (0, [{'outcome': 'EMPTY', 'mutants': 0}])\n"
                "def test_unrelated():\n    assert True\n"
            )
            # The helper's ROOT must be this snapshot, as it is in a real baseline.
            (root / "tests/test_mutation_gate.py").write_text(
                (ROOT / "tests/test_mutation_gate.py")
                .read_text()
                .split("class MutationOperatorsTest")[0]
            )
            shutil.copytree(ROOT / "scripts", root / "scripts")
            result, data = run_domain(root, "unit", [], 30, baseline=True)
            self.assertEqual(result["outcome"], "SURVIVED", result)
            mapping = coverage_map(data, root)
            line = next(
                i
                for i, text in enumerate(
                    (root / "scripts/validation/mutation_gate.py").read_text().splitlines(), 1
                )
                if text.strip() == "args = parser.parse_args(argv)"
            )
            self.assertEqual(
                selection({"unit": mapping}, "scripts/validation/mutation_gate.py", line, line),
                {"unit": ["tests/test_example.py::test_child"]},
            )
            mutants, _ = candidates(
                "scripts/validation/mutation_gate.py",
                (root / "scripts/validation/mutation_gate.py").read_bytes(),
            )
            child_only = [m for m in mutants if m.qualname == "main" and m.before == "return 0"]
            self.assertEqual(len(child_only), 3)
            routes = mutation_gate.manifest_data(root, child_only, {"unit": mapping}, 30)
            self.assertEqual(
                [row["selected"] for row in routes["mutants"]],
                [{"unit": ["tests/test_example.py::test_child"]}] * 3,
            )

    def test_inherited_probe_child_cannot_append_to_parent_evidence(self):
        # A caller may launch pytest directly, without the gate's environment helper.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project(root, "def answer():\n    return True\n")
            (root / "tests/test_child.py").write_text("def test_child():\n    assert True\n")
            (root / "tests/test_example.py").write_text(
                "import subprocess, sys\ndef test_outer():\n"
                "    child = subprocess.run([sys.executable, '-m', 'pytest', '-p', "
                "'scripts.validation.mutation_probe', '--no-cov', '-n', '0', "
                "'tests/test_child.py'], capture_output=True)\n"
                "    assert child.returncode == 0\n"
                "    assert False, 'outer mutation detected'\n"
            )
            result, data = run_domain(root, "unit", ["tests/test_example.py::test_outer"], 30)
            self.assertEqual(result["outcome"], "KILLED", result)
            events = [
                json.loads(line) for line in data.with_name("events.jsonl").read_text().splitlines()
            ]
            self.assertEqual({row["node"] for row in events}, {"tests/test_example.py::test_outer"})

    def test_scheduler_manifest_shares_source_and_keeps_only_selected_test_routes(self):
        source = "def answer():\n    return True\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project(root, source)
            mutants, _ = candidates("src/example.py", source.encode())
            mutant = mutants[-1]
            mapping = {"unit": {"src/example.py": {2: ["tests/test_example.py::test_value|run"]}}}
            captured = []

            def scheduler(command, cwd, env, timeout, log):
                manifest = Path(command[-1])
                captured.append(json.loads(manifest.read_text()))
                (manifest.parent / "result-0.json").write_text('{"outcome": "KILLED"}')
                return 0

            with patch.object(mutation_gate, "program", side_effect=scheduler):
                rows = mutation_gate.schedule(
                    root, [mutant], mapping, SimpleNamespace(timeout=20, workers=1)
                )
            self.assertEqual(rows, [{"outcome": "KILLED"}])
            self.assertEqual(
                captured,
                [
                    {
                        "root": str(root),
                        "sources": {"src/example.py": source},
                        "mutants": [
                            {
                                **mutant.key(),
                                "line": 2,
                                "end_line": 2,
                                "start": mutant.start,
                                "end": mutant.end,
                                "after": "False",
                                "selected": {"unit": ["tests/test_example.py::test_value"]},
                            }
                        ],
                        "timeout": 20,
                    }
                ],
            )

    def test_mutant_report_records_measured_total_and_domain_elapsed_seconds(self):
        source = "def answer():\n    return True\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project(root, source)
            mutants, _ = candidates("src/example.py", source.encode())
            manifest = root / "mutations.json"
            manifest.write_text(
                json.dumps(
                    {
                        "root": str(root),
                        "sources": {"src/example.py": source},
                        "mutants": [
                            {
                                **{
                                    key: value
                                    for key, value in asdict(mutants[-1]).items()
                                    if key != "before"
                                },
                                "selected": {"unit": ["tests/test_example.py::test_value"]},
                            }
                        ],
                        "timeout": 20,
                    }
                )
            )
            execute_job(manifest, 0)
            row = json.loads((root / "result-0.json").read_text())
            self.assertEqual(
                set(row) & {"elapsed_seconds", "domains"}, {"elapsed_seconds", "domains"}
            )
            self.assertEqual(
                [
                    {key: value for key, value in domain.items() if key != "elapsed_seconds"}
                    for domain in row["domains"]
                ],
                [{"domain": "unit", "outcome": "KILLED"}],
            )
            self.assertIsInstance(row["elapsed_seconds"], float)
            self.assertIsInstance(row["domains"][0]["elapsed_seconds"], float)

    def test_nested_probe_and_multiple_subtest_failures_credit_exactly_one_outer_kill(self):
        # Given a selected unittest that runs a child probe, when two subtests detect
        # the mutant, then only its outer node owns the kill and child evidence is separate.
        source = "def answer():\n    return True\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project(root, source)
            project(root / "nested", source)
            (root / "tests/test_example.py").write_text(
                "import unittest, example\nfrom pathlib import Path\n"
                "from scripts.validation.mutation_run import run_domain\n"
                "class Outer(unittest.TestCase):\n"
                "    def test_nested(self):\n"
                "        result, data = run_domain(Path('nested').resolve(), 'unit', [], 20)\n"
                "        self.assertEqual(result['outcome'], 'SURVIVED')\n"
                "        for value in (1, 2):\n"
                "            with self.subTest(value=value):\n"
                "                self.assertIs(example.answer(), True)\n"
            )
            (root / "src/example.py").write_text(source.replace("True", "False"))
            result, data = run_domain(
                root, "unit", ["tests/test_example.py::Outer::test_nested"], 30
            )
            self.assertEqual(result["outcome"], "KILLED", result)
            self.assertEqual(result["node"], "tests/test_example.py::Outer::test_nested")
            events = [
                json.loads(line) for line in data.with_name("events.jsonl").read_text().splitlines()
            ]
            self.assertEqual(
                [row for row in events if row["outcome"] == "failed"],
                [
                    {
                        "node": "tests/test_example.py::Outer::test_nested",
                        "phase": "call",
                        "outcome": "failed",
                        "assertion": True,
                    }
                ],
            )
            self.assertEqual(
                {row["node"] for row in events}, {"tests/test_example.py::Outer::test_nested"}
            )

    def test_uncovered_sites_fail_no_tests_and_reviewed_survivors_record_unreached_fixture(
        self,
    ):
        source = "def answer():\n    return True\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project(root, source, "assert True")
            status, rows = command(root, "--paths", "src/example.py")
            self.assertEqual(status, 1)
            self.assertEqual({row["outcome"] for row in rows}, {"NO_TESTS", "NO_OP"})
            project(root, source, "assert example.answer() is not Ellipsis")
            mutants, _ = candidates("src/example.py", source.encode())
            (root / "allow.yaml").write_text(
                yaml.safe_dump(
                    [
                        {**mutant.key(), "reason": "Fixture deliberately ignores this value."}
                        for mutant in mutants
                    ]
                )
            )
            status, rows = command(root, "--paths", "src/example.py", "--allowlist", "allow.yaml")
            self.assertEqual(status, 0, rows)
            allowed = [row for row in rows if row["outcome"] == "ALLOWLISTED"]
            self.assertEqual(len(allowed), 3)
            self.assertEqual({row["confirmation"] for row in allowed}, {"unreached"})

    def test_fixture_owns_a_kill_when_units_only_execute_the_value(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project(
                root,
                "def answer():\n    return True\n",
                "assert example.answer() is not Ellipsis",
                "assert example.answer() is True",
            )
            status, rows = command(root, "--paths", "src/example.py", "-n", "2")
            actual = [row for row in rows if row["outcome"] != "NO_OP"]
            self.assertEqual(status, 0, rows)
            self.assertEqual({row["outcome"] for row in actual}, {"KILLED"})
            self.assertEqual({row["domain"] for row in actual}, {"fixture"})
            self.assertEqual(
                {row["node"] for row in actual}, {"tests/test_contract.py::test_contract"}
            )
            self.assertEqual({row["confirmation"] for row in actual}, {"fixture"})

    def test_child_imports_and_console_programs_use_scratch_and_seed_does_not_change_results(self):
        source = "def answer():\n    return True\n"
        unit = (
            "import subprocess, sys\n"
            "    child = subprocess.run([sys.executable, '-c', "
            "'import example; print(example.answer())'], capture_output=True, text=True)\n"
            "    assert child.stdout.strip() == 'True'\n"
            "    console = subprocess.run(['nci-si-mcp'], capture_output=True, text=True)\n"
            "    assert console.stdout.strip() == 'True'"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project(root, source, unit)
            package = root / "src/nci_si_mcp"
            package.mkdir()
            (package / "__init__.py").write_text("")
            (package / "cli.py").write_text(
                "import example\ndef main():\n    print(example.answer())\n"
            )
            status, first = command(root, "--paths", "src/example.py", "-n", "2", "--seed", "7")
            other, second = command(root, "--paths", "src/example.py", "--seed", "41")
            self.assertEqual((status, other), (0, 0), (first, second))
            self.assertEqual(
                [(row.get("operator"), row["outcome"]) for row in first],
                [(row.get("operator"), row["outcome"]) for row in second],
            )
            self.assertEqual((root / "src/example.py").read_text(), source)

    def test_setup_collection_crash_and_skips_cannot_be_assertion_kills(self):
        programs = {
            "setup": (
                "import pytest\n@pytest.fixture(autouse=True)\ndef broken():\n"
                "    assert False\ndef test_x():\n    assert True\n"
            ),
            "collection": "raise AssertionError('collection')\n",
            "crash": "import os\ndef test_crash():\n    os._exit(9)\n",
            "skip": "import pytest\ndef test_skip():\n    pytest.skip('absent')\n",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project(root, "def answer():\n    return True\n")
            for name, program_text in programs.items():
                with self.subTest(name=name), snapshot(root) as scratch:
                    (scratch / "tests/test_example.py").write_text(program_text)
                    result, _ = run_domain(scratch, "unit", [], 20)
                    self.assertEqual(result["outcome"], "NO_TESTS" if name == "skip" else "INFRA")

    def test_mutant_crash_and_timeout_restore_scratch_and_never_edit_caller(self):
        cases = {
            "crash": ("import os\n    if not example.answer(): os._exit(8)", "INFRA", 20),
            "timeout": ("import time\n    if not example.answer(): time.sleep(60)", "TIMEOUT", 1),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = "def answer():\n    return True\n"
            for name, (body, expected, timeout) in cases.items():
                with self.subTest(name=name):
                    project(root, source, body + "\n    assert True")
                    mutants, _ = candidates("src/example.py", source.encode())
                    mutant = next(item for item in mutants if item.operator == "boolean:literal")
                    manifest = root / "mutations.json"
                    manifest.write_text(
                        json.dumps(
                            {
                                "root": str(root),
                                "sources": {"src/example.py": source},
                                "mutants": [
                                    {
                                        **{
                                            key: value
                                            for key, value in asdict(mutant).items()
                                            if key != "before"
                                        },
                                        "selected": {"unit": ["tests/test_example.py::test_value"]},
                                    }
                                ],
                                "timeout": timeout,
                            }
                        )
                    )
                    execute_job(manifest, 0)
                    row = json.loads((root / "result-0.json").read_text())
                    self.assertEqual(row["outcome"], expected, row)
                    self.assertTrue(row["restored"])
                    self.assertEqual((root / "src/example.py").read_text(), source)


class MutationCommandTest(unittest.TestCase):
    def test_an_unkilled_mutant_fails_with_source_and_unreached_fixture(self):
        # Given an executed return whose value neither domain checks, when the gate runs,
        # then it records the unreached fixture domain and fails closed.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "src/example.py": "def answer():\n    return True\n",
                "tests/test_example.py": (
                    "from example import answer\ndef test_call():\n"
                    "    assert answer() is not Ellipsis\n"
                ),
                "acceptance/selftests/test_harness.py": (
                    "def test_harness():\n    assert 2 + 2 == 4\n"
                ),
                "acceptance/tests/test_contract.py": (
                    "def test_contract():\n    assert 3 + 3 == 6\n"
                ),
                "acceptance/conftest.py": (
                    "def pytest_addoption(parser):\n    parser.addoption('--report')\n"
                ),
                "pyproject.toml": '[tool.pytest.ini_options]\npythonpath = ["src"]\n',
            }
            for name, text in files.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
            before = (root / "src/example.py").read_bytes()
            result = subprocess.run(  # noqa: S603 - fixed project command, owned fixture path
                ["pdm", "run", "mutation-gate", "--root", str(root), "--paths", "src/example.py"],  # noqa: S607
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            report = [json.loads(line) for line in result.stdout.splitlines()]
            survivors = [row for row in report if row.get("outcome") == "SURVIVED"]
            self.assertTrue(survivors, result.stdout)
            self.assertEqual(survivors[0]["file"], "src/example.py")
            self.assertEqual(survivors[0]["line"], 2)
            self.assertEqual(survivors[0]["before"], "True")
            self.assertEqual(survivors[0]["after"], "False")
            self.assertEqual(survivors[0]["confirmation"], "unreached")
            self.assertEqual((root / "src/example.py").read_bytes(), before)
