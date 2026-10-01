import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from fakes import FakeEVS, concept

import nci_si_mcp
from nci_si_mcp.cli import build_parser, main
from nci_si_mcp.config import Settings
from nci_si_mcp.embeddings import HashingEmbeddingProvider
from nci_si_mcp.index import LocalIndex
from nci_si_mcp.service import NCISIService

NEOPLASM = concept(
    "C3262",
    "Neoplasm",
    synonyms=[{"name": "Tumor"}],
    children=[{"code": "C4741", "name": "Neoplasm by Morphology"}],
)


class ParserTest(unittest.TestCase):
    def test_traverse_options(self):
        args = build_parser().parse_args(
            ["traverse", "C3262", "--max-edges", "25", "--edge-type", "role", "--edge-type", "child"]
        )

        self.assertEqual(args.max_edges, 25)
        self.assertEqual(args.edge_types, ["role", "child"])
        self.assertEqual((args.direction, args.max_depth, args.max_nodes), ("out", 2, 200))
        self.assertEqual(build_parser().parse_args(["traverse", "C3262"]).max_edges, 1000)

    def test_unknown_choices_are_rejected_by_the_parser(self):
        for argv in (
            ["traverse", "C3262", "--edge-type", "sibling"],
            ["traverse", "C3262", "--direction", "sideways"],
            ["search", "tumor", "--mode", "fuzzy"],
        ):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                build_parser().parse_args(argv)


