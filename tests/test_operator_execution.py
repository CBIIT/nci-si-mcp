"""Only an explicitly configured remote profile receives an upstream credential."""

import json
import shutil
import threading
import unittest
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.operator_evidence import finish_evidence
from scripts.operator_execution import ROOT, RemoteProbe, _perform, execute_job
from scripts.operator_source import SOURCE_PATHS
from scripts.portal_store import EvidenceStore


class OperatorExecutionTest(unittest.TestCase):
    def snapshot(self, _repository, _commit, destination):
        destination.mkdir()
        for name in SOURCE_PATHS:
            source = ROOT / name
            if source.is_dir():
                shutil.copytree(
                    source, destination / name, ignore=shutil.ignore_patterns("__pycache__")
                )
            else:
                shutil.copy2(source, destination / name)

    def test_fixture_pipeline_binds_real_http_measurements_to_original_inventory(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with patch("scripts.operator_execution.archive_source", side_effect=self.snapshot):
                code = _perform(directory, "a" * 40, "benchmark-http-fixture")
            self.assertEqual(code, 0)
            report = json.loads((directory / "bundle/report.json").read_text())
            self.assertTrue(report["complete"])
            job = {
                "run_id": "1" * 32,
                "commit": "a" * 40,
                "profile": "benchmark-http-fixture",
                "started_at": "2026-10-08T00:00:00+00:00",
            }
            store = EvidenceStore(directory / "store.sqlite")
            result = finish_evidence(
                job, directory, {"state": "completed", "exit_code": code, "reason": None}, store
            )
            self.assertEqual(result["state"], "completed")
            record = store.get(job["run_id"])
            self.assertEqual(record["evidence"]["state"], "completed")
            self.assertEqual(record["evidence"]["server_commit"], "a" * 40)

    def test_relative_workspace_is_resolved_before_child_working_directory_changes(self):
        observed = []

        def cancelled(_command, **options):
            observed.append(options["directory"])
            return {"state": "cancelled", "exit_code": None, "reason": "cancelled"}

        with TemporaryDirectory() as temporary, chdir(temporary):
            job = {"run_id": "1" * 32, "profile": "benchmark-http-fixture", "commit": "a" * 40}
            with patch("scripts.operator_execution.run_owned", side_effect=cancelled):
                execute_job(
                    job, Path("job"), threading.Event(), store=EvidenceStore(Path("store.sqlite"))
                )
        self.assertTrue(observed[0].is_absolute())

    def test_fixture_job_never_receives_the_configured_remote_credential(self):
        observed = []

        def cancelled(_command, **options):
            observed.append(options["environment"])
            return {"state": "cancelled", "exit_code": None, "reason": "cancelled"}

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = {"run_id": "1" * 32, "profile": "benchmark-http-fixture", "commit": "a" * 40}
            remote = RemoteProbe("https://approved.example/mcp", "Bearer PRIVATE-CANARY")
            with patch("scripts.operator_execution.run_owned", side_effect=cancelled):
                result = execute_job(
                    job,
                    root / "job",
                    threading.Event(),
                    store=EvidenceStore(root / "store.sqlite"),
                    remote=remote,
                )
            self.assertEqual(result["state"], "cancelled")
            self.assertNotIn("PRIVATE-CANARY", str(observed))
            self.assertNotIn("PRIVATE-CANARY", repr(remote))

    def test_remote_configuration_rejects_url_credentials_and_plaintext(self):
        for target in ("http://approved.example/mcp", "https://secret@approved.example/mcp"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                RemoteProbe(target, None)
