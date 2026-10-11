"""The smoke command checks retention and reaps its container-owned assets."""

import io
import json
import shutil
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from scripts import container_smoke, processes


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
    def test_command_exercises_auth_default_after_preparing_assets(self):
        events = []

        def prepare(_image, assets):
            events.append("prepare")
            (assets / "model").mkdir()

        def refusal(_image, _assets):
            events.append("auth default")

        with (
            TemporaryDirectory() as directory,
            patch.object(container_smoke, "ROOT", Path(directory)),
            patch("sys.argv", ["container_smoke.py", "test-image"]),
            patch.object(container_smoke, "prepare", prepare),
            patch.object(container_smoke, "default_auth_refusal", refusal),
            patch.object(container_smoke, "failure"),
            patch.object(container_smoke, "serve", side_effect=lambda *_: events.append("serve")),
            patch.object(container_smoke, "retained_builds"),
            patch.object(container_smoke, "docker"),
            redirect_stdout(io.StringIO()),
        ):
            container_smoke.main()
        self.assertEqual(events, ["prepare", "auth default", "serve"])

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
                    patch.object(container_smoke, "default_auth_refusal"),
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


class AuthRefusalEngine:
    def __init__(self, status, message):
        self.status, self.message = status, message
        self.commands = []

    def docker(self, *args, check=True):
        self.commands.append(args)
        result = subprocess.CompletedProcess(args, self.status, "", self.message)
        if check:
            result.check_returncode()
        return result


class DefaultAuthRefusalTest(unittest.TestCase):
    def test_only_nonzero_with_auth_diagnostic_counts_as_refusal(self):
        message = "Required HTTP authentication needs an installed integration"
        for status, diagnostic, accepted in (
            (1, message, True),
            (0, message, False),
            (1, "wrong", False),
        ):
            with self.subTest(status=status, diagnostic=diagnostic):
                engine = AuthRefusalEngine(status, diagnostic)
                with patch.object(container_smoke, "docker", engine.docker):
                    if accepted:
                        self.assert_valid_refusal_passes()
                    else:
                        with self.assertRaisesRegex(
                            RuntimeError,
                            "^Image did not refuse startup without its auth integration$",
                        ):
                            container_smoke.default_auth_refusal("image", Path("assets"))
                self.assert_default_isolated_and_reaped(engine.commands)

    def assert_valid_refusal_passes(self):
        try:
            result = container_smoke.default_auth_refusal("image", Path("assets"))
        except (RuntimeError, subprocess.CalledProcessError) as error:
            self.fail(f"Valid authentication refusal was rejected: {error}")
        self.assertIsNone(result)

    def assert_default_isolated_and_reaped(self, commands):
        self.assertEqual(len(commands), 2, "The refusal container must be reaped")
        command, cleanup = commands
        self.assertIn("--network", command)
        self.assertEqual(command[command.index("--network") + 1], "none")
        environment = [command[i + 1] for i, arg in enumerate(command) if arg == "-e"]
        self.assertFalse(any(value.startswith("NCI_SI_HTTP_AUTH_MODE=") for value in environment))
        self.assertEqual(cleanup, ("rm", "-f", command[command.index("--name") + 1]))

    def test_command_failure_still_reaps_owned_container(self):
        commands = []

        def docker(*args, **_options):
            commands.append(args)
            if args[0] == "run":
                raise TimeoutError("engine timeout")

        with (
            patch.object(container_smoke, "docker", docker),
            self.assertRaisesRegex(TimeoutError, "^engine timeout$"),
        ):
            container_smoke.default_auth_refusal("image", Path("assets"))
        command, cleanup = commands
        self.assertEqual(cleanup, ("rm", "-f", command[command.index("--name") + 1]))


class DockerHealthTest(unittest.TestCase):
    def test_starting_is_polled_until_healthy(self):
        replies = [SimpleNamespace(stdout=status) for status in ("starting", "starting", "healthy")]
        with (
            patch.object(container_smoke, "docker", side_effect=replies) as docker,
            patch.object(processes.time, "monotonic", side_effect=[0, 1, 2, 3]),
            patch.object(processes.time, "sleep") as sleep,
        ):
            try:
                result = container_smoke.wait_healthy("owned")
            except RuntimeError as error:
                self.fail(f"Container became healthy within the startup bound: {error}")
            self.assertIsNone(result)
        self.assertEqual(docker.call_count, 3)
        self.assertEqual(sleep.call_args_list, [((1,),), ((1,),)])

    def test_unhealthy_is_rejected_without_waiting_for_timeout(self):
        with (
            patch.object(
                container_smoke, "docker", return_value=SimpleNamespace(stdout="unhealthy")
            ),
            patch.object(processes.time, "monotonic", side_effect=[0, 1, 181]),
            patch.object(processes.time, "sleep") as sleep,
            self.assertRaisesRegex(RuntimeError, "^Docker health is unhealthy, not healthy$"),
        ):
            container_smoke.wait_healthy("owned")
        sleep.assert_not_called()

    def test_starting_forever_reports_timeout(self):
        with (
            patch.object(
                container_smoke, "docker", return_value=SimpleNamespace(stdout="starting")
            ) as docker,
            patch.object(processes.time, "monotonic", side_effect=[0, 1, 181]),
            patch.object(processes.time, "sleep"),
            self.assertRaisesRegex(
                RuntimeError, "^Docker health did not become healthy within 180 seconds$"
            ),
        ):
            container_smoke.wait_healthy("owned")
        self.assertEqual(docker.call_count, 2)  # the last probe falls at the deadline
