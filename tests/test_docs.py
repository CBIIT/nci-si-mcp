"""The documentation names what the code defines: settings, error codes and modules.

The tools and resources in QUICKSTART.md are compared with the running server in test_server.
"""

import asyncio
import importlib
import json
import logging
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import get_args
from unittest.mock import patch

import yaml
from mcp.client import Client

from nci_si_mcp.cli import build_parser
from nci_si_mcp.config import Settings
from nci_si_mcp.errors import ErrorCode
from nci_si_mcp.evs import LICENSE_KEY_HEADER
from nci_si_mcp.http_client import MAX_RETRY_DELAY_SECONDS
from nci_si_mcp.server import create_mcp
from nci_si_mcp.traversal import NCIT_EXCLUSION_CODES
from nci_si_mcp.validation import PROFILES, RELEASE_CHANNELS, UPSTREAM_MODES

ROOT = Path(__file__).parent.parent
PACKAGE = ROOT / "src" / "nci_si_mcp"
QUICKSTART = (ROOT / "QUICKSTART.md").read_text(encoding="utf-8")
ARCHITECTURE = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")


def section(document, heading):
    """The text of a second-level section, without its heading."""

    return document.split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]


def bullet_names(text):
    """The name in backticks that starts each bullet of a Markdown list."""

    return re.findall(r"^- `([^`]+)`", text, flags=re.MULTILINE)


def first_column(table):
    """The names in backticks in the first column of a Markdown table."""

    rows = [row.split("|")[1] for row in table.splitlines() if re.match(r"\|\s*`", row)]
    return {name for row in rows for name in re.findall(r"`([^`]+)`", row)}


# Stands in for a result until it is captured from a live run.
RESULT_PENDING = "<!-- result: to be captured from a live run -->"
CALL = re.compile(r"^Call:\n\n```json\n(.+)\n```$", flags=re.MULTILINE)


RECORDS = yaml.safe_load((ROOT / "spec/records.yaml").read_text(encoding="utf-8"))


def provenances(value):
    """Every provenance record nested anywhere in a parsed example."""

    children = value.values() if isinstance(value, dict) else value
    nested = (
        [each for item in children for each in provenances(item)]
        if isinstance(value, dict | list)
        else []
    )
    own = [value["provenance"]] if isinstance(value, dict) and "provenance" in value else []
    return own + nested


def served_arguments():
    """The arguments each tool of the server takes, by tool."""

    async def run(data_dir):
        async with Client(create_mcp(Settings(data_dir=data_dir))) as client:
            tools = (await client.list_tools()).tools
            return {tool.name: set(tool.input_schema["properties"]) for tool in tools}

    with tempfile.TemporaryDirectory() as directory:
        return asyncio.run(run(Path(directory)))


# Only the ignore file may name the local files of a coding tool; the names are built from parts
# so that this file does not name them either.
LOCAL_AGENT_FILES = tuple(
    "".join(parts)
    for parts in (("CLAUDE", ".md"), (".claude", "/"), ("CLAUDE", ".local.md"), (".mcp", ".json"))
)


def tracked_mentions_of_local_agent_files(root):
    """`path:line` of every line in a tracked file, but the ignore file, naming one of them."""

    listing = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607 - git from PATH
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    found = []
    for name in filter(None, listing.split("\0")):
        path = root / name
        if name == ".gitignore" or not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        found += [
            f"{name}:{number}"
            for number, line in enumerate(lines, 1)
            if any(local in line for local in LOCAL_AGENT_FILES)
        ]
    return found


