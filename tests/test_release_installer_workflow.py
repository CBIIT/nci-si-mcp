import re
import unittest
from pathlib import Path

import yaml

# ruff: noqa: E501 - exact workflow command snapshot intentionally preserves literal lines

ROOT = Path(__file__).parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
DOCUMENT = ROOT / "docs" / "installer-targets.md"
JOB_NAME = "local installer (macOS/Linux)"
STDIO_CAPTURE = (
    'STDIO_CLIENT="$("$INSTALLER_VENV/bin/nci-si-install" start '
    '--target local --transport stdio --data-dir "$STDIO_ROOT")"'
)
CI_ONLY = {
    STDIO_CAPTURE: "captures the documented client record for the acceptance harness",
    '"$INSTALLER_VENV/bin/nci-si-install" stop --target local --transport http '
    '--data-dir "$HTTP_ROOT" || cleanup_status=$?': "cleans up HTTP after a failed check",
    '"$INSTALLER_VENV/bin/nci-si-install" uninstall --target local --transport http '
    '--data-dir "$HTTP_ROOT" || cleanup_status=$?': "cleans up HTTP after a failed check",
}
EXPECTED_JOB = yaml.safe_load(r"""
name: local installer (macOS/Linux) (${{ matrix.os }})
needs: release
if: needs.release.outputs.image_tag != ''
runs-on: ${{ matrix.os }}
timeout-minutes: 20
permissions: {contents: read}
strategy:
  fail-fast: false
  matrix: {os: [macos-latest, ubuntu-24.04]}
steps:
  - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
    with: {ref: '${{ needs.release.outputs.image_sha }}', fetch-depth: 0, persist-credentials: false}
  - name: Install Python
    id: python
    uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97
    with: {python-version: '3.14'}
  - name: Install the released installer
    working-directory: ${{ runner.temp }}
    env:
      RELEASE_TAG: ${{ needs.release.outputs.image_tag }}
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

    def test_job_runs_only_for_the_exact_release_with_read_only_permissions(self):
        value = job()
        self.assertEqual(value["name"], JOB_NAME + " (${{ matrix.os }})")
        self.assertEqual(value["needs"], "release")
        self.assertEqual(value["if"], "needs.release.outputs.image_tag != ''")
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
                    "ref": "${{ needs.release.outputs.image_sha }}",
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
