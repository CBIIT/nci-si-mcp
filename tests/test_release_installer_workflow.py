import re
import unittest
from pathlib import Path

import yaml

# ruff: noqa: E501 - exact workflow command snapshot intentionally preserves literal lines

ROOT = Path(__file__).parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "local-installer.yml"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
DOCUMENT = ROOT / "docs" / "installer-targets.md"
JOB_NAME = "local installer (macOS/Linux)"
STDIO_CAPTURE = (
    'STDIO_CLIENT="$("$INSTALLER_VENV/bin/nci-si-install" start '
    '--target local --transport stdio --data-dir "$STDIO_ROOT")"'
)
CI_ONLY = {
    'git tag "$INSTALL_TAG" "$INSTALL_REF"': "gives the PR head a synthetic v0.0.0 release identity for release-only checks",
    'git config --global url."file://$GITHUB_WORKSPACE/".insteadOf https://github.com/CBIIT/nci-si-mcp': "redirects the installer Git URL to the checked-out PR source",
    STDIO_CAPTURE: "captures the documented client record for the acceptance harness",
    '"$INSTALLER_VENV/bin/nci-si-install" stop --target local --transport http '
    '--data-dir "$HTTP_ROOT" || cleanup_status=$?': "cleans up HTTP after a failed check",
    '"$INSTALLER_VENV/bin/nci-si-install" uninstall --target local --transport http '
    '--data-dir "$HTTP_ROOT" || cleanup_status=$?': "cleans up HTTP after a failed check",
}
EXPECTED_JOB = yaml.safe_load(r"""
name: local installer (macOS/Linux) (${{ matrix.os }})
needs: prepare
runs-on: ${{ matrix.os }}
timeout-minutes: 20
permissions: {contents: read}
strategy:
  fail-fast: false
  matrix: {os: [macos-latest, ubuntu-24.04]}
steps:
  - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
    with: {ref: '${{ needs.prepare.outputs.ref }}', fetch-depth: 0, persist-credentials: false}
  - name: Install Python
    id: python
    uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97
    with: {python-version: '3.14'}
  - name: Prepare pull request source (CI only)
    if: github.event_name == 'pull_request'
    env:
      INSTALL_REF: ${{ needs.prepare.outputs.ref }}
      INSTALL_TAG: ${{ needs.prepare.outputs.tag }}
    run: |
      git tag "$INSTALL_TAG" "$INSTALL_REF"
      git config --global url."file://$GITHUB_WORKSPACE/".insteadOf https://github.com/CBIIT/nci-si-mcp
  - name: Install the released installer
    working-directory: ${{ runner.temp }}
    env:
      RELEASE_TAG: ${{ needs.prepare.outputs.tag }}
      INSTALLER_VENV: ${{ runner.temp }}/installer-venv
      PYTHON: ${{ steps.python.outputs.python-path }}
    run: |
      "$PYTHON" -m venv "$INSTALLER_VENV"
      "$INSTALLER_VENV/bin/python" -m pip install "nci-si-mcp[server] @ git+https://github.com/CBIIT/nci-si-mcp@${RELEASE_TAG}"
  - name: Install the release acceptance suite (CI only)
    env: {PYTHON: '${{ steps.python.outputs.python-path }}'}
    run: |
      "$PYTHON" -m venv "$RUNNER_TEMP/acceptance-venv"
      "$RUNNER_TEMP/acceptance-venv/bin/python" -m pip install ./acceptance
  - name: Verify stdio and HTTP installer lifecycles
    working-directory: ${{ runner.temp }}
    env:
      ACCEPTANCE_VENV: ${{ runner.temp }}/acceptance-venv
      INSTALLER_VENV: ${{ runner.temp }}/installer-venv
      STDIO_ROOT: ${{ runner.temp }}/stdio-install
      HTTP_ROOT: ${{ runner.temp }}/http-install
    run: |
      set -euo pipefail
      cleanup() {
        status=$?
        cleanup_status=0
        if [ -f "$HTTP_ROOT/installer-state.json" ]; then
          "$INSTALLER_VENV/bin/nci-si-install" stop --target local --transport http --data-dir "$HTTP_ROOT" || cleanup_status=$?
          "$INSTALLER_VENV/bin/nci-si-install" uninstall --target local --transport http --data-dir "$HTTP_ROOT" || cleanup_status=$?
        fi
        if [ "$status" -eq 0 ]; then status=$cleanup_status; fi
        exit "$status"
      }
      trap cleanup EXIT
      export REQUIRED_TESTS="test_a_resolved_release_is_never_cached test_an_index_mode_without_an_index_is_unavailable"
      mkdir -p "$STDIO_ROOT"
      touch "$STDIO_ROOT/unowned"
      "$INSTALLER_VENV/bin/nci-si-install" check --target local
      "$INSTALLER_VENV/bin/nci-si-install" run --target local --transport stdio --data-dir "$STDIO_ROOT" --non-interactive
      "$INSTALLER_VENV/bin/nci-si-install" status --target local --transport stdio --data-dir "$STDIO_ROOT"
      STDIO_CLIENT="$("$INSTALLER_VENV/bin/nci-si-install" start --target local --transport stdio --data-dir "$STDIO_ROOT")"
      STDIO_SERVER="$(printf '%s' "$STDIO_CLIENT" | "$ACCEPTANCE_VENV/bin/python" -c 'import json,shlex,sys; r=json.load(sys.stdin); print(shlex.join(["env", *(f"{k}={v}" for k,v in r["env"].items()), r["command"], *r["args"]]))')"
      NCI_SI_ACCEPTANCE_SERVER="$STDIO_SERVER" "$ACCEPTANCE_VENV/bin/pytest" "$GITHUB_WORKSPACE/acceptance/tests/test_evs.py::test_a_resolved_release_is_never_cached" "$GITHUB_WORKSPACE/acceptance/tests/test_evs.py::test_an_index_mode_without_an_index_is_unavailable" --report="$RUNNER_TEMP/stdio.json"
      "$INSTALLER_VENV/bin/nci-si-install" uninstall --target local --transport stdio --data-dir "$STDIO_ROOT"
      test -f "$STDIO_ROOT/unowned"
      test ! -e "$STDIO_ROOT/venv"
      test ! -e "$STDIO_ROOT/installer-state.json"
      mkdir -p "$HTTP_ROOT"
      touch "$HTTP_ROOT/unowned"
      "$INSTALLER_VENV/bin/nci-si-install" check --target local
      "$INSTALLER_VENV/bin/nci-si-install" run --target local --transport http --data-dir "$HTTP_ROOT" --non-interactive
      "$INSTALLER_VENV/bin/nci-si-install" status --target local --transport http --data-dir "$HTTP_ROOT"
      export NCI_SI_UPSTREAM_MODE=fixture
      export NCI_SI_EVS_BASE_URL=http://127.0.0.1:8766
      export NCI_SI_EVS_FHIR_BASE_URL=http://127.0.0.1:8766
      export NCI_SI_CADSR_BASE_URL=http://127.0.0.1:8766
      export NCI_SI_CADSR_FTP_URL=http://127.0.0.1:8766
      export NCI_SI_SSIS_SPARQL_URL=http://127.0.0.1:8766
      "$INSTALLER_VENV/bin/nci-si-install" start --target local --transport http --data-dir "$HTTP_ROOT"
      NCI_SI_ACCEPTANCE_URL=http://127.0.0.1:8000/mcp NCI_SI_ACCEPTANCE_FIXTURE_BIND=127.0.0.1:8766 NCI_SI_ACCEPTANCE_FIXTURE_URL=http://127.0.0.1:8766 "$ACCEPTANCE_VENV/bin/pytest" "$GITHUB_WORKSPACE/acceptance/tests/test_evs.py::test_a_resolved_release_is_never_cached" "$GITHUB_WORKSPACE/acceptance/tests/test_evs.py::test_an_index_mode_without_an_index_is_unavailable" --report="$RUNNER_TEMP/http.json"
      "$INSTALLER_VENV/bin/nci-si-install" stop --target local --transport http --data-dir "$HTTP_ROOT"
      "$INSTALLER_VENV/bin/nci-si-install" uninstall --target local --transport http --data-dir "$HTTP_ROOT"
      test -f "$HTTP_ROOT/unowned"
      test ! -e "$HTTP_ROOT/venv"
      test ! -e "$HTTP_ROOT/installer-state.json"
      "$ACCEPTANCE_VENV/bin/python" - <<'PY'
      import json, os
      from pathlib import Path
      expected = json.loads(Path(os.environ["GITHUB_WORKSPACE"] + "/acceptance/expected/fixture.json").read_text())
      names = os.environ["REQUIRED_TESTS"].split()
      required = {key for key in expected if any(name in key for name in names)}
      for name in ("stdio", "http"):
          report = json.loads(Path(os.environ["RUNNER_TEMP"] + f"/{name}.json").read_text())
          actual = {key: value["outcome"] for key, value in report["tests"].items()}
          assert set(actual) == required, (name, set(actual), required)
          assert actual == {key: expected[key] for key in actual}, (name, actual)
      PY
""")

