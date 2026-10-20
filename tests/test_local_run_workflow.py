"""The Local run workflow walks the documented setup as written, on both platforms."""

import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

ROOT = Path(__file__).parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
LOCAL_RUN = WORKFLOWS / "local-run.yml"

MACOS = "runner.os == 'macOS'"
LINUX = "runner.os == 'Linux'"

# Every step in order: its name and its condition.
STEPS = [
    ("Remove the runner's python.org Python (macOS)", MACOS),
    ("Remove preinstalled Python and PDM (macOS)", MACOS),
    ("Install Python and PDM (macOS)", MACOS),
    ("Install uv (Linux)", LINUX),
    ("Open a new shell (Linux)", LINUX),
    ("Install Python and PDM (Linux)", LINUX),
    ("Install the project", None),
    ("Check that the documented Python and PDM are in use (macOS)", MACOS),
    ("Check that the documented Python and PDM are in use (Linux)", LINUX),
    ("Print the EVS release", None),
    ("Build a small local index", None),
    ("Write the readiness probe (CI only)", None),
    ("Serve over stdio and initialize (CI only)", None),
    ("Serve over streamable HTTP until ready (CI only)", None),
    ("Run the acceptance suite against the fixtures", None),
    ("Compare the outcomes with the expected ones", None),
    ("Remove a preinstalled Node.js (macOS)", MACOS),
    ("Install Node.js (macOS)", MACOS),
    ("Install nvm and Node.js (Linux)", LINUX),
    ("Check that the documented Node.js is in use", None),
    ("Build the documentation site", None),
    ("Serve the validation dashboard until ready (CI only)", None),
]

# The document that tells a developer to run each step's commands.
DOCUMENTS = {
    "Install Python and PDM (macOS)": "QUICKSTART.md",
    "Install uv (Linux)": "QUICKSTART.md",
    "Install Python and PDM (Linux)": "QUICKSTART.md",
    "Install the project": "QUICKSTART.md",
    "Print the EVS release": "QUICKSTART.md",
    "Build a small local index": "QUICKSTART.md",
    "Run the acceptance suite against the fixtures": "AGENTS.md",
    "Compare the outcomes with the expected ones": "acceptance/README.md",
    "Install Node.js (macOS)": "QUICKSTART.md",
    "Install nvm and Node.js (Linux)": "QUICKSTART.md",
    "Build the documentation site": "docs/documentation-site.md",
}

# What exists only in CI, by step name (the whole step) or by exact line, and why.
CI_ONLY = {
    "Remove the runner's python.org Python (macOS)": "the runner image ships python.org builds "
    "in /Library/Frameworks that a clean Mac lacks, and PDM picks them over Homebrew's",
    "Remove preinstalled Python and PDM (macOS)": "brew install exits 0 without installing a "
    "formula the runner image ships, so the documented install must really run",
    "Remove a preinstalled Node.js (macOS)": "the same, for Node.js",
    "Open a new shell (Linux)": "the documents say 'in a new shell'; the next step sees the path",
    "Check that the documented Python and PDM are in use (macOS)": "fails the job when a copy "
    "that the runner image ships takes over from the Homebrew install",
    "Check that the documented Python and PDM are in use (Linux)": "fails the job when a copy "
    "that the runner image ships takes over from the uv install",
    "Write the readiness probe (CI only)": "the helper that starts a documented server, waits "
    "for its readiness endpoint and stops it",
    "Serve over stdio and initialize (CI only)": "an initialize round-trip that no one types",
    "Serve over streamable HTTP until ready (CI only)": "the documented serve command, run by "
    "the probe: a foreground server would never end the step",
    "Check that the documented Node.js is in use": "fails the job when a Node.js that the "
    "runner image ships takes over from the documented install",
    "Serve the validation dashboard until ready (CI only)": "the documented portal serve "
    "command, run by the probe, for the same reason",
    '. "$HOME/.nvm/nvm.sh"': "the documents say 'in a new shell', where nvm is loaded",
    'dirname "$(command -v node)" >> "$GITHUB_PATH"': "puts the nvm Node.js on the "
    "path of the job's next steps, as a new shell would",
    "status=$?": "keeps the fixture run's exit status, which the next line judges",
    '[ "$status" -le 1 ] && [ -s acceptance/fixture.json ] || { echo "::error::The fixture run '
    'did not finish (exit status $status)."; exit 1; }': "exit status 1 means some tests did "
    "not pass, which the comparison judges; any other status, or no report, is no run",
}

