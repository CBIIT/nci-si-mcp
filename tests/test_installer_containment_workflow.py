"""The temporary pre-merge verifier of #434 runs the contained installer workflows only after
their exact containment tests pass, at the pull request's own revision."""

import unittest
from pathlib import Path

import yaml

# ruff: noqa: E501 - exact workflow snapshot intentionally preserves literal lines

ROOT = Path(__file__).parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "installer-containment.yml"

EXPECTED = yaml.safe_load(r"""
name: Installer containment
on:
  pull_request:
    paths:
      - .github/workflows/installer-containment.yml
      - .github/workflows/local-installer.yml
      - .github/workflows/local-run.yml
      - tests/test_installer_containment_workflow.py
      - tests/test_release_installer_workflow.py
      - tests/test_local_run_workflow.py
permissions:
  contents: read
concurrency:
  group: installer-containment-${{ github.event.pull_request.head.sha }}
  cancel-in-progress: true
jobs:
  contained:
    name: containment tests
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with: {fetch-depth: 0, persist-credentials: false}
      - uses: $/.github/actions/setup
      - name: Require the reviewed containment before either workflow runs
        run: pdm run pytest tests/test_installer_containment_workflow.py tests/test_release_installer_workflow.py tests/test_local_run_workflow.py
  local-installer:
    name: local installer
    needs: contained
    permissions:
      contents: read
    uses: $/.github/workflows/local-installer.yml
    with:
      ref: ''
      tag: ''
  local-run:
    name: local run
    needs: contained
    permissions:
      contents: read
    uses: $/.github/workflows/local-run.yml
""")


def load(path):
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class InstallerContainmentWorkflowTest(unittest.TestCase):
    def test_the_verifier_is_exactly_this_pull_request_only_workflow(self):
        # Exact: no other trigger, no write token or inherited secret, the guard job before both
        # callees, and both callees at this revision ($/), never a branch or tag reference. With
        # ref '' Local installer keeps its pull-request source selection, which
        # test_release_installer_workflow pins exactly.
        self.assertEqual(load(WORKFLOW), EXPECTED)
