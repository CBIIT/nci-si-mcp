"""The remote fixture control channel isolates settings and owns restarted children."""

import json
import os
import socketserver
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace
from unittest.mock import patch

from scripts import acceptance_http


class HTTPControlTest(unittest.TestCase):
    def test_deep_worker_workspace_still_has_a_working_private_control_channel(self):
        settings = []
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / ("nested-" * 20)
            root.mkdir()

            def suite(_port, socket_path, **_options):
                with patch.dict(os.environ, {"NCI_SI_PROFILE": "fixture-profile"}, clear=True):
                    acceptance_http.state(str(socket_path))
                return 0

            process = SimpleNamespace(restart=settings.append, close=lambda: None)
            with (
                patch.object(acceptance_http, "ROOT", root),
                patch.object(acceptance_http, "prepare"),
                patch.object(acceptance_http, "ServerProcess", return_value=process),
                patch.object(acceptance_http, "run_suite", side_effect=suite),
            ):
                result = acceptance_http.run()
            self.assertEqual(result, 0)
            self.assertEqual(settings, [{"NCI_SI_PROFILE": "fixture-profile"}])
            self.assertEqual(list((root / "tmp").iterdir()), [])

    def test_preparation_uses_supplied_environment_without_inherited_secrets(self):
        observed = []

        def prepared(_command, *, env, **_options):
            observed.append(env)

        with (
            TemporaryDirectory() as directory,
            patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": "PRIVATE-CANARY"}),
            patch.object(acceptance_http.subprocess, "run", prepared),
        ):
            acceptance_http.prepare(Path(directory), environment={"HOME": directory})
        self.assertEqual(observed[0]["HOME"], directory)
        self.assertNotIn("PRIVATE-CANARY", str(observed))
        self.assertTrue(observed[0]["NCI_SI_EVS_BASE_URL"].startswith("http://127.0.0.1:"))

    def test_unrecorded_preparation_request_cannot_be_treated_as_prepared(self):
        def prepare_with_an_unrecorded_request(_command, *, env, **_options):
            try:
                url = env["NCI_SI_EVS_BASE_URL"] + "/unrecorded"
                with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 - local fixture
                    response.read()
            except urllib.error.HTTPError as error:
                error.close()

        with (
            TemporaryDirectory() as directory,
            patch.object(acceptance_http.subprocess, "run", prepare_with_an_unrecorded_request),
            self.assertRaisesRegex(RuntimeError, "unrecorded upstream response"),
        ):
            acceptance_http.prepare(Path(directory))

    def test_interrupted_runner_closes_its_log_and_removes_control_directory(self):
        processes = []
        constructor = acceptance_http.ServerProcess

        def opened(directory, port, **options):
            process = constructor(directory, port, **options)
            processes.append(process)
            return process

        with TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(acceptance_http, "ROOT", root),
                patch.object(acceptance_http, "prepare"),
                patch.object(acceptance_http, "ServerProcess", opened),
                patch.object(acceptance_http, "run_suite", side_effect=KeyboardInterrupt),
                self.assertRaises(KeyboardInterrupt),
            ):
                acceptance_http.run()
            self.assertEqual(list((root / "tmp").iterdir()), [])
            self.assertTrue(processes[0].log.closed)
            self.assertIsNone(processes[0].process)

    def test_state_hook_sends_only_server_settings_and_requires_acknowledgement(self):
        with TemporaryDirectory() as directory:
            socket_path = str(Path(directory) / "control")
            settings = []
            process = SimpleNamespace(restart=settings.append)
            handler = acceptance_http.hook_handler(process)
            with socketserver.UnixStreamServer(socket_path, handler) as server:
                server.timeout = 2
                thread = Thread(target=server.handle_request)
                thread.start()
                try:
                    with patch.dict(
                        os.environ,
                        {
                            "NCI_SI_EVS_BASE_URL": "http://fixture.invalid",
                            "NCI_SI_ACCEPTANCE_URL": "http://control.invalid",
                            "UNRELATED": "not a server setting",
                        },
                        clear=True,
                    ):
                        acceptance_http.state(socket_path)
                finally:
                    thread.join(timeout=35)
                self.assertFalse(thread.is_alive())
            self.assertEqual(settings, [{"NCI_SI_EVS_BASE_URL": "http://fixture.invalid"}])

    def test_state_hook_rejects_a_failed_restart(self):
        class FailedRestart(socketserver.StreamRequestHandler):
            def handle(self):
                self.rfile.readline()
                self.wfile.write(b"failed\n")

        with TemporaryDirectory() as directory:
            socket_path = str(Path(directory) / "control")
            with socketserver.UnixStreamServer(socket_path, FailedRestart) as server:
                server.timeout = 2
                thread = Thread(target=server.handle_request)
                thread.start()
                try:
                    with self.assertRaisesRegex(RuntimeError, "restart failed"):
                        acceptance_http.state(socket_path)
                finally:
                    thread.join(timeout=35)
                self.assertFalse(thread.is_alive())

    def test_restart_replaces_owned_child_and_forces_isolated_http_settings(self):
        spawn = subprocess.Popen
        children = []
        script = (
            "import json, os, time; "
            "print(json.dumps({k:v for k,v in os.environ.items() if k.startswith('NCI_SI_')}), "
            "file=__import__('sys').stderr, flush=True); time.sleep(300)"
        )

        def launch(_command, **options):
            child = spawn([sys.executable, "-c", script], **options)
            children.append(child)
            deadline = time.monotonic() + 5
            while len((root / "server.log").read_text().splitlines()) < len(children):
                if time.monotonic() > deadline:
                    raise TimeoutError("Owned child did not write its settings")
                time.sleep(0.01)
            return child

        with TemporaryDirectory() as directory:
            root = Path(directory)
            process = acceptance_http.ServerProcess(root, 8123)
            try:
                with (
                    patch.dict(os.environ, {"NCI_SI_PROFILE": "inherited-profile"}),
                    patch.object(acceptance_http.subprocess, "Popen", launch),
                ):
                    process.restart({"NCI_SI_EVS_BASE_URL": "http://first.invalid"})
                    process.restart({"NCI_SI_EVS_BASE_URL": "http://second.invalid"})
                process.close()
                statuses = [child.poll() for child in children]
            finally:
                process.close()
                for child in children:
                    if child.poll() is None:
                        child.kill()
                        child.wait(timeout=5)
            observed = [json.loads(line) for line in (root / "server.log").read_text().splitlines()]
            forced = {
                "NCI_SI_DATA_DIR": str(root / "index"),
                "NCI_SI_TRANSPORT": "streamable-http",
                "NCI_SI_HTTP_PORT": "8123",
                "NCI_SI_HTTP_SESSIONS": "stateful",
                "NCI_SI_HTTP_REQUIRE_INDEX": "1",
            }
            self.assertEqual(
                observed,
                [
                    forced | {"NCI_SI_EVS_BASE_URL": f"http://{name}.invalid"}
                    for name in ("first", "second")
                ],
            )
            self.assertEqual(statuses, [-15, -15])
            self.assertIsNone(process.process)
            self.assertTrue(process.log.closed)
