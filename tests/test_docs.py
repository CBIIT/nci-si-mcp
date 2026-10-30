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
import tomllib
import unittest
from dataclasses import replace
from pathlib import Path
from typing import get_args
from unittest.mock import patch

import yaml
from mcp.client import Client

from nci_si_mcp.audit import Audit
from nci_si_mcp.cli import build_parser
from nci_si_mcp.config import DEFAULT_CALL_DEADLINE_SECONDS, DEFAULT_EXCLUSION_ROLE_CODES, Settings
from nci_si_mcp.context import Context
from nci_si_mcp.errors import CORRELATION_ID_FORM, ErrorCode, correlated
from nci_si_mcp.evs import LICENSE_KEY_HEADER
from nci_si_mcp.http_client import MAX_RETRY_DELAY_SECONDS
from nci_si_mcp.models import IndexManifest
from nci_si_mcp.registry import SPECS
from nci_si_mcp.server import create_mcp
from nci_si_mcp.transport import GRACEFUL_SHUTDOWN_SECONDS, create_http_app
from nci_si_mcp.validation import (
    MAX_DICTIONARY_TEXT,
    MAX_MATCH_TEXT,
    MAX_QUERY_TEXT,
    MAX_QUESTION_TEXT,
    MAX_TEXT_TARGET_BYTES,
    PROFILES,
    RELEASE_CHANNELS,
    UPSTREAM_MODES,
)
from ncit_index.scoring import MAX_LEXICAL_TOKENS

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


# Build personal filenames from parts so this contract does not itself name them.
LOCAL_AGENT_FILES = tuple(
    "".join(parts)
    for parts in (
        ("CLAUDE", ".local.md"),
        (".claude", "/settings.local.json"),
        (".mcp", ".json"),
    )
)
LOCAL_AGENT_PATHS = (*LOCAL_AGENT_FILES, ".claude/worktrees/")