EXPECTED_TRIGGERS = yaml.safe_load(r"""
workflow_call:
  inputs:
    ref: {required: true, type: string}
    tag: {required: true, type: string}
pull_request:
  paths:
    - src/nci_si_mcp/**
    - tests/test_installer*.py
    - tests/test_release_installer_workflow.py
    - acceptance/**
    - pyproject.toml
    - pdm.lock
    - .github/workflows/local-installer.yml
    - .github/workflows/release.yml
schedule:
  - cron: '23 7 * * 1'
""")
EXPECTED_PREPARE = yaml.safe_load(r"""
name: select source
runs-on: ubuntu-24.04
timeout-minutes: 5
outputs:
  ref: ${{ steps.source.outputs.ref }}
  tag: ${{ steps.source.outputs.tag }}
steps:
  - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
    with: {fetch-depth: 0, persist-credentials: false}
  - name: Select exact source
    id: source
    env:
      EVENT_NAME: ${{ github.event_name }}
      INPUT_REF: ${{ inputs.ref }}
      INPUT_TAG: ${{ inputs.tag }}
      PR_REF: ${{ github.event.pull_request.head.sha }}
      GH_TOKEN: ${{ github.token }}
    run: |
      set -euo pipefail
      if [ -n "$INPUT_REF" ]; then
        ref="$INPUT_REF"
        tag="$INPUT_TAG"
      elif [ "$EVENT_NAME" = "pull_request" ]; then
        ref="$PR_REF"
        tag="v0.0.0"
      else
        tag="$(gh release view --json tagName --jq .tagName)"
        ref="$(git rev-list -n 1 "$tag")"
      fi
      echo "ref=$ref" >> "$GITHUB_OUTPUT"
      echo "tag=$tag" >> "$GITHUB_OUTPUT"
""")


