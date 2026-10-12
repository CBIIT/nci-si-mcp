"""The Local run workflow walks the documented setup as written, on both platforms."""

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
LOCAL_RUN = WORKFLOWS / "local-run.yml"

MACOS = "runner.os == 'macOS'"
LINUX = "runner.os == 'Linux'"

# Every step in order: its name and its condition.
STEPS = [
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


def load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def job():
    return load(LOCAL_RUN)["jobs"]["local-run"]


def steps():
    return [step for step in job()["steps"] if "name" in step]


def documented_commands(document):
    """Commands a document gives: lines of fenced blocks and inline code spans, comments removed."""

    text = (ROOT / document).read_text(encoding="utf-8")
    commands = set()
    for block in re.findall(r"^```(?:bash|sh|shell)\n(.*?)^```", text, re.DOTALL | re.MULTILINE):
        for line in block.splitlines():
            commands.add(re.sub(r"\s+#.*$", "", line).strip())
    for span in re.findall(r"`([^`\n]+)`", text):
        commands.add(span.replace("\\|", "|").strip())
    return commands


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
            {"fail-fast": False, "matrix": {"os": ["macos-latest", "ubuntu-latest"]}},
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

    def test_the_ci_only_list_has_no_stale_entry(self):
        lines = {line.strip() for step in steps() for line in step["run"].splitlines()}
        names = {step["name"] for step in steps()}
        self.assertEqual([key for key in CI_ONLY if key not in names | lines], [])

    def test_a_one_sided_edit_is_caught(self):
        step = next(s for s in steps() if s["name"] == "Install the project")
        self.assertEqual(undocumented_lines(step["name"], step["run"], DOCUMENTS), [])
        self.assertEqual(
            undocumented_lines(step["name"], "pdm install --no-lock", DOCUMENTS),
            ["pdm install --no-lock"],
        )