# The CI-only probe steps run a documented server command; the document that gives the command,
# the command, and the readiness URL the probe waits for (None: an initialize over stdio).
PROBES = {
    "Serve over stdio and initialize (CI only)": (
        "QUICKSTART.md",
        "pdm run nci-si-mcp serve",
        None,
    ),
    "Serve over streamable HTTP until ready (CI only)": (
        "QUICKSTART.md",
        "pdm run nci-si-mcp serve --transport streamable-http",
        "http://127.0.0.1:8000/ready",
    ),
    "Serve the validation dashboard until ready (CI only)": (
        "docs/local-validation.md",
        "pdm run portal serve",
        "http://127.0.0.1:8081/health",
    ),
}

# The exact run text of the step that writes the readiness probe. It answers ready only while the
# server it started is running, stops that server, and fails unless it ended by its own exit or
# the SIGTERM sent to it (directly, or as `pdm run` reports it).
READY_PROBE_RUN = r"""cat > "$RUNNER_TEMP/ready.py" <<'PY'
import os, signal, subprocess, sys, time, urllib.request

url, command = sys.argv[1], sys.argv[2:]
server = subprocess.Popen(command, start_new_session=True)
try:
    deadline = time.monotonic() + 90
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5) as reply:
                if server.poll() is None:
                    print(url, reply.status, reply.read().decode())
                    break
        except OSError:
            pass
        if server.poll() is not None or time.monotonic() > deadline:
            sys.exit("not ready: " + url)
        time.sleep(1)
finally:
    try:
        os.killpg(server.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    status = server.wait(timeout=30)
# pdm run reports a child that the SIGTERM ended as 256 - 15.
if status not in (0, -signal.SIGTERM, 256 - signal.SIGTERM):
    sys.exit(f"the server stopped with status {status}")
PY
"""

# The CI-only steps that remove a copy the runner image ships, and the formulae each removes.
REMOVALS = {
    "Remove preinstalled Python and PDM (macOS)": ["python@3.14", "pdm"],
    "Remove a preinstalled Node.js (macOS)": ["node"],
}

# The exact run text of each in-use check: a check that cannot fail would let a copy the runner
# image ships take over unnoticed.
IN_USE_PYTHON_MACOS = r"""fail() { echo "::error::$1"; exit 1; }
[ "$(command -v pdm)" = "$(brew --prefix)/bin/pdm" ] || fail "pdm is $(command -v pdm), not the Homebrew install"
version="$(pdm run python -c "import sys; print('%d.%d' % sys.version_info[:2])" | tail -n 1)"
[ "$version" = "3.14" ] || fail "the project runs Python $version, not 3.14"
base="$(pdm run python -c "import sys; print(sys.base_prefix)" | tail -n 1)"
formula="$(brew --prefix python@3.14)"
case "$base" in "$formula"/*) ;; *) fail "the project's Python is $base, not Homebrew's python@3.14 ($formula)" ;; esac
"""  # noqa: E501