def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def job():
    return workflow()["jobs"]["local-installer"]


def named_steps():
    return [step for step in job()["steps"] if "name" in step]


def documented_commands():
    text = DOCUMENT.read_text(encoding="utf-8")
    blocks = re.findall(r"^```(?:bash|sh|shell)\n(.*?)^```", text, re.DOTALL | re.MULTILINE)
    return {line.strip() for block in blocks for line in block.splitlines() if line.strip()}


def undocumented_operator_lines(run, documented):
    lines = [line.strip() for line in run.splitlines() if "nci-si-install" in line]
    return [line for line in lines if line not in documented and line not in CI_ONLY]


class ReleaseInstallerWorkflowTest(unittest.TestCase):
    def test_the_whole_release_installer_job_is_exact(self):
        self.assertEqual(job(), EXPECTED_JOB)

    def test_source_selection_is_exact_for_call_pr_and_schedule(self):
        self.assertEqual(workflow()["jobs"]["prepare"], EXPECTED_PREPARE)

    def test_workflow_has_exact_entry_paths_concurrency_and_permissions(self):
        value = workflow()
        self.assertEqual(value[True], EXPECTED_TRIGGERS)
        self.assertEqual(value["permissions"], {"contents": "read"})
        self.assertEqual(
            value["concurrency"],
            {
                "group": "local-installer-${{ inputs.ref || github.event.pull_request.head.sha || github.ref }}",
                "cancel-in-progress": True,
            },
        )

    def test_release_workflow_calls_the_reusable_workflow_without_a_pr_trigger(self):
        release = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
        self.assertNotIn("pull_request", release[True])
        self.assertEqual(
            release["jobs"]["local-installer"],
            {
                "name": "local installer (macOS/Linux)",
                "needs": "release",
                "if": "needs.release.outputs.image_tag != ''",
                "uses": "$/.github/workflows/local-installer.yml",
                "with": {
                    "ref": "${{ needs.release.outputs.image_sha }}",
                    "tag": "${{ needs.release.outputs.image_tag }}",
                },
            },
        )

    def test_job_uses_prepared_source_with_read_only_permissions(self):
        value = job()
        self.assertEqual(value["name"], JOB_NAME + " (${{ matrix.os }})")
        self.assertEqual(value["needs"], "prepare")
        self.assertEqual(value["permissions"], {"contents": "read"})
        self.assertEqual(value["runs-on"], "${{ matrix.os }}")
        self.assertEqual(
            value["strategy"],
            {"fail-fast": False, "matrix": {"os": ["macos-latest", "ubuntu-24.04"]}},
        )
        self.assertEqual(value["timeout-minutes"], 20)

    def test_checkout_is_the_exact_release_without_credentials(self):
        checkout = job()["steps"][0]
        self.assertEqual(
            checkout,
            {
                "uses": "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
                "with": {
                    "ref": "${{ needs.prepare.outputs.ref }}",
                    "fetch-depth": 0,
                    "persist-credentials": False,
                },
            },
        )

    def test_python_is_the_sha_pinned_setup_action_on_both_platforms(self):
        setup = job()["steps"][1]
        self.assertEqual(
            setup,
            {
                "name": "Install Python",
                "id": "python",
                "uses": "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
                "with": {"python-version": "3.14"},
            },
        )
        steps = {step["name"]: step for step in named_steps()}
        self.assertEqual(
            steps["Install the released installer"]["env"]["PYTHON"],
            "${{ steps.python.outputs.python-path }}",
        )
        self.assertNotIn("curl", WORKFLOW.read_text(encoding="utf-8"))

    def test_installer_commands_run_outside_checkout_with_the_release_executable(self):
        steps = {step["name"]: step for step in named_steps()}
        install = steps["Install the released installer"]
        self.assertEqual(install["working-directory"], "${{ runner.temp }}")
        self.assertIn("@${RELEASE_TAG}", install["run"])
        self.assertIn('"$PYTHON" -m venv "$INSTALLER_VENV"', documented_commands())
        self.assertIn('"$PYTHON" -m venv "$INSTALLER_VENV"', install["run"])
        lifecycle = steps["Verify stdio and HTTP installer lifecycles"]
        self.assertEqual(lifecycle["working-directory"], "${{ runner.temp }}")
        lines = lifecycle["run"].splitlines()
        installer = '"$INSTALLER_VENV/bin/nci-si-install"'
        for command in ("check", "run", "status", "start", "stop", "uninstall"):
            self.assertTrue(any(line.startswith(f"{installer} {command} ") for line in lines))
        self.assertNotIn("pdm run nci-si-install", lifecycle["run"])

    def test_acceptance_targets_only_release_resolution_and_no_index_search(self):
        run = next(
            step["run"]
            for step in named_steps()
            if step["name"] == "Verify stdio and HTTP installer lifecycles"
        )
        self.assertIn("test_a_resolved_release_is_never_cached", run)
        self.assertIn("test_an_index_mode_without_an_index_is_unavailable", run)
        self.assertIn("NCI_SI_ACCEPTANCE_SERVER", run)
        self.assertIn("NCI_SI_ACCEPTANCE_URL", run)
        self.assertIn("acceptance/expected/fixture.json", run)
        self.assertIn(STDIO_CAPTURE, run)
        self.assertNotIn('NCI_SI_ACCEPTANCE_SERVER="$STDIO_ROOT/venv/bin/nci-si-mcp serve"', run)
        self.assertIn("REQUIRED_TESTS=", run)
        self.assertIn("assert set(actual) == required", run)
        self.assertIn("trap cleanup EXIT", run)
        self.assertIn('if [ "$status" -eq 0 ]; then status=$cleanup_status; fi', run)
        self.assertIn(
            "required = {key for key in expected if any(name in key for name in names)}", run
        )

    def test_uninstall_check_distinguishes_owned_and_unowned_paths(self):
        run = next(
            step["run"]
            for step in named_steps()
            if step["name"] == "Verify stdio and HTTP installer lifecycles"
        )
        self.assertIn('test -f "$STDIO_ROOT/unowned"', run)
        self.assertIn('test ! -e "$STDIO_ROOT/venv"', run)
        self.assertIn('test ! -e "$STDIO_ROOT/installer-state.json"', run)

    def test_every_operator_command_is_documented(self):
        documented = documented_commands()
        run = next(
            step["run"]
            for step in named_steps()
            if step["name"] == "Verify stdio and HTTP installer lifecycles"
        )
        self.assertEqual(undocumented_operator_lines(run, documented), [])
        self.assertIn(
            '"$INSTALLER_VENV/bin/nci-si-install" start --target local --transport stdio '
            '--data-dir "$STDIO_ROOT"',
            documented,
        )

    def test_lifecycle_script_keeps_the_exact_ordered_commands(self):
        run = next(
            step["run"]
            for step in named_steps()
            if step["name"] == "Verify stdio and HTTP installer lifecycles"
        )
        expected = [
            "run --target local --transport stdio",
            "status --target local --transport stdio",
            "start --target local --transport stdio",
            "uninstall --target local --transport stdio",
            "run --target local --transport http",
            "status --target local --transport http",
            "start --target local --transport http",
            "stop --target local --transport http",
            "uninstall --target local --transport http",
        ]
        success = run.partition('mkdir -p "$STDIO_ROOT"')[2]
        positions = [success.index(command) for command in expected]
        self.assertEqual(positions, sorted(positions))
