import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, call, patch

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
        (self.data_dir / "venv" / ".installer-owned").touch()

    def write_completed_state(self, *, transport="stdio", profile="unified"):
        installer_state.write_state(
            self.data_dir,
            installer_state.InstallationState(
                2,
                "local",
                ("server",),
                tuple(
                    installer_state.StepOutcome(step, "completed")
                    for step in ("check_prerequisites", "install", "configure", "verify")
                ),
                transport=transport,
                profile=profile,
            ),
        )

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
        verify = next(kwargs for argv, kwargs in calls if argv[:2] == [str(venv_python), "-c"])
        self.assertEqual(verify["env"]["NCI_SI_DATA_DIR"], str(self.data_dir / "server-data"))

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
        self.write_distribution()
        self.write_completed_state(profile="cadsr")
        actions = installer.LocalActions(
            self.data_dir, ("server",), transport="stdio", profile="cadsr"
        )
        actions.apply("configure")

        with (
            patch.object(installer, "__version__", "0.21.0"),
            patch.object(installer, "_verify_stdio", return_value=True),
        ):
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

    def test_start_refuses_an_incomplete_installation(self):
        installer_state.write_state(
            self.data_dir, installer_state.InstallationState(2, "local", ("server",), ())
        )
        code, output, error = self.run_main(
            "start", "--target", "local", "--data-dir", str(self.data_dir)
        )
        self.assertEqual((code, output), (2, ""))
        self.assertIn("complete `run`", error)

    def test_wrong_installed_revision_fails_reverification(self):
        self.write_distribution(revision="v0.20.0")
        actions = installer.LocalActions(
            self.data_dir, ("server",), transport="stdio", profile="unified"
        )
        self.assertFalse(actions.verify("install"))

    def test_unverifiable_process_ownership_blocks_stop_and_uninstall(self):
        self.write_completed_state(transport="http")
        runtime = self.data_dir / "runtime"
        runtime.mkdir()
        (runtime / "server-process.json").write_text(
            '{"pid":4321,"token":"owned"}\n', encoding="utf-8"
        )
        observed = subprocess.CompletedProcess(["ps"], 0, stdout="other process")
        with patch.object(installer.subprocess, "run", return_value=observed):
            stopped = self.run_main(
                "stop", "--target", "local", "--transport", "http", "--data-dir", str(self.data_dir)
            )
            removed = self.run_main(
                "uninstall",
                "--target",
                "local",
                "--transport",
                "http",
                "--data-dir",
                str(self.data_dir),
            )
        self.assertEqual(stopped[0], 2)
        self.assertEqual(removed[0], 2)
        self.assertIn("ownership", stopped[2])
        self.assertTrue((self.data_dir / "installer-state.json").is_file())

    def test_dead_record_is_removed_but_weak_ownership_records_fail_closed(self):
        runtime = self.data_dir / "runtime"
        runtime.mkdir()
        record = runtime / "server-process.json"
        token = "a" * 32
        record.write_text(json.dumps({"pid": 4321, "token": token}) + "\n")
        dead = subprocess.CompletedProcess(["ps"], 1, stdout="")
        with patch.object(installer.subprocess, "run", return_value=dead):
            self.assertIsNone(installer._owned_process(runtime))  # pyright: ignore[reportPrivateUsage]
        self.assertFalse(record.exists())
        for value in ({"pid": True, "token": token}, {"pid": 2, "token": "short"}):
            record.write_text(json.dumps(value))
            with self.assertRaisesRegex(installer.InstallerStateError, "ownership"):
                installer._owned_process(runtime)  # pyright: ignore[reportPrivateUsage]

    def test_http_start_failure_reaps_child_and_removes_record(self):
        self.write_distribution()
        self.write_completed_state(transport="http")
        installer.LocalActions(
            self.data_dir, ("server",), transport="http", profile="unified"
        ).apply("configure")
        process = MagicMock(pid=4321)
        with (
            patch.object(installer, "__version__", "0.21.0"),
            patch.object(installer, "_owned_process", return_value=None),
            patch.object(installer, "_port_available", return_value=True),
            patch.object(installer.subprocess, "Popen", return_value=process),
            patch.object(installer, "_poll_ready", return_value=False),
        ):
            code, output, error = self.run_main(
                "start",
                "--target",
                "local",
                "--transport",
                "http",
                "--data-dir",
                str(self.data_dir),
            )
        self.assertEqual((code, output), (2, ""))
        self.assertIn("did not become ready", error)
        process.terminate.assert_called_once_with()
        self.assertFalse((self.data_dir / "runtime" / "server-process.json").exists())

    def test_http_start_and_stop_manage_only_the_recorded_process(self):
        self.write_distribution()
        self.write_completed_state(transport="http")
        actions = installer.LocalActions(
            self.data_dir, ("server",), transport="http", profile="unified"
        )
        actions.apply("configure")
        process = MagicMock(pid=4321)
        process.poll.return_value = None
        response = MagicMock(status=200)
        response.__enter__.return_value = response
        with (
            patch.object(installer, "__version__", "0.21.0"),
            patch.object(installer, "_owned_process", side_effect=[None, 4321]),
            patch.object(installer, "_port_available", return_value=True),
            patch.object(installer.subprocess, "Popen", return_value=process) as popen,
            patch.object(installer, "urlopen", return_value=response),
            patch.object(installer.os, "kill", side_effect=[None, ProcessLookupError]) as kill,
        ):
            started = self.run_main(
                "start",
                "--target",
                "local",
                "--transport",
                "http",
                "--data-dir",
                str(self.data_dir),
            )
            stopped = self.run_main(
                "stop", "--target", "local", "--transport", "http", "--data-dir", str(self.data_dir)
            )

        self.assertEqual(json.loads(started[1]), {"status": "started", "pid": 4321})
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(popen.call_args.kwargs["env"]["NCI_SI_HTTP_PORT"], "8000")
        kill.assert_any_call(4321, installer.signal.SIGTERM)
        self.assertEqual(json.loads(stopped[1]), {"status": "stopped"})
        self.assertFalse((self.data_dir / "runtime" / "server-process.json").exists())

    def test_http_start_reaps_child_when_the_ownership_record_cannot_be_written(self):
        self.write_distribution()
        self.write_completed_state(transport="http")
        actions = installer.LocalActions(
            self.data_dir, ("server",), transport="http", profile="unified"
        )
        actions.apply("configure")
        process = MagicMock(pid=4321)
        with (
            patch.object(installer, "__version__", "0.21.0"),
            patch.object(installer, "_owned_process", return_value=None),
            patch.object(installer, "_port_available", return_value=True),
            patch.object(installer.subprocess, "Popen", return_value=process),
            patch.object(installer, "_write_process_record", side_effect=OSError("full")),
        ):
            code, output, error = self.run_main(
                "start",
                "--target",
                "local",
                "--transport",
                "http",
                "--data-dir",
                str(self.data_dir),
            )
        self.assertEqual((code, output), (2, ""))
        self.assertIn("full", error)
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=30)

    def test_uninstall_refuses_owned_child_symlinks(self):
        self.write_completed_state()
        outside = self.data_dir.parent / f"{self.data_dir.name}-outside"
        outside.mkdir()
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        (self.data_dir / "venv").symlink_to(outside, target_is_directory=True)
        code, output, error = self.run_main(
            "uninstall", "--target", "local", "--data-dir", str(self.data_dir)
        )
        self.assertEqual((code, output), (2, ""))
        self.assertIn("Refusing unowned path", error)
        self.assertTrue(outside.is_dir())
        self.assertTrue((self.data_dir / "installer-state.json").is_file())

    def test_reserved_children_are_not_adopted_or_partially_deleted(self):
        outside = self.data_dir.parent / f"{self.data_dir.name}-outside"
        outside.mkdir()
        self.addCleanup(outside.rmdir)
        (self.data_dir / "runtime").symlink_to(outside, target_is_directory=True)
        actions = installer.LocalActions(
            self.data_dir, ("server",), transport="stdio", profile="unified"
        )
        with self.assertRaisesRegex(installer.InstallerStateError, "unowned"):
            actions.apply("configure")
        self.assertEqual(list(outside.iterdir()), [])

        (self.data_dir / "runtime").unlink()
        self.write_completed_state()
        for child in ("venv", "server-data"):
            path = self.data_dir / child
            path.mkdir(exist_ok=True)
            (path / ".installer-owned").touch()
            (path / "keep").touch()
        (self.data_dir / "runtime").symlink_to(outside, target_is_directory=True)
        code, _, _ = self.run_main(
            "uninstall", "--target", "local", "--data-dir", str(self.data_dir)
        )
        self.assertEqual(code, 2)
        self.assertTrue((self.data_dir / "venv" / "keep").exists())

    def test_process_record_fsyncs_its_directory_and_http_keeps_diagnostics(self):
        runtime = self.data_dir / "runtime"
        runtime.mkdir()
        descriptors = []
        real_open = installer.os.open

        def open_path(*args):
            descriptor = real_open(*args)
            descriptors.append(descriptor)
            return descriptor

        with (
            patch.object(installer.os, "open", side_effect=open_path) as opened,
            patch.object(installer.os, "fsync") as fsync,
            patch.object(installer.os, "close", wraps=installer.os.close) as close,
        ):
            installer._write_process_record(runtime, 42, "a" * 32)  # pyright: ignore[reportPrivateUsage]
        self.assertIn(call(runtime, installer.os.O_RDONLY), opened.mock_calls)
        fsync.assert_any_call(descriptors[-1])
        close.assert_called_once_with(descriptors[-1])
        self.assertEqual(installer._http_log(runtime), runtime / "server.log")

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
            (path / ".installer-owned").touch()
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
        (runtime / ".installer-owned").touch()
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