IN_USE_PYTHON_LINUX = r"""fail() { echo "::error::$1"; exit 1; }
[ "$(command -v pdm)" = "$HOME/.local/bin/pdm" ] || fail "pdm is $(command -v pdm), not the uv tool install"
version="$(pdm run python -c "import sys; print('%d.%d' % sys.version_info[:2])" | tail -n 1)"
[ "$version" = "3.14" ] || fail "the project runs Python $version, not 3.14"
base="$(pdm run python -c "import sys; print(sys.base_prefix)" | tail -n 1)"
managed="$(uv python dir)"
case "$base" in "$managed"/*) ;; *) fail "the project's Python is $base, not a uv-managed one under $managed" ;; esac
"""  # noqa: E501

IN_USE_NODE = r"""fail() { echo "::error::$1"; exit 1; }
case "$RUNNER_OS" in
  macOS) expected="$(brew --prefix)/bin/node" ;;
  *) expected="$HOME/.nvm/versions/node/*/bin/node" ;;
esac
# shellcheck disable=SC2254 # the pattern is meant to expand
case "$(command -v node)" in $expected) ;; *) fail "node is $(command -v node), not the documented install ($expected)" ;; esac
node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 24 ? 0 : 1)' || fail "Node.js $(node --version) is older than 24"
"""  # noqa: E501

IN_USE_CHECKS = {
    "Check that the documented Python and PDM are in use (macOS)": IN_USE_PYTHON_MACOS,
    "Check that the documented Python and PDM are in use (Linux)": IN_USE_PYTHON_LINUX,
    "Check that the documented Node.js is in use": IN_USE_NODE,
}

# The CI-only probe steps each end after this long, however the server behaves.
PROBE_TIMEOUT_MINUTES = 3

TRIGGERS = {
    "push": {"branches": ["main"]},
    "schedule": [{"cron": "17 7 * * 1"}],
    "workflow_dispatch": None,
    "pull_request": {
        "paths": [
            "pyproject.toml",
            "pdm.lock",
            "docs/site-assets/package.json",
            "docs/site-assets/package-lock.json",
            "README.md",
            "QUICKSTART.md",
            "CONTRIBUTING.md",
            "acceptance/README.md",
            "docs/documentation-site.md",
            "docs/local-validation.md",
            "scripts/portal*.py",
            "scripts/operator_*.py",
            "scripts/docs_site.py",
            ".github/workflows/local-run.yml",
        ]
    },
}


STDIO_INITIALIZE_RUN = r"""python3 - <<'PY'
import json, subprocess

request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {},
    "clientInfo": {"name": "local-run", "version": "0"}}}
server = subprocess.Popen(["pdm", "run", "nci-si-mcp", "serve"], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, text=True)
try:
    server.stdin.write(json.dumps(request) + "\n")
    server.stdin.flush()
    reply = json.loads(server.stdout.readline())
    assert reply["id"] == 1 and "serverInfo" in reply["result"], reply
    print("initialize answered by", reply["result"]["serverInfo"])
finally:
    server.kill()
    server.wait(timeout=30)
PY
"""


def probe_run(command, url):
    """The run text a probe step must have: the documented command as the probe starts it."""

    if url is None:
        return '["' + '", "'.join(command.split()) + '"]'
    return f'python3 "$RUNNER_TEMP/ready.py" {url} {command}'


def load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def job():
    return load(LOCAL_RUN)["jobs"]["local-run"]


def steps():
    return [step for step in job()["steps"] if "name" in step]


def documented_commands(document):
    """Commands a document gives: fenced block lines and table-row code spans, comments removed."""

    text = (ROOT / document).read_text(encoding="utf-8")
    commands = {}  # ordered: a step's lines must keep the document's order
    for block in re.findall(r"^```(?:bash|sh|shell)\n(.*?)^```", text, re.DOTALL | re.MULTILINE):
        for line in block.splitlines():
            commands.setdefault(re.sub(r"\s+#.*$", "", line).strip())
    # Inline spans count only in table rows (QUICKSTART's prerequisites table), never in prose.
    for row in re.findall(r"^\|.*$", text, re.MULTILINE):
        for span in re.findall(r"`([^`\n]+)`", row):
            commands.setdefault(span.replace("\\|", "|").strip())
    return list(commands)


