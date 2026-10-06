"""The smoke command checks retention and reaps its container-owned assets."""

import io
import json
import shutil
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from scripts import container_smoke


class SmokeLifecycle:
    def __init__(self, changes_build):
        self.changes_build = changes_build
        self.builds = ["prepared"]
        self.cleaned = False

    def prepare(self, _image, assets):
        self.assets = assets
        (assets / "model").mkdir()
        (assets / "builds.json").write_text(json.dumps(self.builds))

    def serve(self, _image, _assets):
        if self.changes_build:
            self.builds = ["unexpected replacement"]

    def docker(self, *args):
        if "list_builds()" in args[-1]:
            return SimpleNamespace(stdout=json.dumps(self.builds))
        if "shutil.rmtree" not in args[-1]:
            raise AssertionError("Unexpected Docker command")
        for path in self.assets.iterdir():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        self.cleaned = True


class SmokeCommandTest(unittest.TestCase):
    def test_changed_build_fails_the_command_and_reaps_assets(self):
        for changed in (False, True):
            with self.subTest(changed=changed), TemporaryDirectory() as directory:
                lifecycle = SmokeLifecycle(changed)
                output = io.StringIO()
                with (
                    patch.object(container_smoke, "ROOT", Path(directory)),
                    patch("sys.argv", ["container_smoke.py", "test-image"]),
                    patch.object(container_smoke, "failure"),
                    patch.object(container_smoke, "prepare", lifecycle.prepare),
                    patch.object(container_smoke, "serve", lifecycle.serve),
                    patch.object(container_smoke, "docker", lifecycle.docker),
                    redirect_stdout(output),
                ):
                    if changed:
                        with self.assertRaisesRegex(RuntimeError, "Serving changed"):
                            container_smoke.main()
                    else:
                        container_smoke.main()
                self.assertEqual("Image smoke passed" in output.getvalue(), not changed)
                self.assertTrue(lifecycle.cleaned)
                self.assertFalse(lifecycle.assets.exists())