@patch("nci_si_mcp.cli.configure_logging")
class MainTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)

    def run_cli(self, *argv, service="fake", **environment):
        """Run `main` and return (exit code, parsed stdout, stderr text).

        `service` is "fake" for a real service over FakeEVS, "real" to let the
        CLI build its own, or an object to hand to the CLI as the service.
        """

        stdout, stderr = io.StringIO(), io.StringIO()
        environment.setdefault("NCI_SI_DATA_DIR", str(self.path))
        patches = [
            patch.object(sys, "argv", ["nci-si-mcp", *argv]),
            patch.dict(os.environ, environment, clear=True),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ]
        if service == "fake":
            service = NCISIService(
                Settings(data_dir=self.path),
                evs=FakeEVS([NEOPLASM]),
                index=LocalIndex(self.path),
                embedding_provider=HashingEmbeddingProvider(),
            )
        if service != "real":
            patches.append(patch("nci_si_mcp.cli.NCISIService", return_value=service))
        with ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            code = main()
        return code, json.loads(stdout.getvalue() or "null"), stderr.getvalue()

    def test_each_command_prints_its_result_and_exits_zero(self, _):
        code, manifest, _ = self.run_cli("index-sample", "C3262")
        self.assertEqual((code, manifest["concept_count"]), (0, 1))

        code, lookup, _ = self.run_cli("lookup", "C3262", "--include-raw")
        self.assertEqual((code, lookup["source"]), (0, "live_evs"))
        self.assertIn("raw", lookup)

        code, search, _ = self.run_cli("search", "tumor", "--mode", "bm25", "--limit", "1")
        self.assertEqual((code, search["hits"][0]["concept"]["code"]), (0, "C3262"))

        code, traversal, _ = self.run_cli("traverse", "C3262", "--max-depth", "1", "--no-roles")
        self.assertEqual((code, traversal["edges"][0]["target_code"]), (0, "C4741"))

        code, info, _ = self.run_cli("release-info")
        self.assertEqual((code, info["active_index"]["release_version"]), (0, "26.06e"))

        code, evaluation, _ = self.run_cli("evaluate")
        self.assertEqual((code, len(evaluation["results"])), (0, 3))

    def test_every_option_reaches_the_service(self, _):
        service = MagicMock(spec=NCISIService)
        for method in ("index_codes", "search", "lookup", "traverse"):
            getattr(service, method).return_value = {"ok": True}

        self.run_cli("index-sample", "C1", "C2", service=service)
        service.index_codes.assert_called_once_with(["C1", "C2"])

        self.run_cli(
            "search", "tumor", "--limit", "7", "--mode", "vector", "--include-raw", service=service
        )
        service.search.assert_called_once_with("tumor", limit=7, mode="vector", include_raw=True)

        self.run_cli("lookup", "C1", "--live-only", "--include-raw", service=service)
        service.lookup.assert_called_once_with("C1", live_only=True, include_raw=True)

        options = "--direction both --max-depth 3 --max-nodes 40 --max-edges 50 --no-hierarchy"
        options += " --no-roles --no-associations --relationship-name Has_Finding --edge-type role"
        self.run_cli("traverse", "C1", "C2", *options.split(), service=service)
        service.traverse.assert_called_once_with(
            start_codes=["C1", "C2"],
            direction="both",
            max_depth=3,
            max_nodes=40,
            max_edges=50,
            include_hierarchy=False,
            include_roles=False,
            include_associations=False,
            relationship_names=["Has_Finding"],
            edge_types=["role"],
        )

    def test_error_envelope_exits_one(self, _):
        for argv, error in (
            (["lookup", "C999"], "concept_not_found"),
            (["search", "tumor"], "no_active_index"),
            (["evaluate"], "no_active_index"),
            (["traverse", "C3262", "--max-depth", "-1"], "invalid_request"),
        ):
            with self.subTest(argv=argv):
                code, result, _ = self.run_cli(*argv)
                self.assertEqual((code, result["error"]), (1, error))

    def test_invalid_configuration_names_the_variable(self, _):
        for variable, value in (
            ("NCI_SI_TIMEOUT_SECONDS", "0"),
            ("NCI_SI_EMBEDDING_PROVIDER", "bogus"),
            ("NCI_SI_EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
        ):
            with self.subTest(variable):
                code, result, _ = self.run_cli("release-info", service="real", **{variable: value})
                self.assertEqual((code, result["error"]), (1, "invalid_configuration"))
                self.assertIn(variable, result["message"])

    def test_startup_failures_are_reported_not_raised(self, _):
        occupied = self.path / "occupied"
        occupied.write_text("not a directory")
        corrupt = self.path / "corrupt"
        corrupt.mkdir()
        (corrupt / "nci_si.sqlite3").write_text("not a database" * 100)
        for label, data_dir in {"data dir is a file": occupied, "corrupt database": corrupt}.items():
            with self.subTest(label):
                code, result, _ = self.run_cli(
                    "search", "tumor", service="real", NCI_SI_DATA_DIR=str(data_dir)
                )
                self.assertEqual((code, result["error"]), (1, "startup_failed"))
                self.assertIn(str(data_dir), result["message"])

    def test_value_error_at_startup_is_reported(self, _):
        with patch("nci_si_mcp.cli.NCISIService", side_effect=ValueError("bad model name")):
            code, result, _ = self.run_cli("release-info", service="real")

        self.assertEqual((code, result["error"]), (1, "startup_failed"))

    def test_serve_runs_the_server_and_keeps_stdout_for_the_protocol(self, _):
        server = MagicMock()
        with patch("nci_si_mcp.cli.create_mcp", return_value=server) as create:
            code, printed, _ = self.run_cli("serve")

        self.assertEqual((code, printed), (0, None))
        server.run.assert_called_once_with()
        self.assertIsInstance(create.call_args.kwargs["service"], NCISIService)

    def test_serve_failures_go_to_stderr(self, _):
        with patch("nci_si_mcp.cli.create_mcp", side_effect=RuntimeError("mcp is missing")):
            code, printed, stderr = self.run_cli("serve")

        self.assertEqual((code, printed), (1, None))
        self.assertEqual(json.loads(stderr)["error"], "startup_failed")
        self.assertIn("mcp is missing", stderr)

        code, printed, stderr = self.run_cli("serve", NCI_SI_TIMEOUT_SECONDS="0")

        self.assertEqual((code, printed), (1, None))
        self.assertEqual(json.loads(stderr)["error"], "invalid_configuration")


class ProcessTest(unittest.TestCase):
    def test_results_go_to_stdout_and_diagnostics_to_stderr(self):
        with tempfile.TemporaryDirectory() as data_dir:
            # The developer's own NCI_SI_* settings must not reach the subprocess.
            inherited = {key: value for key, value in os.environ.items() if not key.startswith("NCI_SI_")}
            process = subprocess.run(
                [sys.executable, "-m", "nci_si_mcp.cli", "lookup", "oops"],
                capture_output=True,
                text=True,
                timeout=60,
                env={
                    **inherited,
                    "PYTHONPATH": str(Path(nci_si_mcp.__file__).parents[1]),
                    "NCI_SI_DATA_DIR": data_dir,
                },
            )

        self.assertEqual(process.returncode, 1)
        self.assertEqual(json.loads(process.stdout)["error"], "invalid_request")
        self.assertIn("lookup_failed error=invalid_request", process.stderr)
        self.assertIn("WARNING", process.stderr)


if __name__ == "__main__":
    unittest.main()
