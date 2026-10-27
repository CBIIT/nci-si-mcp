"""The mutation command measures assertion sensitivity without editing its caller."""

import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

import yaml
from scripts.validation.mutation_gate import execute_job
from scripts.validation.mutation_run import run_domain, selection, snapshot
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
    result = subprocess.run(  # noqa: S603 - fixed PDM command, owned paths
        ["pdm", "run", "mutation-gate", "--root", str(root), *args],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        env={key: value for key, value in os.environ.items() if not key.startswith("COVERAGE")},
    )
    return result.returncode, [json.loads(line) for line in result.stdout.splitlines()]


class MutationOperatorsTest(unittest.TestCase):
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
                        },
                        "run": 'pdm run mutation-gate --base "$BASE_SHA" --head "$HEAD_SHA" -n 2',
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

    def test_contexts_are_domain_qualified_and_setup_or_child_contexts_use_whole_domain(self):
        mapping = {
            "unit": {"src/a.py": {2: ["tests/test_a.py::test_a|run"]}},
            "selftest": {"src/a.py": {2: ["tests/test_a.py::test_a|setup", ""]}},
        }
        self.assertEqual(
            selection(mapping, "src/a.py", 2, 2),
            {"unit": ["tests/test_a.py::test_a"], "selftest": []},
        )
        self.assertEqual(selection(mapping, "src/a.py", 9, 9), {})


class MutationExecutionTest(unittest.TestCase):
    def test_uncovered_sites_fail_no_tests_and_reviewed_survivors_still_get_fixture_confirmation(
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
            self.assertEqual({row["confirmation"] for row in allowed}, {"fixture"})

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
            "runtime": "def test_runtime():\n    raise RuntimeError('AssertionError is text')\n",
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
                                "mutants": [asdict(mutant)],
                                "timeout": timeout,
                                "mapping": {
                                    "unit": {
                                        "src/example.py": {
                                            2: ["tests/test_example.py::test_value|run"]
                                        }
                                    }
                                },
                            }
                        )
                    )
                    execute_job(manifest, 0)
                    row = json.loads((root / "result-0.json").read_text())
                    self.assertEqual(row["outcome"], expected, row)
                    self.assertTrue(row["restored"])
                    self.assertEqual((root / "src/example.py").read_text(), source)


class MutationCommandTest(unittest.TestCase):
    def test_an_unkilled_mutant_fails_with_source_and_fixture_confirmation(self):
        # Given an executed return whose value neither domain checks, when the gate runs,
        # then it reports the survivor after trying the fixture owner, and fails closed.
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
                env={
                    key: value
                    for key, value in os.environ.items()
                    if not key.startswith("COVERAGE")
                },
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
            self.assertEqual(survivors[0]["confirmation"], "fixture")
            self.assertEqual((root / "src/example.py").read_bytes(), before)
