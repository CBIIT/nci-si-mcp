"""Every script that a workflow, action, PDM script or document runs by its file path starts.

A script run as `python scripts/name.py` has `scripts/` on its path, not the repository root,
so it cannot import the `scripts` package; such a script must be invoked with `python -m`.
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
INVOCATION = re.compile(r"python(?:3(?:\.14)?)? (?:\.\./)?(scripts/[\w/]+\.py)")
SOURCES = (
    *ROOT.glob(".github/**/*.yml"),
    ROOT / "pyproject.toml",
    *ROOT.glob("*.md"),
    *ROOT.glob("docs/*.md"),
    *ROOT.glob("acceptance/*.md"),
)
# Top-level imports only: a run name other than __main__ never reaches the script's main().
LOAD = (
    "import runpy, sys; sys.path.insert(0, sys.argv[2]); "
    "runpy.run_path(sys.argv[1], run_name='entry')"
)


def invoked_by_path() -> dict[str, set[str]]:
    scripts: dict[str, set[str]] = {}
    for source in SOURCES:
        for script in INVOCATION.findall(source.read_text()):
            scripts.setdefault(script, set()).add(str(source.relative_to(ROOT)))
    return scripts


class ScriptEntryPointTest(unittest.TestCase):
    def test_every_script_invoked_by_its_path_loads_as_a_file(self):
        scripts = invoked_by_path()
        self.assertIn("scripts/image_scan.py", scripts, "The invocation pattern finds no callers")
        with TemporaryDirectory() as elsewhere:
            for script, callers in sorted(scripts.items()):
                with self.subTest(script=script, callers=sorted(callers)):
                    path = ROOT / script
                    loaded = subprocess.run(  # noqa: S603 - fixed interpreter, repository files
                        [sys.executable, "-I", "-c", LOAD, str(path), str(path.parent)],
                        cwd=elsewhere,
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=60,
                    )
                    self.assertEqual(loaded.returncode, 0, loaded.stderr.strip().splitlines()[-1:])


if __name__ == "__main__":
    unittest.main()