def tracked_mentions_of_local_agent_files(root):
    """Personal filename mentions in tracked readable files, except the ignore file.

    Untracked files and non-file entries are outside this scan; undecodable bytes are replaced.
    """

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


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def tracked_documents():
    """Tracked Markdown files, except the generated specification and the upstream packages."""

    tracked = subprocess.run(
        ["git", "ls-files", "*.md"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [
        name
        for name in tracked
        if not name.startswith(("spec/", "docs/upstream/")) and name != "docs/specification.md"
    ]


def shell_lines(name):
    """(line number, text) of each line inside a shell or unlabelled fenced block."""

    fence = None
    for number, line in enumerate(read(name).splitlines(), 1):
        text = line.strip()
        if text.startswith("```"):
            fence = None if fence is not None else text[3:]
        elif fence in ("", "bash", "sh", "console"):
            yield number, text


def operations_rows(document, heading):
    """Bounded two-value contract columns; the remaining column explains their meaning."""
    text = document.partition(f"\n## {heading}\n")[2].split("\n## ", 1)[0]
    return [
        tuple(cell.strip().strip("`") for cell in line.split("|")[1:3])
        for line in text.splitlines()
        if line.startswith("| `")
    ]


def operations_endpoints(settings):
    """The installed health/readiness routes and their available/unavailable responses."""
    app = create_http_app(settings, context=Context(settings))
    endpoints = {
        route.path: route.endpoint for route in app.routes if route.path in {"/health", "/ready"}
    }
    health = asyncio.run(endpoints["/health"](None))
    ready = asyncio.run(endpoints["/ready"](None))
    required = create_http_app(replace(settings, http_require_index=True))
    blocked_endpoint = next(route.endpoint for route in required.routes if route.path == "/ready")
    with patch("nci_si_mcp.transport.emit") as transition:
        blocked = asyncio.run(blocked_endpoint(None))
    return {
        **{
            path: [response.status_code, json.loads(response.body)]
            for path, response in (
                ("/health", health),
                ("/ready", ready),
                ("/ready:unavailable", blocked),
            )
        },
        transition.call_args.args[2]: sorted(transition.call_args.kwargs),
    }


def operations_signals(settings):
    """Resolve the document's inventory through the interfaces that own the signals."""
    with patch("nci_si_mcp.audit.emit") as completion:
        Audit("doc-contract", {}, {}, (), result={}).finish()
    manifest = IndexManifest("ncit", "test", None, "hashing", "hashing", 0, "test", "test")
    with correlated():
        manifest_fields = sorted(manifest.to_result())
    return {
        **operations_endpoints(settings),
        completion.call_args.args[2]: sorted(completion.call_args.kwargs),
        "ErrorCode": sorted(get_args(ErrorCode)),
        next(spec.uri for spec in SPECS if spec.operation == "index_resource"): manifest_fields,
    }


class OperationsDocumentationTest(unittest.TestCase):
    def assert_signals(self, document, available):
        rows = operations_rows(document, "Available signals")
        self.assertEqual(len(rows), 7, "operations signal inventory must describe seven interfaces")
        self.assertEqual({name for name, _ in rows}, set(available))
        for name, encoded in rows:
            self.assertIn(name, available)
            if name == "ErrorCode":
                self.assertLessEqual(set(json.loads(encoded)), set(available[name]))
            else:
                self.assertEqual(json.loads(encoded), available[name])

    def assert_settings(self, document):
        rows = operations_rows(document, "Operational settings")
        self.assertGreater(len(rows), 0, "operations settings inventory must not be empty")
        self.assertEqual({name for name, _ in rows}, set(re.findall(r"NCI_SI_[A-Z_]+", document)))
        with patch.dict(os.environ, clear=True):
            defaults = Settings.from_env()
        for name, encoded in rows:
            attribute = name.removeprefix("NCI_SI_").lower()
            self.assertTrue(hasattr(defaults, attribute), name)
            self.assertEqual(json.loads(encoded), getattr(defaults, attribute), name)

    def test_governance_states_the_exact_sponsor_approval_path(self):
        expected = (
            "The government sponsor approves changes; the NCI SI MCP project coordinator "
            "relays that approval, and the reviewer acts for the coordinator."
        )
        for name in ("AGENTS.md", "CONTRIBUTING.md"):
            paragraphs = [" ".join(p.split()) for p in read(name).split("\n\n")]
            found = [p for p in paragraphs if p.startswith("The government sponsor")]
            self.assertEqual(found, [expected], name)

    def test_operations_signals_and_settings_match_their_runtime_owners(self):
        path = ROOT / "docs/operations.md"
        document = path.read_text() if path.exists() else ""
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("nci_si_mcp.server.configure_logging"),
        ):
            available = operations_signals(Settings(data_dir=Path(directory)))
        self.assert_signals(document, available)
        self.assert_settings(document)

    def test_invented_operations_claims_fail_the_same_document_comparisons(self):
        document = read("docs/operations.md")
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("nci_si_mcp.server.configure_logging"),
        ):
            available = operations_signals(Settings(data_dir=Path(directory)))
        mutations = (
            ("/ready`", "/invented`", self.assert_signals),
            ("elapsedMs", "inventedField", self.assert_signals),
            ("builtAt", "inventedDate", self.assert_signals),
            ("NCI_SI_LOG_LEVEL", "NCI_SI_INVENTED", self.assert_settings),
            ('`"INFO"`', '`"DEBUG"`', self.assert_settings),
            (
                "## Operational settings",
                "NCI_SI_UNLISTED\n\n## Operational settings",
                self.assert_settings,
            ),
        )
        for old, new, compare in mutations:
            with self.subTest(old=old):
                self.assertIn(old, document)
                arguments = (available,) if compare == self.assert_signals else ()
                with self.assertRaises(AssertionError):
                    compare(document.replace(old, new), *arguments)


