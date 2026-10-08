"""Local composition separates public docs, bounded fixture workers and serving assets."""

import tomllib
import unittest
from pathlib import Path

import yaml
from scripts.companion_lock import render


class CompanionCompositionTest(unittest.TestCase):
    def setUp(self):
        self.compose = yaml.safe_load(Path("container/compose.local.yaml").read_text())

    def test_local_services_publish_only_loopback_and_have_resource_boundaries(self):
        for service in self.compose["services"].values():
            with self.subTest(service=service["image"]):
                self.assertTrue(
                    all(port.startswith("127.0.0.1:") for port in service.get("ports", []))
                )
                self.assertTrue(service["read_only"])
                self.assertEqual(service["user"], "65532:65532")
                self.assertEqual(service["cap_drop"], ["ALL"])
                self.assertGreater(service["pids_limit"], 0)
                self.assertIn("mem_limit", service)
                self.assertGreater(service["cpus"], 0)

    def test_fixture_workers_have_no_serving_network_assets_or_credentials(self):
        services = self.compose["services"]
        admin = services["administration"]
        self.assertEqual(admin["networks"], ["validation"])
        self.assertTrue(self.compose["networks"]["validation"]["internal"])
        self.assertEqual(admin["volumes"], ["evidence:/state"])
        self.assertNotIn("environment", admin)
        self.assertNotIn("ports", admin)
        self.assertEqual(services["admin-ingress"]["networks"], ["access", "validation"])
        self.assertNotIn("volumes", services["admin-ingress"])
        self.assertTrue(set(admin["networks"]).isdisjoint(services["mcp"]["networks"]))
        self.assertNotIn("volumes", services["documentation"])

    def test_committed_companion_dependencies_match_pdm_and_exclude_models(self):
        expected = render(tomllib.loads(Path("pdm.lock").read_text()))
        self.assertEqual(Path("container/companion-requirements.txt").read_text(), expected)
        self.assertNotIn("torch==", expected)
        self.assertNotIn("sentence-transformers==", expected)
        self.assertIn("numpy==", expected)
        self.assertIn("mcp==", expected)
