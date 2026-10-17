import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

from nci_si_mcp import installer, installer_state


class LocalInstallerTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data_dir = Path(directory.name)

    @staticmethod
    def passed_prerequisite(*_):
        return (installer.Prerequisite("ready", "Repair it.", lambda: True),)

    @staticmethod
    def run_main(*arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = installer.main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def write_distribution(self, version="0.21.0", revision="v0.21.0"):
        site = self.data_dir / "venv" / "lib" / "python3.14" / "site-packages"
        dist = site / "nci_si_mcp-0.21.0.dist-info"
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "METADATA").write_text(
            f"Metadata-Version: 2.4\nName: nci-si-mcp\nVersion: {version}\n",
            encoding="utf-8",
        )
        (dist / "direct_url.json").write_text(
            json.dumps(
                {
                    "url": "https://github.com/CBIIT/nci-si-mcp",
                    "vcs_info": {"vcs": "git", "requested_revision": revision},
                }
            ),
            encoding="utf-8",
        )
        executable = self.data_dir / "venv" / "bin" / "python"
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.touch()

    def test_install_and_verify_use_only_the_installers_exact_release_tag(self):
        calls = []

        def command(argv, **kwargs):
            calls.append((argv, kwargs))
            if argv[1:3] == ["-m", "venv"]:
                self.write_distribution()
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        with (
            patch.object(installer, "__version__", "0.21.0", create=True),
            patch.object(installer, "local_prerequisites", side_effect=self.passed_prerequisite),
            patch.object(installer.subprocess, "run", side_effect=command),
        ):
            code, output, error = self.run_main(
                "run", "--target", "local", "--data-dir", str(self.data_dir), "--non-interactive"
            )

        venv_python = self.data_dir / "venv" / "bin" / "python"
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(
            json.loads(output)["completed"],
            ["check_prerequisites", "install", "configure", "verify"],
        )
        self.assertIn(
            (
                [
                    str(venv_python),
                    "-m",
                    "pip",
                    "install",
                    "nci-si-mcp[server] @ git+https://github.com/CBIIT/nci-si-mcp@v0.21.0",
                ],
                {"check": True},
            ),
            calls,
        )
        self.assertTrue(
            any(argv[:2] == [str(venv_python), "-c"] for argv, _ in calls),
            "stdio verification must initialize the installed MCP server",
        )

    def test_development_installer_is_blocked_without_running_an_install_command(self):
        with (
            patch.object(installer, "__version__", "0.21.1.dev3+gabc", create=True),
            patch.object(installer, "local_prerequisites", side_effect=self.passed_prerequisite),
            patch.object(installer.subprocess, "run") as run,
        ):
            code, output, error = self.run_main(
                "run", "--target", "local", "--data-dir", str(self.data_dir), "--non-interactive"
            )

        self.assertEqual((code, error), (1, ""))
        self.assertEqual(json.loads(output)["blocked"][0]["step"], "install")
        self.assertIn("released installer", output)
        run.assert_not_called()

    def test_configuration_preserves_profile_and_keeps_each_transport_local(self):
        cases = {
            "stdio": {
                "NCI_SI_DATA_DIR": str(self.data_dir / "server-data"),
                "NCI_SI_PROFILE": "cadsr",
                "NCI_SI_TRANSPORT": "stdio",
            },
            "http": {
                "NCI_SI_DATA_DIR": str(self.data_dir / "server-data"),
                "NCI_SI_PROFILE": "cadsr",
                "NCI_SI_TRANSPORT": "streamable-http",
                "NCI_SI_HTTP_HOST": "127.0.0.1",
                "NCI_SI_HTTP_PORT": "8000",
                "NCI_SI_HTTP_AUTH_MODE": "trusted-local",
                "NCI_SI_HTTP_REQUIRE_INDEX": "0",
            },
        }
        for transport, expected in cases.items():
            with self.subTest(transport=transport):
                self.write_distribution()
                actions = installer.LocalActions(
                    self.data_dir, ("server",), transport=transport, profile="cadsr"
                )
                actions.apply("configure")
                configuration = json.loads(
                    (self.data_dir / "runtime" / "server.json").read_text(encoding="utf-8")
                )
                self.assertEqual(configuration, expected)

    def test_state_identity_includes_transport_and_profile(self):
        state = cast("Any", installer_state.InstallationState)(
            2, "local", ("server",), (), transport="http", profile="evs"
        )
        installer_state.write_state(self.data_dir, state)

        self.assertEqual(
            cast("Any", installer_state.read_state)(
                self.data_dir, "local", ("server",), transport="http", profile="evs"
            ),
            state,
        )
        with self.assertRaisesRegex(installer_state.InstallerStateError, "another selection"):
            cast("Any", installer_state.read_state)(
                self.data_dir, "local", ("server",), transport="stdio", profile="evs"
            )

    def test_stdio_start_emits_the_configured_client_command(self):
        installer_state.write_state(
            self.data_dir,
            installer_state.InstallationState(
                2, "local", ("server",), (), transport="stdio", profile="cadsr"
            ),
        )
        actions = installer.LocalActions(
            self.data_dir, ("server",), transport="stdio", profile="cadsr"
        )
        actions.apply("configure")

        code, output, error = self.run_main(
            "start",
            "--target",
            "local",
            "--transport",
            "stdio",
            "--profile",
            "cadsr",
            "--data-dir",
            str(self.data_dir),
        )

        self.assertEqual((code, error), (0, ""))
        self.assertEqual(
            json.loads(output),
            {
                "command": str(self.data_dir / "venv" / "bin" / "nci-si-mcp"),
                "args": ["serve"],
                "env": {
                    "NCI_SI_DATA_DIR": str(self.data_dir / "server-data"),
                    "NCI_SI_PROFILE": "cadsr",
                    "NCI_SI_TRANSPORT": "stdio",
                },
            },
        )

    def test_http_verification_cleans_up_a_transient_process_and_blocks_an_unowned_port(self):
        self.write_distribution()
        actions = installer.LocalActions(
            self.data_dir, ("server",), transport="http", profile="unified"
        )
        actions.apply("configure")
        process = MagicMock(pid=4321)
        process.poll.return_value = None
        response = MagicMock(status=200)
        response.__enter__.return_value = response
        with (
            patch.object(installer, "_port_available", return_value=True),
            patch.object(installer.subprocess, "Popen", return_value=process),
            patch.object(installer, "urlopen", return_value=response),
        ):
            self.assertTrue(actions.verify("verify"))
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=30)
        self.assertFalse((self.data_dir / "runtime" / "server-process.json").exists())

        with patch.object(installer, "_port_available", return_value=False):
            missing = actions.missing_inputs("verify")
        self.assertEqual(missing, ("Free loopback port 8000; an unowned process holds it.",))

    def test_http_verification_pins_port_8000_despite_the_callers_environment(self):
        self.write_distribution()
        actions = installer.LocalActions(
            self.data_dir, ("server",), transport="http", profile="unified"
        )
        actions.apply("configure")
        process = MagicMock(pid=4321)
        process.poll.return_value = None
        response = MagicMock(status=200)
        response.__enter__.return_value = response
        with (
            patch.dict(installer.os.environ, {"NCI_SI_HTTP_PORT": "9000"}),
            patch.object(installer, "_port_available", return_value=True) as available,
            patch.object(installer.subprocess, "Popen", return_value=process) as start,
            patch.object(installer, "urlopen", return_value=response) as opened,
        ):
            self.assertTrue(actions.verify("verify"))

        available.assert_called_with(8000)
        self.assertEqual(start.call_args.kwargs["env"]["NCI_SI_HTTP_PORT"], "8000")
        opened.assert_called_with("http://127.0.0.1:8000/ready", timeout=5)

    def test_http_verification_kills_a_transient_process_that_ignores_sigterm(self):
        self.write_distribution()
        actions = installer.LocalActions(
            self.data_dir, ("server",), transport="http", profile="unified"
        )
        actions.apply("configure")
        process = MagicMock(pid=4321)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired(["server"], 30), 0]
        response = MagicMock(status=200)
        response.__enter__.return_value = response
        with (
            patch.object(installer, "_port_available", return_value=True),
            patch.object(installer.subprocess, "Popen", return_value=process),
            patch.object(installer, "urlopen", return_value=response),
        ):
            self.assertTrue(actions.verify("verify"))

        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)
        self.assertFalse((self.data_dir / "runtime" / "server-process.json").exists())

    def test_uninstall_removes_only_owned_children_and_then_the_state(self):
        outside = self.data_dir / "unowned"
        outside.write_text("keep", encoding="utf-8")
        for child in ("venv", "server-data", "runtime"):
            path = self.data_dir / child
            path.mkdir()
            (path / "owned").write_text("x", encoding="utf-8")
        installer_state.write_state(
            self.data_dir, installer_state.InstallationState(2, "local", ("server",), ())
        )

        code, output, error = self.run_main(
            "uninstall", "--target", "local", "--data-dir", str(self.data_dir)
        )

        self.assertEqual((code, error), (0, ""))
        self.assertEqual(json.loads(output), {"status": "uninstalled"})
        self.assertTrue(outside.is_file())
        self.assertFalse((self.data_dir / "installer-state.json").exists())
        for child in ("venv", "server-data", "runtime"):
            self.assertFalse((self.data_dir / child).exists())

        code, output, error = self.run_main(
            "status", "--target", "local", "--data-dir", str(self.data_dir)
        )
        self.assertEqual((code, output), (2, ""))
        self.assertIn("no installation recorded", error)

    def test_uninstall_refuses_a_running_owned_server_and_preserves_state(self):
        installer_state.write_state(
            self.data_dir,
            installer_state.InstallationState(2, "local", ("server",), (), transport="http"),
        )
        runtime = self.data_dir / "runtime"
        runtime.mkdir()
        (runtime / "server-process.json").write_text(
            '{"pid":4321,"token":"owned-token"}\n', encoding="utf-8"
        )

        with patch.object(installer, "_owned_process", return_value=4321):
            code, output, error = self.run_main(
                "uninstall",
                "--target",
                "local",
                "--transport",
                "http",
                "--data-dir",
                str(self.data_dir),
            )

        self.assertEqual((code, output), (1, ""))
        self.assertIn("BLOCKED", error)
        self.assertIn("stop", error)
        self.assertTrue((self.data_dir / "installer-state.json").is_file())