class DocumentationTest(unittest.TestCase):
    def test_quickstart_introduces_exactly_the_settings_the_code_reads(self):
        setting = re.compile(r"NCI_SI_[A-Z_]+")
        sources = [path.read_text(encoding="utf-8") for path in PACKAGE.glob("*.py")]
        read_by_the_code = {name for source in sources for name in setting.findall(source)}

        # A setting is introduced by its row in the table or by an `export` example.
        exported = set(re.findall(r"^export (NCI_SI_[A-Z_]+)=", QUICKSTART, flags=re.MULTILINE))
        table = first_column(section(QUICKSTART, "Settings"))
        self.assertEqual(table | exported, read_by_the_code)
        self.assertEqual(set(setting.findall(QUICKSTART)), read_by_the_code)

    def test_quickstart_names_the_licence_key_header_and_the_wait_cap_the_client_uses(self):
        row = next(line for line in QUICKSTART.splitlines() if "NCI_SI_EVS_LICENSE_KEY" in line)
        backoff = next(line for line in QUICKSTART.splitlines() if "RETRY_BACKOFF" in line)

        self.assertIn("`X-EVSRESTAPI-License-Key`", row)
        self.assertEqual(LICENSE_KEY_HEADER, "X-EVSRESTAPI-License-Key")
        self.assertIn(f"capped at {MAX_RETRY_DELAY_SECONDS:.0f} seconds", backoff)

    def test_quickstart_states_the_default_of_each_setting_in_the_table(self):
        table = section(QUICKSTART, "Settings")
        row = re.compile(r"^\|\s*`(\w+)`\s*\|\s*(`[^`]+`|unset)\s*\|", flags=re.MULTILINE)
        documented = dict(row.findall(table))
        with patch.dict(os.environ, clear=True):
            defaults = Settings.from_env()

        self.assertEqual(set(documented), first_column(table))
        for name, default in documented.items():
            with self.subTest(name):
                actual = getattr(defaults, name.removeprefix("NCI_SI_").lower())
                if default == "unset":
                    self.assertIsNone(actual)
                elif isinstance(actual, tuple):
                    self.assertEqual(default.strip("`"), ",".join(actual))
                else:
                    self.assertEqual(type(actual)(default.strip("`")), actual)

    def test_quickstart_documents_the_closed_value_sets_of_the_code(self):
        table = section(QUICKSTART, "Settings")
        for variable, values in (
            ("NCI_SI_PROFILE", PROFILES),
            ("NCI_SI_UPSTREAM_MODE", UPSTREAM_MODES),
            ("NCI_SI_RELEASE_CHANNEL", RELEASE_CHANNELS),
        ):
            with self.subTest(variable):
                row = next(line for line in table.splitlines() if f"`{variable}`" in line)
                purpose = row.split("|")[3]
                self.assertEqual(set(re.findall(r"`(\w+)`", purpose)), values)

    def test_quickstart_error_table_lists_exactly_the_error_codes(self):
        self.assertEqual(first_column(section(QUICKSTART, "Errors")), set(get_args(ErrorCode)))

    @patch("nci_si_mcp.server.configure_logging")
    def test_every_usage_example_calls_a_tool_and_arguments_the_server_has(self, _):
        # Creating a server installs a root log handler; take it out again.
        root = logging.getLogger()
        self.addCleanup(setattr, root, "handlers", root.handlers[:])
        self.addCleanup(root.setLevel, root.level)
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        examples = section(QUICKSTART, "Usage examples").split("\n### ")[1:]
        served = served_arguments()

        self.assertEqual(len(examples), 3)
        for example in examples:
            with self.subTest(example.splitlines()[0]):
                self.assertIn('User prompt: "', example)
                # The result is still pending, or captured as a code block.
                result = example.partition("\nResult:\n\n")[2]
                self.assertTrue(result.startswith((RESULT_PENDING, "```")), result)
                call = json.loads(CALL.search(example).group(1))
                self.assertIn(call["tool"], served)
                self.assertLessEqual(set(call["arguments"]), served[call["tool"]])

    def test_quickstart_provenance_table_names_the_fields_of_the_specification(self):
        text = section(QUICKSTART, "Provenance and truncation")

        self.assertEqual(first_column(text), set(RECORDS["provenance"]["fields"]))
        # The bounds the text lists are values of the truncation record.
        listed = re.search(r"`bound` \(([^)]*)\)", text).group(1)
        self.assertLessEqual(
            set(re.findall(r"`(\w+)`", listed)),
            set(RECORDS["truncation"]["fields"]["bound"]["values"]),
        )

    def test_quickstart_names_the_exclusion_roles_and_the_cli_flag_the_code_has(self):
        text = section(QUICKSTART, "Provenance and truncation")
        low, high = map(int, re.search(r"R(\d+) to R(\d+)", text).groups())

        self.assertEqual(NCIT_EXCLUSION_CODES, {f"R{number}" for number in range(low, high + 1)})
        self.assertIn("--include-raw", QUICKSTART)
        for command in ("search", "lookup"):
            arguments = build_parser().parse_args([command, "x", "--include-raw"])
            self.assertTrue(arguments.include_raw)
        # Every command-line flag the guide names is one of the CLI.
        (subcommands,) = build_parser()._subparsers._group_actions
        flags = {
            flag
            for parser in subcommands.choices.values()
            for flag in parser._option_string_actions
        }
        self.assertLessEqual(set(re.findall(r"(?<![\w-])--[a-z][a-z-]+", QUICKSTART)), flags)

    def test_quickstart_examples_parse_and_carry_the_provenance_field_set(self):
        blocks = re.findall(r"```json\n(.*?)\n```", QUICKSTART, flags=re.DOTALL)
        found = [record for block in blocks for record in provenances(json.loads(block))]
        fields = RECORDS["provenance"]["fields"]
        required = {name for name, spec in fields.items() if not spec.get("optional")}
        required -= {"graphs", "upstream"}
        allowed = set(fields) | set(RECORDS["traversal"]["fields"])

        # The lookups, the three hits shown and the nodes and edges of the traversal.
        self.assertGreaterEqual(len(found), 12)
        for record in found:
            self.assertLessEqual(required, set(record))
            self.assertLessEqual(set(record), allowed)

    def test_architecture_provenance_section_names_functions_that_exist(self):
        # `owner.name`, where the owner is a module or a class of one; `models.py` is a file name.
        named = re.findall(r"`(\w+)\.(?!py\b)(\w+)`", section(ARCHITECTURE, "Provenance"))
        stems = sorted(path.stem for path in PACKAGE.glob("*.py") if path.stem != "__init__")
        modules = {stem: importlib.import_module(f"nci_si_mcp.{stem}") for stem in stems}
        owners = dict(modules)
        for module in modules.values():
            owners.update(vars(module))

        self.assertTrue(named)
        for owner, name in named:
            with self.subTest(f"{owner}.{name}"):
                self.assertTrue(hasattr(owners[owner], name))

    def test_architecture_describes_every_module(self):
        modules = {path.name for path in PACKAGE.glob("*.py")} - {"__init__.py"}

        self.assertEqual(first_column(section(ARCHITECTURE, "Components")), modules)

    def test_agent_instructions_exist_and_no_tracked_file_names_a_local_tool_file(self):
        self.assertTrue((ROOT / "AGENTS.md").is_file(), "AGENTS.md must exist")
        mentions = tracked_mentions_of_local_agent_files(ROOT)

        self.assertEqual(
            mentions,
            [],
            "only .gitignore may name the local files of a coding tool "
            f"({', '.join(LOCAL_AGENT_FILES)}); found at {', '.join(mentions)}",
        )

    def test_the_check_reports_a_tracked_mention_and_ignores_the_ignore_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)  # noqa: S607
            (root / ".gitignore").write_text(f"{LOCAL_AGENT_FILES[0]}\n", encoding="utf-8")
            (root / "guide.md").write_text(f"ok\nsee {LOCAL_AGENT_FILES[1]}x\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)  # noqa: S607

            self.assertEqual(tracked_mentions_of_local_agent_files(root), ["guide.md:2"])


if __name__ == "__main__":
    unittest.main()
