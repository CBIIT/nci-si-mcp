"""The project gates in scripts/validation report what they are meant to, and nothing else."""

import io
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import check_complexity
import check_test_quality


class GateTestCase(unittest.TestCase):
    def write(self, source, name="module.py"):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / name
        path.write_text(textwrap.dedent(source), encoding="utf-8")
        return path


class ComplexityGateTest(GateTestCase):
    BRANCHY = """
        def tangled(value):
            if value == 1: return 1
            if value == 2: return 2
            if value == 3: return 3
            if value == 4: return 4
            if value == 5: return 5
            if value == 6: return 6
            {last}
            return 0
        """

    def test_complexity_eight_is_reported_with_its_location(self):
        path = self.write(self.BRANCHY.format(last="if value == 7: return 7"))

        self.assertEqual(check_complexity.violations(path), [f"{path}:2: tangled has complexity 8"])

    def test_complexity_seven_passes(self):
        self.assertEqual(check_complexity.violations(self.write(self.BRANCHY.format(last=""))), [])

    def test_boolean_operators_and_comprehensions_count(self):
        path = self.write(
            """
            def dense(a, b, c, d, items):
                return [item for item in items if item and a or b and c or d and item > 1]
            """
        )

        self.assertEqual(check_complexity.violations(path), [f"{path}:2: dense has complexity 8"])

    def test_a_method_is_reported_under_its_class_and_the_class_is_not(self):
        source = self.BRANCHY.format(last="if value == 7: return 7").replace(
            "def tangled(", "def m(self, "
        )
        path = self.write("class Thing:\n" + textwrap.indent(textwrap.dedent(source), "    "))

        self.assertEqual(check_complexity.violations(path), [f"{path}:3: Thing.m has complexity 8"])

    def test_functions_and_classes_nested_in_others_are_checked(self):
        tangled = textwrap.dedent(self.BRANCHY.format(last="if value == 7: return 7"))
        method = textwrap.indent(tangled.replace("def tangled(", "def m(self, "), " " * 8)
        path = self.write(
            "def outer():" + textwrap.indent(tangled, "    ") + "\nclass A:\n    class B:" + method
        )

        names = sorted(line.split(": ")[1] for line in check_complexity.violations(path))

        self.assertEqual(names, ["A.B.m has complexity 8", "outer.tangled has complexity 8"])

    def test_exit_code_and_report(self):
        clean = self.write("def simple():\n    return 1\n", "clean.py")
        tangled = self.write(self.BRANCHY.format(last="if value == 7: return 7"))
        output = io.StringIO()

        with redirect_stdout(output):
            codes = (
                check_complexity.main([str(clean)]),
                check_complexity.main([str(tangled.parent)]),
            )

        self.assertEqual(codes, (0, 1))
        self.assertIn("tangled has complexity 8", output.getvalue())


class TestQualityGateTest(GateTestCase):
    def findings(self, body, docstring="Tests of the thing."):
        source = f'"""{docstring}"""\n\n\ndef test_it(self, mock=None):\n'
        path = self.write(source + textwrap.indent(textwrap.dedent(body), "    "), "test_it.py")
        return [finding.split(": ", 1)[1] for finding in check_test_quality.findings(path)]

    def test_assertions_on_behaviour_pass(self):
        for body in (
            "assert compute() == 2",
            "self.assertEqual(compute(), 2)",
            "with self.assertRaises(ValueError):\n    compute()",
            "self.fail('unreachable')",
            "assert_valid(compute())",
            "self.assertEqual(compute(), 2)\nmock.assert_called_once_with(1)",
        ):
            with self.subTest(body):
                self.assertEqual(self.findings(body), [])

    def test_a_test_without_an_assertion_is_rejected(self):
        self.assertEqual(self.findings("compute()"), ["test_it asserts nothing"])

    def test_a_test_that_only_inspects_a_mock_is_rejected(self):
        for body in (
            "mock.assert_called_once_with(1)",
            "mock.assert_not_called()",
            "mock.assert_has_calls([])",
            "mock.assert_any_await(1)",
            "self.assertEqual(mock.call_count, 2)",
            "assert mock.called",
            "self.assertEqual(mock.call_args.kwargs['timeout'], 2)",
        ):
            with self.subTest(body):
                self.assertEqual(len(self.findings(body)), 1)
                self.assertIn("only asserts how a mock was used", self.findings(body)[0])

    def test_a_call_that_is_not_an_assertion_does_not_count(self):
        for body in ("subprocess.check_output(['true'])", "failed = compute()"):
            with self.subTest(body):
                self.assertEqual(self.findings(body), ["test_it asserts nothing"])

    def test_an_assertion_in_a_nested_function_counts_only_when_the_function_is_used(self):
        nested = "def verify():\n    assert compute() == 2\n"

        self.assertEqual(self.findings(nested), ["test_it asserts nothing"])
        self.assertEqual(self.findings(nested + "verify()"), [])
        self.assertEqual(self.findings(nested + "run(verify)"), [])
        self.assertEqual(self.findings("run(lambda: self.assertEqual(compute(), 2))"), [])

    def test_a_test_that_only_checks_callability_is_rejected(self):
        self.assertEqual(
            self.findings("assert callable(compute)"),
            [
                "test_it only asserts that something is callable; "
                "assert the result or the effect as well"
            ],
        )

    def test_mock_and_callability_assertions_together_are_both_named(self):
        (finding,) = self.findings("assert callable(compute)\nmock.assert_called_once()")

        self.assertIn("that something is callable and how a mock was used", finding)

    def test_a_helper_named_like_a_mock_assertion_is_an_assertion(self):
        for body in ("assert_has_keys(compute(), 'a')", "self.assert_any_hit(compute())"):
            with self.subTest(body):
                self.assertEqual(self.findings(body), [])

    def test_a_coverage_aim_in_the_module_docstring_is_rejected(self):
        for docstring in (
            "Improve coverage of the index.",
            "Raises the coverage of index.py.",
            "Bring the coverage to 95%.",
        ):
            with self.subTest(docstring):
                self.assertEqual(
                    self.findings("assert compute() == 2", docstring),
                    ["the module docstring states a coverage aim"],
                )

    def test_a_percentage_in_the_module_docstring_is_not_a_coverage_aim(self):
        docstring = "Scores are normalized to 100% of the best hit."

        self.assertEqual(self.findings("assert compute() == 2", docstring), [])

    def test_helpers_are_not_tests(self):
        path = self.write("def helper():\n    return 1\n", "test_helpers.py")

        self.assertEqual(check_test_quality.findings(path), [])

    def test_exit_code_and_report(self):
        good = self.write("def test_a():\n    assert 1 + 1 == 2\n", "test_good.py")
        bad = self.write("def test_b():\n    pass\n", "test_bad.py")
        output = io.StringIO()

        with redirect_stdout(output):
            codes = (
                check_test_quality.main([str(good)]),
                check_test_quality.main([str(good), str(bad)]),
            )

        self.assertEqual(codes, (0, 1))
        self.assertIn(f"{bad}:1: test_b asserts nothing", output.getvalue())


if __name__ == "__main__":
    unittest.main()
