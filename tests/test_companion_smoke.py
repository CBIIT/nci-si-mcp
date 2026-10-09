"""Container verification waits for actual queue states and requires readable results."""

import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import companion_smoke


class CompanionSmokeTest(unittest.TestCase):
    def test_cleanup_failure_is_reported_after_other_owned_resources_are_removed(self):
        resources = {"container": {"owned-serving"}, "network": {"owned-network"}}

        def docker(*args):
            if args[0] == "compose":
                raise subprocess.CalledProcessError(1, args)
            kind, action = args[:2]
            if action == "ls":
                return "\n".join(resources[kind])
            resources[kind].remove(args[-1])
            return ""

        with (
            patch.object(companion_smoke, "docker", side_effect=docker),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            companion_smoke.cleanup(("compose",), "owned-serving", "owned-network")
        self.assertEqual(resources, {"container": set(), "network": set()})

    def test_pending_job_is_polled_until_completed_and_results_are_required(self):
        run_id = "a" * 32
        history = [
            json.dumps({"jobs": [{"run_id": run_id, "state": state}]})
            for state in ("pending", "running", "completed")
        ]
        with (
            patch.object(companion_smoke.uuid, "uuid4", return_value=SimpleNamespace(hex=run_id)),
            patch.object(companion_smoke, "docker", side_effect=history),
            patch.object(companion_smoke, "request", side_effect=[(303, ""), (503, "Unavailable")]),
            patch.object(companion_smoke.time, "sleep"),
            self.assertRaisesRegex(RuntimeError, "Benchmark results are unavailable"),
        ):
            companion_smoke.benchmark("owned-admin")
