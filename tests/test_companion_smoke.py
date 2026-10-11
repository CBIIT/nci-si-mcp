"""Container verification waits for actual queue states and requires readable results."""

import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import companion_smoke, processes


class CompanionSmokeTest(unittest.TestCase):
    def test_serving_probe_explicitly_opts_into_local_auth(self):
        record = {
            "NetworkSettings": {
                "Ports": {"8000/tcp": [{"HostPort": "12345"}]},
                "Networks": {"owned-network": {"IPAddress": "10.0.0.2"}},
            }
        }
        commands = []

        def docker(*args):
            commands.append(args)
            return json.dumps([record])

        with patch.object(companion_smoke, "docker", docker):
            self.assertEqual(
                companion_smoke._serving_probe("owned-network", "owned-serving"),
                ("10.0.0.2", 12345),
            )
        command = next(args for args in commands if args[0] == "run")
        environment = dict(
            command[i + 1].split("=", 1) for i, arg in enumerate(command) if arg == "-e"
        )
        self.assertEqual(environment.get("NCI_SI_HTTP_AUTH_MODE"), "trusted-local")

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
            patch.object(processes.time, "sleep"),
            self.assertRaisesRegex(RuntimeError, "Benchmark results are unavailable"),
        ):
            companion_smoke.benchmark("owned-admin")