def command_lines(run):
    lines = (line.strip() for line in run.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def undocumented_lines(step_name, run, documents):
    """The step's command lines that neither a document gives nor the CI-only list explains."""

    if step_name in CI_ONLY:
        return []
    commands = documented_commands(documents[step_name])
    return [line for line in command_lines(run) if line not in commands and line not in CI_ONLY]


class LocalRunWorkflowTest(unittest.TestCase):
    def test_the_workflow_exists_beside_the_others(self):
        self.assertEqual(
            sorted(path.name for path in WORKFLOWS.glob("*.yml")),
            [
                "acceptance-live.yml",
                "audit.yml",
                "branch-cleanup.yml",
                "ci.yml",
                "local-installer.yml",
                "local-run.yml",
                "pr-title.yml",
                "release.yml",
            ],
        )

    def test_it_runs_only_when_intended(self):
        self.assertEqual(load(LOCAL_RUN)[True], TRIGGERS)

    def test_it_has_least_privilege_and_no_secret(self):
        self.assertEqual(load(LOCAL_RUN)["permissions"], {"contents": "read"})
        self.assertNotIn("secrets.", LOCAL_RUN.read_text(encoding="utf-8"))

    def test_it_runs_on_exactly_the_two_platforms_with_a_bound(self):
        self.assertEqual(job()["runs-on"], "${{ matrix.os }}")
        self.assertEqual(
            job()["strategy"],
            {"fail-fast": False, "matrix": {"os": ["macos-latest", "ubuntu-24.04"]}},
        )
        self.assertEqual(job()["timeout-minutes"], 40)

    def test_checkout_keeps_tags_and_no_credentials(self):
        checkouts = [s for s in job()["steps"] if s.get("uses", "").startswith("actions/checkout@")]
        self.assertEqual(len(checkouts), 1)
        self.assertEqual(checkouts[0]["with"], {"fetch-depth": 0, "persist-credentials": False})

    def test_it_installs_by_the_documented_route_not_a_setup_action(self):
        self.assertEqual(
            [s["uses"] for s in job()["steps"] if "uses" in s],
            ["actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"],
        )

    def test_the_steps_are_exactly_these_in_order_each_on_its_platform(self):
        self.assertEqual([(s["name"], s.get("if")) for s in steps()], STEPS)

    def test_every_command_is_copied_from_the_document_it_walks(self):
        for step in steps():
            with self.subTest(step=step["name"]):
                self.assertEqual(undocumented_lines(step["name"], step["run"], DOCUMENTS), [])

    def test_every_step_keeps_the_order_of_its_document(self):
        for step in steps():
            if step["name"] in DOCUMENTS and step["name"] not in CI_ONLY:
                commands = documented_commands(DOCUMENTS[step["name"]])
                found = [
                    commands.index(line) for line in command_lines(step["run"]) if line in commands
                ]
                with self.subTest(step=step["name"]):
                    self.assertEqual(found, sorted(found))

    def test_the_stdio_initialize_round_trip_is_exactly_this(self):
        by_name = {step["name"]: step["run"] for step in steps()}
        self.assertEqual(by_name["Serve over stdio and initialize (CI only)"], STDIO_INITIALIZE_RUN)

    def test_the_ci_only_list_has_no_stale_entry(self):
        lines = {line.strip() for step in steps() for line in step["run"].splitlines()}
        names = {step["name"] for step in steps()}
        self.assertEqual([key for key in CI_ONLY if key not in names | lines], [])

    def test_the_probe_steps_run_the_documented_server_commands(self):
        by_name = {step["name"]: step["run"] for step in steps()}
        for name, (document, command, url) in PROBES.items():
            with self.subTest(step=name):
                self.assertIn(command, documented_commands(document))
                self.assertIn(probe_run(command, url), by_name[name])
                if url is not None:
                    self.assertEqual(by_name[name].strip(), probe_run(command, url))

    def test_the_in_use_checks_are_exactly_these(self):
        by_name = {step["name"]: step["run"] for step in steps()}
        for name, run in IN_USE_CHECKS.items():
            with self.subTest(step=name):
                self.assertEqual(by_name[name], run)

    def test_every_probe_step_has_the_same_short_bound(self):
        by_name = {step["name"]: step for step in steps()}
        for name in PROBES:
            with self.subTest(step=name):
                self.assertEqual(by_name[name]["timeout-minutes"], PROBE_TIMEOUT_MINUTES)

    def test_the_readiness_probe_is_exactly_this(self):
        by_name = {step["name"]: step["run"] for step in steps()}
        self.assertEqual(by_name["Write the readiness probe (CI only)"], READY_PROBE_RUN)

    def test_the_runner_loses_its_python_org_builds_before_the_documented_install(self):
        by_name = {step["name"]: step["run"] for step in steps()}
        self.assertEqual(
            by_name.get("Remove the runner's python.org Python (macOS)", "").splitlines(),
            [
                "sudo find /usr/local/bin -lname '*Library/Frameworks/Python.framework/*' -delete",
                "sudo rm -rf /Library/Frameworks/Python.framework",
            ],
        )

    def test_a_failed_removal_of_a_preinstalled_formula_fails_the_job(self):
        by_name = {step["name"]: step["run"] for step in steps()}
        for name, formulae in REMOVALS.items():
            with self.subTest(step=name):
                self.assertEqual(
                    by_name.get(name, "").splitlines(),
                    [
                        f"if brew list --versions {formula} >/dev/null; then "
                        f"brew uninstall --ignore-dependencies {formula}; fi"
                        for formula in formulae
                    ],
                )

    def test_nothing_changes_a_step_beside_its_pinned_keys(self):
        # An env, defaults, working-directory or continue-on-error would change what a documented
        # line does, or keep a failed check from failing the job, with every run line unchanged.
        workflow = load(LOCAL_RUN)
        self.assertEqual(set(workflow), {"name", True, "permissions", "concurrency", "jobs"})
        self.assertEqual(set(job()), {"name", "runs-on", "strategy", "timeout-minutes", "steps"})
        allowed = {"name", "if", "uses", "with", "shell", "timeout-minutes", "run"}
        extra = {
            step.get("name", step.get("uses")): sorted(set(step) - allowed)
            for step in job()["steps"]
            if set(step) - allowed
        }
        self.assertEqual(extra, {})

    def test_only_the_fixture_run_survives_a_failing_command_to_judge_its_status(self):
        # Under the default `bash -e`, exit status 1 would end the step before the guard and the
        # comparison that judge it.
        shells = {step["name"]: step["shell"] for step in steps() if "shell" in step}
        self.assertEqual(shells, {"Run the acceptance suite against the fixtures": "bash {0}"})

    def test_a_newer_push_replaces_only_a_pull_request_run(self):
        self.assertEqual(
            load(LOCAL_RUN)["concurrency"],
            {
                "group": "local-run-"
                "${{ github.event_name == 'pull_request' && github.ref || github.sha }}",
                "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
            },
        )

    def test_a_command_in_prose_is_not_a_documented_command(self):
        with TemporaryDirectory() as directory:
            document = Path(directory) / "document.md"
            document.write_text(
                "Run `in prose` first.\n\n| Tool | Check |\n|---|---|\n| Node | `in a table` |\n",
                encoding="utf-8",
            )
            self.assertEqual(documented_commands(str(document)), ["in a table"])

    def test_a_one_sided_edit_is_caught(self):
        step = next(s for s in steps() if s["name"] == "Install the project")
        self.assertEqual(undocumented_lines(step["name"], step["run"], DOCUMENTS), [])
        self.assertEqual(
            undocumented_lines(step["name"], "pdm install --no-lock", DOCUMENTS),
            ["pdm install --no-lock"],
        )
