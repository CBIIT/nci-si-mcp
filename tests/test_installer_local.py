import io
import json
import shutil
import subprocess
import sys
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
        for child in ("venv", "server-data", "runtime"):
            self.assertTrue((self.data_dir / child / ".installer-owned").is_file())
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
                    self.data_dir, transport=transport, profile="cadsr"
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
        actions = installer.LocalActions(self.data_dir, transport="stdio", profile="cadsr")
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

    def test_start_refuses_partial_and_stale_completed_installations(self):
        cases = (
            (("check_prerequisites", "install"), "incomplete"),
            (("check_prerequisites", "install", "configure", "verify"), "stale"),
        )
        for steps, message in cases:
            with self.subTest(steps=steps), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                installer_state.write_state(
                    root,
                    installer_state.InstallationState(
                        2,
                        "local",
                        ("server",),
                        tuple(installer_state.StepOutcome(step, "completed") for step in steps),
                    ),
                )
                code, output, error = self.run_main(
                    "start", "--target", "local", "--data-dir", str(root)
                )
            self.assertEqual((code, output), (2, ""))
            self.assertIn(message, error.lower())

    def test_start_refuses_each_independent_failed_precondition(self):
        self.write_distribution()
        self.write_completed_state()
        actions = installer.LocalActions(self.data_dir, transport="stdio", profile="unified")
        actions.apply("configure")
        for failed in ("install", "configure", "verify"):
            with (
                self.subTest(failed=failed),
                patch.object(
                    installer.LocalActions,
                    "verify",
                    autospec=True,
                    side_effect=lambda _self, step, failed=failed: step != failed,
                ),
            ):
                code, output, error = self.run_main(
                    "start", "--target", "local", "--data-dir", str(self.data_dir)
                )
            self.assertEqual((code, output), (2, ""))
            self.assertIn("stale", error)
        for status in ("failed", "blocked"):
            outcomes = tuple(
                installer_state.StepOutcome(
                    step,
                    status if step == "verify" else "completed",
                    ("fix",) if status == "blocked" and step == "verify" else (),
                )
                for step in ("check_prerequisites", "install", "configure", "verify")
            )
            installer_state.write_state(
                self.data_dir,
                installer_state.InstallationState(2, "local", ("server",), outcomes),
            )
            code, output, error = self.run_main(
                "start", "--target", "local", "--data-dir", str(self.data_dir)
            )
            self.assertEqual((code, output), (2, ""))
            self.assertIn("incomplete", error)

    def test_wrong_installed_revision_fails_reverification(self):
        self.write_distribution(revision="v0.20.0")
        actions = installer.LocalActions(self.data_dir, transport="stdio", profile="unified")
        self.assertFalse(actions.verify("install"))

    def test_release_and_configuration_verifiers_fail_closed(self):
        self.assertTrue(installer.RELEASE_VERSION.pattern.endswith(r"\Z"))
        actions = installer.LocalActions(self.data_dir, transport="stdio", profile="unified")
        cases = (
            ("0.21.0.dev1", "v0.21.0", False),
            ("0.21.0", "v0.20.0", False),
            ("0.21.0", None, False),
        )
        for version, revision, expected in cases:
            with self.subTest(version=version, revision=revision):
                self.write_distribution(version, revision or "v0.21.0")
                direct = next(
                    (self.data_dir / "venv").glob("lib/python*/site-packages/*/direct_url.json")
                )
                if revision is None:
                    direct.write_text('{"vcs_info":{}}')
                with patch.object(installer, "__version__", "0.21.0"):
                    self.assertEqual(actions.verify("install"), expected)
                shutil.rmtree(self.data_dir / "venv")
        self.write_distribution()
        original = self.data_dir / "venv/lib/python3.14/site-packages/nci_si_mcp-0.21.0.dist-info"
        duplicate = (
            self.data_dir / "venv/lib/python3.14/site-packages/nci_si_mcp-duplicate.dist-info"
        )
        duplicate.mkdir()
        for name in ("METADATA", "direct_url.json"):
            shutil.copy2(original / name, duplicate / name)
        with patch.object(installer, "__version__", "0.21.0"):
            self.assertFalse(actions.verify("install"))
        actions.apply("configure")
        (self.data_dir / "runtime/server.json").write_text("{}")
        self.assertFalse(actions.verify("configure"))
        (self.data_dir / "runtime/server.json").write_text('{"valid":"x","invalid":1}')
        self.assertIsNone(installer._configuration(self.data_dir))

    def test_stdio_and_http_verification_honor_results_and_environment(self):
        environment = {"NCI_SI_PROFILE": "cadsr"}
        for status, expected in ((0, True), (1, False)):
            with (
                self.subTest(status=status),
                patch.dict(
                    installer.os.environ, {"PATH": "/bin", "HOME": "/home/test"}, clear=True
                ),
                patch.object(
                    installer.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], status),
                ) as run,
            ):
                self.assertEqual(
                    installer._verify_stdio(Path("/venv/python"), environment), expected
                )
                self.assertEqual(
                    run.call_args.kwargs["env"],
                    {"PATH": "/bin", "HOME": "/home/test", **environment},
                )
        response = MagicMock()
        response.__enter__.return_value = response
        for status, expected in ((200, True), (201, False)):
            response.status = status
            with patch.object(installer, "urlopen", return_value=response):
                self.assertEqual(installer._ready(), expected)

    def test_stdio_verifier_initializes_real_server_through_real_sdk(self):
        environment = {
            "NCI_SI_DATA_DIR": str(self.data_dir / "server-data"),
            "NCI_SI_PROFILE": "unified",
            "NCI_SI_TRANSPORT": "stdio",
        }
        self.assertTrue(installer._verify_stdio(Path(sys.executable), environment))

    def test_stdio_verifier_passes_configured_environment_to_real_server(self):
        environment = {
            "NCI_SI_DATA_DIR": str(self.data_dir / "server-data"),
            "NCI_SI_PROFILE": "invalid-profile",
            "NCI_SI_TRANSPORT": "stdio",
        }
        self.assertFalse(installer._verify_stdio(Path(sys.executable), environment))

    def test_stdio_verify_requires_install_and_configuration(self):
        actions = installer.LocalActions(self.data_dir, transport="stdio", profile="unified")
        for failed in ("install", "configure"):
            with (
                self.subTest(failed=failed),
                patch.object(
                    installer.LocalActions,
                    "verify",
                    autospec=True,
                    side_effect=lambda _self, step, failed=failed: step != failed,
                ),
                patch.object(installer, "_verify_stdio", return_value=True),
            ):
                self.assertFalse(actions._verify_server())

    def test_http_start_and_stop_refusals_are_exact(self):
        actions = installer.LocalActions(self.data_dir, transport="http", profile="unified")
        actions.apply("configure")
        with (
            patch.object(installer, "_owned_process", return_value=None),
            patch.object(installer, "_port_available", return_value=False),
            self.assertRaisesRegex(installer.InstallerStateError, "held by an unowned"),
        ):
            installer._start_http(actions)
        with (
            patch.object(installer, "_owned_process", return_value=42),
            patch.object(installer, "_ready", return_value=False),
            self.assertRaisesRegex(installer.InstallerStateError, "not ready"),
        ):
            installer._start_http(actions)
        with patch.object(installer, "_owned_process", return_value=None):
            self.assertEqual(installer._stop_http(actions), {"status": "stopped"})
        with (
            patch.object(installer, "_owned_process", return_value=42),
            patch.object(installer, "_ready", return_value=False),
        ):
            self.assertFalse(
                installer._verify_http(actions.python, actions.runtime, actions.environment())
            )

    def test_stdio_stop_is_refused_by_the_cli(self):
        self.write_completed_state()
        code, output, error = self.run_main(
            "stop", "--target", "local", "--data-dir", str(self.data_dir)
        )
        self.assertEqual((code, output), (2, ""))
        self.assertIn("no process to stop", error)

    def test_lifecycle_refuses_semantically_invalid_outcomes(self):
        path = self.data_dir / "installer-state.json"
        self.data_dir.mkdir(exist_ok=True)
        path.write_text(
            '{"version":2,"target":"local","components":["server"],'
            '"transport":"stdio","profile":"unified",'
            '"outcomes":[{"step":"unknown","status":"completed"}]}\n'
        )
        code, output, error = self.run_main(
            "start", "--target", "local", "--data-dir", str(self.data_dir)
        )
        self.assertEqual((code, output), (2, ""))
        self.assertIn("malformed", error)

    def test_stop_escalates_and_never_reports_a_live_process_stopped(self):
        with (
            patch.object(
                installer.os, "kill", side_effect=[None] * 30 + [None] + [None] * 30
            ) as kill,
            self.assertRaisesRegex(installer.InstallerStateError, "Cannot stop"),
        ):
            installer._wait_for_pid(42)
        self.assertIn(call(42, installer.signal.SIGKILL), kill.mock_calls)
        self.assertNotEqual(kill.mock_calls[-1], call(42, installer.signal.SIGTERM))

    def test_process_identity_comparison_and_absence_checks_are_safe(self):
        runtime = self.data_dir / "runtime"
        runtime.mkdir()
        record = runtime / "server-process.json"
        record.write_text(json.dumps({"pid": 42, "identity": "Mon Jan 1 00:00:00 2026"}))
        seen = subprocess.CompletedProcess([], 0, stdout="Tue Jan 2 00:00:00 2026")
        with (
            patch.object(installer.subprocess, "run", return_value=seen),
            self.assertRaisesRegex(installer.InstallerStateError, "ownership"),
        ):
            installer._owned_process(runtime)
        record.write_text(json.dumps({"pid": 42, "identity": "same identity"}))
        missing = subprocess.CompletedProcess([], 1, stdout="")
        with (
            patch.object(installer.subprocess, "run", return_value=missing),
            patch.object(installer.os, "kill", side_effect=PermissionError) as kill,
            self.assertRaisesRegex(installer.InstallerStateError, "ownership"),
        ):
            installer._owned_process(runtime)
        kill.assert_called_once_with(42, 0)
        self.assertTrue(record.exists())
        self.assertEqual(installer._normalized_identity(" Mon  Jan  1 "), "Mon Jan 1")

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
        identity = "Sun Oct 18 19:59:57 2026 /owned/server"
        record.write_text(json.dumps({"pid": 4321, "identity": identity}) + "\n")
        dead = subprocess.CompletedProcess(["ps"], 1, stdout="")
        with patch.object(installer.subprocess, "run", return_value=dead):
            self.assertIsNone(installer._owned_process(runtime))  # pyright: ignore[reportPrivateUsage]
        self.assertFalse(record.exists())
        for value in ({"pid": True, "identity": identity}, {"pid": 2, "identity": ""}):
            record.write_text(json.dumps(value))
            with self.assertRaisesRegex(installer.InstallerStateError, "ownership"):
                installer._owned_process(runtime)  # pyright: ignore[reportPrivateUsage]

    def test_supported_platform_observes_a_real_owned_process(self):
        runtime = self.data_dir / "runtime"
        runtime.mkdir()
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            identity = installer._process_identity(process.pid)  # pyright: ignore[reportPrivateUsage]
            installer._write_process_record(runtime, process.pid, identity)  # pyright: ignore[reportPrivateUsage]
            self.assertEqual(installer._owned_process(runtime), process.pid)  # pyright: ignore[reportPrivateUsage]
        finally:
            process.terminate()
            process.wait(timeout=30)

    def test_http_start_failure_reaps_child_and_removes_record(self):
        self.write_distribution()
        self.write_completed_state(transport="http")
        installer.LocalActions(self.data_dir, transport="http", profile="unified").apply(
            "configure"
        )
        process = MagicMock(pid=4321)
        with (
            patch.object(installer, "__version__", "0.21.0"),
            patch.object(installer, "_owned_process", return_value=None),
            patch.object(installer, "_port_available", return_value=True),
            patch.object(installer, "_process_identity", return_value="identity"),
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
        actions = installer.LocalActions(self.data_dir, transport="http", profile="unified")
        actions.apply("configure")
        process = MagicMock(pid=4321)
        process.poll.return_value = None
        response = MagicMock(status=200)
        response.__enter__.return_value = response
        (self.data_dir / "runtime/server.log").write_text("before\n")
        with (
            patch.object(installer, "__version__", "0.21.0"),
            patch.object(installer, "_owned_process", side_effect=[None, 4321]),
            patch.object(installer, "_port_available", return_value=True),
            patch.object(installer, "_process_identity", return_value="identity"),
            patch.object(installer.subprocess, "Popen", return_value=process) as popen,
            patch.object(installer, "urlopen", return_value=response),
            patch.object(installer.os, "kill", side_effect=[None, ProcessLookupError]) as kill,
            patch.dict(installer.os.environ, {"PATH": "/bin", "HOME": "/home/test"}, clear=True),
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
        self.assertEqual(popen.call_args.kwargs["env"]["PATH"], "/bin")
        self.assertEqual(
            Path(popen.call_args.kwargs["stderr"].name), self.data_dir / "runtime/server.log"
        )
        kill.assert_any_call(4321, installer.signal.SIGTERM)
        self.assertEqual(json.loads(stopped[1]), {"status": "stopped"})
        self.assertFalse((self.data_dir / "runtime" / "server-process.json").exists())
        self.assertEqual((self.data_dir / "runtime/server.log").read_text(), "before\n")

    def test_second_http_start_appends_to_the_existing_log(self):
        self.write_distribution()
        self.write_completed_state(transport="http")
        actions = installer.LocalActions(self.data_dir, transport="http", profile="unified")
        actions.apply("configure")
        log = self.data_dir / "runtime/server.log"
        log.write_text("first\n")
        process = MagicMock(pid=42)
        with (
            patch.object(installer, "__version__", "0.21.0"),
            patch.object(installer, "_owned_process", return_value=None),
            patch.object(installer, "_port_available", return_value=True),
            patch.object(installer, "_process_identity", return_value="identity"),
            patch.object(installer, "_write_process_record"),
            patch.object(installer, "_poll_ready", return_value=True),
            patch.object(installer.subprocess, "Popen", return_value=process) as popen,
        ):
            installer._start_http(actions)
        self.assertEqual(log.read_text(), "first\n")
        self.assertEqual(popen.call_args.kwargs["stderr"].mode, "ab")

    def test_http_start_reaps_child_when_the_ownership_record_cannot_be_written(self):
        self.write_distribution()
        self.write_completed_state(transport="http")
        actions = installer.LocalActions(self.data_dir, transport="http", profile="unified")
        actions.apply("configure")
        process = MagicMock(pid=4321)
        with (
            patch.object(installer, "__version__", "0.21.0"),
            patch.object(installer, "_owned_process", return_value=None),
            patch.object(installer, "_port_available", return_value=True),
            patch.object(installer, "_process_identity", return_value="identity"),
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
        for child in ("venv", "server-data", "runtime"):
            for kind in ("directory", "symlink"):
                with (
                    self.subTest(child=child, kind=kind),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    installer_state.write_state(
                        root, installer_state.InstallationState(2, "local", ("server",), ())
                    )
                    outside = root / "outside"
                    outside.mkdir()
                    path = root / child
                    if kind == "directory":
                        path.mkdir()
                    else:
                        (outside / ".installer-owned").touch()
                        path.symlink_to(outside, target_is_directory=True)
                    code, output, error = self.run_main(
                        "uninstall", "--target", "local", "--data-dir", str(root)
                    )
                    self.assertEqual((code, output), (2, ""))
                    self.assertIn("Refusing unowned path", error)
                    self.assertTrue(path.exists())

    def test_claim_refuses_each_existing_unowned_child(self):
        for child, step in (
            ("venv", "install"),
            ("server-data", "configure"),
            ("runtime", "configure"),
        ):
            for kind in ("directory", "symlink"):
                with (
                    self.subTest(child=child, kind=kind),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    outside = root / "outside"
                    outside.mkdir()
                    path = root / child
                    if kind == "directory":
                        path.mkdir()
                    else:
                        (outside / ".installer-owned").touch()
                        path.symlink_to(outside, target_is_directory=True)
                    actions = installer.LocalActions(root, transport="stdio", profile="unified")
                    with self.assertRaisesRegex(installer.InstallerStateError, "unowned"):
                        actions.apply(step)

    def test_uninstall_failure_modes_preserve_state_or_report_the_os_error(self):
        self.write_completed_state()
        for child in ("venv", "server-data", "runtime"):
            path = self.data_dir / child
            path.mkdir()
            (path / ".installer-owned").touch()
        state_path = self.data_dir / "installer-state.json"
        state_path.write_text(
            '{"version":2,"target":"local","components":["server"],'
            '"transport":"stdio","profile":"unified",'
            '"outcomes":[{"step":"unknown","status":"completed"}]}\n'
        )
        code, _, error = self.run_main(
            "uninstall", "--target", "local", "--data-dir", str(self.data_dir)
        )
        self.assertEqual(code, 2)
        self.assertIn("malformed", error)
        self.assertTrue(state_path.exists())
        self.write_completed_state()
        with patch.object(
            installer.shutil, "rmtree", side_effect=OSError("cannot remove")
        ) as remove:
            code, _, error = self.run_main(
                "uninstall", "--target", "local", "--data-dir", str(self.data_dir)
            )
        self.assertEqual(code, 2)
        self.assertIn("cannot remove", error)
        self.assertTrue(state_path.exists())
        self.assertFalse(remove.call_args.kwargs["ignore_errors"])

    def test_reserved_children_are_not_adopted_or_partially_deleted(self):
        outside = self.data_dir.parent / f"{self.data_dir.name}-outside"
        outside.mkdir()
        self.addCleanup(outside.rmdir)
        (self.data_dir / "runtime").symlink_to(outside, target_is_directory=True)
        actions = installer.LocalActions(self.data_dir, transport="stdio", profile="unified")
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

    def test_process_record_is_atomic_and_http_keeps_diagnostics(self):
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
            installer._write_process_record(runtime, 42, "identity")  # pyright: ignore[reportPrivateUsage]
        self.assertIn(call(runtime, installer.os.O_RDONLY), opened.mock_calls)
        fsync.assert_any_call(descriptors[-1])
        close.assert_called_once_with(descriptors[-1])
        self.assertEqual(installer._http_log(runtime), runtime / "server.log")

    def test_http_verification_cleans_up_a_transient_process_and_blocks_an_unowned_port(self):
        self.write_distribution()
        actions = installer.LocalActions(self.data_dir, transport="http", profile="unified")
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

    def test_verify_http_appends_diagnostics_and_preserves_inherited_environment(self):
        self.write_distribution()
        actions = installer.LocalActions(self.data_dir, transport="http", profile="unified")
        actions.apply("configure")
        log = self.data_dir / "runtime/server.log"
        log.write_text("before\n")
        process = MagicMock(pid=42)
        process.poll.return_value = None
        with (
            patch.dict(installer.os.environ, {"PATH": "/bin", "HOME": "/home/test"}, clear=True),
            patch.object(installer, "_owned_process", return_value=None),
            patch.object(installer.subprocess, "Popen", return_value=process) as popen,
            patch.object(installer, "_poll_ready", return_value=True),
        ):
            self.assertTrue(actions.verify("verify"))
        self.assertEqual(log.read_text(), "before\n")
        self.assertEqual(Path(popen.call_args.kwargs["stderr"].name), log)
        self.assertEqual(popen.call_args.kwargs["env"]["PATH"], "/bin")
        self.assertEqual(popen.call_args.kwargs["env"]["NCI_SI_HTTP_PORT"], "8000")

    def test_http_verification_pins_port_8000_despite_the_callers_environment(self):
        self.write_distribution()
        actions = installer.LocalActions(self.data_dir, transport="http", profile="unified")
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
        actions = installer.LocalActions(self.data_dir, transport="http", profile="unified")
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