class DocumentationTest(unittest.TestCase):
    def test_typing_conventions_are_consulted_before_python_and_in_type_review(self):
        agents = read("AGENTS.md")
        self.assertIn(
            "Before writing or changing Python, read CONTRIBUTING.md's Typing conventions and "
            "say in the issue plan which constructs the change uses.",
            agents,
        )
        self.assertIn("Check the diff against the Typing conventions in CONTRIBUTING.md.", agents)
        conventions = (
            read("CONTRIBUTING.md").partition("### Typing conventions\n")[2].split("\n## ", 1)[0]
        )
        rows = [line.split("|")[1:3] for line in conventions.splitlines() if line.startswith("| ")][
            2:
        ]
        self.assertEqual(
            [(status.strip(), situation.strip()) for status, situation in rows],
            [
                ("Standard", "MCP inputs"),
                ("Project convention", "MCP output"),
                ("Standard", "Trust boundaries"),
                ("Project convention", "Internal data"),
                ("Standard", "JSON records"),
                ("Standard", "Interfaces"),
                ("Project convention", "Closed sets"),
                ("Standard", "Distinct identifiers"),
                ("Project convention", "Storage"),
                ("Project convention", "Escapes"),
            ],
        )

    def test_domain_stories_name_the_specification_prompts_and_exact_tool_sequences(self):
        path = ROOT / "docs/user-acceptance.md"
        document = path.read_text(encoding="utf-8") if path.exists() else ""
        prompts = yaml.safe_load(read("spec/prompts.yaml"))
        stories = re.findall(r"^## Story: `([^`]+)`\n(.*?)(?=\n## |\Z)", document, re.M | re.S)

        self.assertEqual({name for name, _ in stories}, set(prompts))
        for name, story in stories:
            with self.subTest(name):
                tools = re.search(r"^Tools: (.+)$", story, re.M).group(1)
                self.assertEqual(re.findall(r"`([^`]+)`", tools), prompts[name]["tools"])
                prompt = re.search(r"^Prompt: (.+)$", story, re.M).group(1)
                self.assertEqual(
                    re.findall(r"`([^`]+)`", prompt),
                    [name, *[argument["name"] for argument in prompts[name]["arguments"]]],
                )
                for label in (
                    "Role",
                    "Need",
                    "Benefit",
                    "Given",
                    "When",
                    "Then",
                    "Pass criteria",
                    "Role confirms",
                    "Gaps",
                    "Evidence",
                ):
                    self.assertRegex(story, rf"(?m)^{label}: .+\S")

    def test_uat_records_provider_approval_and_configuration_without_claiming_execution(self):
        document = read("docs/user-acceptance.md")
        for statement in (
            "Status: planned; UAT has not run.",
            "Owner clarification, 25 October 2026: NCI/NIH approves model providers. "
            "Any MCP-capable client\n"
            "using an NCI-approved model provider is a valid UAT environment.",
            "Decision owner: NCI model-provider/platform owner.",
            "Known examples include Claude Code (desktop), ChatGPT (Codex) and GitHub Copilot, "
            "which can\n"
            "host a local stdio MCP server, and access to AWS Bedrock models through Cloud One. "
            "These are\n"
            "examples, not a closed list. Bedrock through Cloud One is a model source, "
            "not an MCP client;\n"
            "record the client separately from its provider and model.",
            "Before each session, confirm and record that the model provider is NCI-approved, "
            "the model\n"
            "name and version, the client name and version, and the transport. "
            "An unapproved provider is\n"
            "not a valid UAT environment; missing confirmation or configuration details "
            "block the session.",
            "A story is accepted only for the client, provider and model configuration "
            "in its recorded run,\n"
            "including the client and model versions. Results do not establish acceptance "
            "for another\n"
            "client, provider or model configuration.",
            "Keep one record per story, client/provider/model configuration, participant "
            "and attempt, with\n"
            "references to the retained artifacts:",
            "- `environment`: client name and version, model name and version, "
            "model provider and NCI\n"
            "  provider approval reference, model source (such as Bedrock via Cloud One), "
            "transport, profile,\n"
            "  server version and commit SHA.",
        ):
            self.assertIn(statement, document)
        self.assertNotRegex(document, r"(?i)permission|approved\s+client")
        self.assertEqual(
            bullet_names(section(document, "Evidence record")),
            [
                "story",
                "participant",
                "environment",
                "content",
                "conversation",
                "trace",
                "decision",
            ],
        )

    def test_readme_explains_client_orchestration_and_links_the_uat_plan(self):
        readme = read("README.md")
        for statement in (
            "The client model holds the conversation and selects tool calls; the prompts guide it.",
            "The tools are deterministic; the server runs no LLM.",
            "Workflow tools also bundle their own upstream calls deterministically, "
            "with provenance.",
            "[Domain-user stories and UAT plan](docs/user-acceptance.md)",
            "UAT runs in any MCP-capable client that uses an NCI-approved model provider; "
            "none has run yet.",
        ):
            self.assertIn(statement, readme)

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
                elif isinstance(actual, bool):
                    self.assertEqual(default.strip("`"), str(int(actual)))
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

    def test_quickstart_limits_table_values_match_their_code_constants(self):
        table = section(QUICKSTART, "Limits and reasons")
        rows = [line.split("|")[1:-1] for line in table.splitlines() if line.startswith("| ")]
        documented = {name.strip(): value.strip() for name, value, _reason in rows[1:]}
        expected = {
            "Call deadline (`NCI_SI_CALL_DEADLINE_SECONDS`)": (
                f"{DEFAULT_CALL_DEADLINE_SECONDS:g} s default"
            ),
            "HTTP shutdown drain": f"{GRACEFUL_SHUTDOWN_SECONDS:g} s",
            "`search_concepts.query`, `search_data_elements.query`": f"{MAX_QUERY_TEXT:,}",
            "`get_data_element.questionText`": f"{MAX_QUESTION_TEXT:,}",
            "`ground_value.text`; CDE-match `entities[].name`, `userTip`, `permissibleValues[]`; "
            "`match_value_meanings.values[]`": f"{MAX_MATCH_TEXT:,} each",
            "Dictionary `columns[].name`, `description`, `sampleValues[]`": (
                f"{MAX_DICTIONARY_TEXT:,} each"
            ),
            "URL-carried free text: complete encoded request target, including filters/pins": (
                f"{MAX_TEXT_TARGET_BYTES:,} bytes"
            ),
            "Local lexical FTS expression": f"First {MAX_LEXICAL_TOKENS} tokens, in order",
            "Supplied `_meta.correlationId`": f"Full match `{CORRELATION_ID_FORM}`",
        }
        self.assertEqual(documented, expected)
        self.assertEqual(len(rows) - 1, len(expected))

    def test_quickstart_error_table_lists_exactly_the_error_codes(self):
        self.assertEqual(first_column(section(QUICKSTART, "Errors")), set(get_args(ErrorCode)))

    def test_quickstart_input_and_deadline_errors_explain_the_callers_remedy(self):
        rows = {
            cells[0].strip().strip("`"): tuple(cell.strip() for cell in cells[1:])
            for line in section(QUICKSTART, "Errors").splitlines()
            if line.startswith("| `")
            for cells in [line.split("|")[1:-1]]
        }
        meaning, details = rows["invalid_request"]
        self.assertEqual(details, "`parameter`, `reason`")
        for clause in (
            "Text exceeding its field's `maxLength` or the 7,000-byte encoded URL-target "
            "ceiling is refused, never shortened: reduce the named field and check filters/pins.",
            "A supplied `_meta.correlationId` must fully match `[A-Za-z0-9._:-]{1,128}`: "
            "correct it or omit it; refusal uses a fresh safe ID without echoing the rejected "
            "value.",
        ):
            self.assertIn(clause, meaning)
        self.assertIn(
            "Call deadline exhaustion uses this code, with `attempts` counting all outbound "
            "requests actually started across the call, including retries and release discovery",
            rows["upstream_unavailable"][0],
        )
        self.assertEqual(
            rows["timeout"][0],
            "Every attempt at an upstream request timed out (`NCI_SI_TIMEOUT_SECONDS`; "
            "`NCI_SI_MATCH_TIMEOUT_SECONDS` for caDSR matching) before the call deadline; "
            "timeouts are errors, never empty results",
        )

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

        self.assertEqual(
            set(DEFAULT_EXCLUSION_ROLE_CODES), {f"R{number}" for number in range(low, high + 1)}
        )
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

    def test_quickstart_cli_section_names_subcommands_the_cli_has(self):
        (subcommands,) = build_parser()._subparsers._group_actions
        cli = section(QUICKSTART, "MCP Tools").split("\n### CLI\n", 1)[1].split("\n### ", 1)[0]
        named = set(re.findall(r"`([a-z][a-z-]+)[ `]", cli))
        # `serve` has its own section; the three local-index commands are the diagnostics the
        # section's opening sentence names as such.
        documented_elsewhere = {"serve", "search", "lookup", "traverse"}

        self.assertTrue(named)
        self.assertLessEqual(named, set(subcommands.choices))
        self.assertEqual(set(subcommands.choices) - documented_elsewhere - named, set())

    def test_quickstart_examples_parse_and_carry_the_provenance_field_set(self):
        blocks = re.findall(r"```json\n(.*?)\n```", QUICKSTART, flags=re.DOTALL)
        found = [record for block in blocks for record in provenances(json.loads(block))]
        fields = RECORDS["provenance"]["fields"]
        required = {name for name, spec in fields.items() if not spec.get("optional")}
        required -= {"graphs"}
        allowed = set(fields) | set(RECORDS["traversal"]["fields"])

        # One lookup, one hit, and in the shortened neighborhood two nodes plus one edge.
        self.assertEqual(len(found), 5)
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

    def test_shared_agent_files_are_tracked_and_the_entry_file_only_imports_the_rules(self):
        tracked = subprocess.run(
            ["git", "ls-files", "-z"],  # noqa: S607
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split("\0")

        self.assertEqual(
            {name for name in tracked if name in {"CLAUDE.md", ".claude/settings.json"}},
            {"CLAUDE.md", ".claude/settings.json"},
        )
        self.assertEqual(read("CLAUDE.md"), "@AGENTS.md\n")

    def test_personal_agent_files_and_runtime_paths_are_ignored(self):
        for name in LOCAL_AGENT_PATHS:
            with self.subTest(name=name):
                result = subprocess.run(  # noqa: S603 - fixed Git command and contract paths
                    ["git", "check-ignore", "-v", name],  # noqa: S607
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                # Global excludes must not hide a missing repository ignore rule.
                source, match = result.stdout.rstrip("\n").split("\t")
                self.assertEqual(source.split(":", 2)[::2], [".gitignore", name])
                self.assertEqual(match, name)

    def test_the_check_reports_a_tracked_mention_and_ignores_the_ignore_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)  # noqa: S607
            (root / ".gitignore").write_text(f"{LOCAL_AGENT_FILES[0]}\n", encoding="utf-8")
            (root / "guide.md").write_text(
                "CLAUDE.md and .claude/settings.json are shared\n"
                + "\n".join(f"see {name}" for name in LOCAL_AGENT_FILES)
                + "\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "."], cwd=root, check=True)  # noqa: S607

            self.assertEqual(
                tracked_mentions_of_local_agent_files(root),
                ["guide.md:2", "guide.md:3", "guide.md:4"],
            )

    def test_contributing_names_every_pdm_command_of_the_project(self):
        # Every entry point in pyproject.toml is a command a contributor may need; the
        # command tables of CONTRIBUTING.md are where they look it up.
        with (ROOT / "pyproject.toml").open("rb") as handle:
            scripts = tomllib.load(handle)["tool"]["pdm"]["scripts"]
        commands = [name for name in scripts if not name.startswith("_")]
        contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")

        self.assertTrue(commands)
        self.assertEqual(
            [
                name
                for name in commands
                if not re.search(rf"pdm run {re.escape(name)}(?![\w-])", contributing)
            ],
            [],
        )

    def test_quickstart_links_to_the_installer_target_interface(self):
        quickstart = (ROOT / "QUICKSTART.md").read_text(encoding="utf-8")
        target_interface = ROOT / "docs" / "installer-targets.md"

        self.assertIn("[installer target interface](docs/installer-targets.md)", quickstart)
        self.assertTrue(target_interface.is_file())
        self.assertIn(
            "check prerequisites → install → configure → verify",
            target_interface.read_text(encoding="utf-8"),
        )

    def test_every_repository_url_in_the_documents_names_this_repository(self):
        # The documents must send readers to CBIIT/nci-si-mcp, never to a fork of it.
        tracked = subprocess.run(
            ["git", "ls-files", "*.md", "*.yaml", "*.yml", "*.json", "*.toml"],  # noqa: S607
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        foreign = [
            f"{name}:{number}"
            for name in tracked
            for number, line in enumerate((ROOT / name).read_text(encoding="utf-8").splitlines(), 1)
            for owner in re.findall(r"github\.com/([A-Za-z0-9_.-]+)/nci-si-mcp", line)
            if owner != "CBIIT"
        ]

        self.assertTrue(tracked)
        self.assertEqual(foreign, [])

    def test_every_file_in_docs_has_a_lower_case_name(self):
        # Owner's rule: names in docs/ differ by meaning, never by case alone.
        names = [path.name for path in (ROOT / "docs").iterdir()]

        self.assertTrue(names)
        self.assertEqual([name for name in names if name != name.lower()], [])

    def test_the_platform_statement_exists_once_and_the_setup_documents_link_to_it(self):
        statement = "Supported platforms: macOS (primary) and Linux, with a POSIX shell."
        holders = [name for name in tracked_documents() if statement in read(name)]
        links = {
            "README.md": "QUICKSTART.md",
            "CONTRIBUTING.md": "QUICKSTART.md",
            "docs/container.md": "../QUICKSTART.md",
            "docs/local-validation.md": "../QUICKSTART.md",
            "docs/companion-containers.md": "../QUICKSTART.md",
            "docs/documentation-site.md": "../QUICKSTART.md",
        }

        self.assertEqual(holders, ["QUICKSTART.md"])
        self.assertIn("\n### Platforms and prerequisites\n", read("QUICKSTART.md"))
        self.assertEqual(
            [
                name
                for name, target in links.items()
                if f"]({target}#platforms-and-prerequisites)" not in read(name)
            ],
            [],
        )

    def test_the_code_of_conduct_holds_the_report_address_and_the_entry_documents_link_to_it(self):
        address = "hannes.niedner@nih.gov"
        holders = [name for name in tracked_documents() if address in read(name)]

        self.assertEqual(holders, ["CODE_OF_CONDUCT.md"])
        self.assertEqual(
            [
                name
                for name in ("README.md", "CONTRIBUTING.md")
                if "](CODE_OF_CONDUCT.md)" not in read(name)
            ],
            [],
        )

    def test_shell_blocks_run_project_commands_through_pdm_run(self):
        # After `pdm install` the environment is not activated, so a bare `python -m ...`
        # or `nci-si-mcp ...` fails for a new developer. The one exception installs the
        # released package with pip, which puts `nci-si-mcp` on the PATH itself.
        released = {
            ("docs/local-guide.md", command)
            for command in (
                'python3.14 -m venv "$SERVER_VENV"',
                'python3.14 -m venv "$INDEX_VENV"',
                '"$INDEX_VENV/bin/nci-si-mcp" index-sample C3262 C2991 C40704 C153397 C116938',
                '"$INDEX_VENV/bin/nci-si-mcp" evaluate',
                '"$INDEX_VENV/bin/nci-si-mcp" serve --transport streamable-http',
            )
        }
        bare = re.compile(
            r"(?:\w+=\S+\s+)*(?:python3?(?:\.\d+)?|nci-si-mcp|pytest|ruff|basedpyright)\s"
        )
        found = [
            (name, number)
            for name in tracked_documents()
            for number, line in shell_lines(name)
            if bare.match(line + " ") and (name, line) not in released
        ]

        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
