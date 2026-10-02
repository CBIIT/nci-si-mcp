"""The documentation names what the code defines: settings, error codes and modules.

The tools and resources in the README are compared with the running server in test_server.
"""

import os
import re
import unittest
from pathlib import Path
from typing import get_args
from unittest.mock import patch

from nci_si_mcp.config import Settings
from nci_si_mcp.errors import ErrorCode

ROOT = Path(__file__).parent.parent
PACKAGE = ROOT / "src" / "nci_si_mcp"
README = (ROOT / "README.md").read_text(encoding="utf-8")
ARCHITECTURE = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")


def section(document, heading):
    """The text of a second-level section, without its heading."""

    return document.split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]


def bullet_names(text):
    """The name in backticks that starts each bullet of a Markdown list."""

    return re.findall(r"^- `([^`]+)`", text, flags=re.MULTILINE)


def first_column(table):
    """The names in backticks in the first column of a Markdown table."""

    rows = [row.split("|")[1] for row in table.splitlines() if row.startswith("| `")]
    return {name for row in rows for name in re.findall(r"`([^`]+)`", row)}


class DocumentationTest(unittest.TestCase):
    def test_readme_introduces_exactly_the_settings_the_code_reads(self):
        setting = re.compile(r"NCI_SI_[A-Z_]+")
        sources = [path.read_text(encoding="utf-8") for path in PACKAGE.glob("*.py")]
        read_by_the_code = {name for source in sources for name in setting.findall(source)}

        # A setting is introduced by its row in the table or by an `export` example.
        exported = set(re.findall(r"^export (NCI_SI_[A-Z_]+)=", README, flags=re.MULTILINE))
        table = first_column(section(README, "Build A Small Local Index"))
        self.assertEqual(table | exported, read_by_the_code)
        self.assertEqual(set(setting.findall(README)), read_by_the_code)

    def test_readme_states_the_default_of_each_setting_in_the_table(self):
        table = section(README, "Build A Small Local Index")
        documented = dict(re.findall(r"^\| `(\w+)` \| `([^`]+)` \|", table, flags=re.MULTILINE))
        with patch.dict(os.environ, clear=True):
            defaults = Settings.from_env()

        # The default of the base URL is described in words.
        self.assertEqual(set(documented), first_column(table) - {"NCI_SI_EVS_BASE_URL"})
        for name, default in documented.items():
            with self.subTest(name):
                actual = getattr(defaults, name.removeprefix("NCI_SI_").lower())
                self.assertEqual(type(actual)(default), actual)

    def test_readme_error_table_lists_exactly_the_error_codes(self):
        self.assertEqual(first_column(section(README, "Errors")), set(get_args(ErrorCode)))

    def test_architecture_describes_every_module(self):
        modules = {path.name for path in PACKAGE.glob("*.py")} - {"__init__.py"}

        self.assertEqual(first_column(section(ARCHITECTURE, "Components")), modules)


if __name__ == "__main__":
    unittest.main()
