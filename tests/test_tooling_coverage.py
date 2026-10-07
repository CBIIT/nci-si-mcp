"""Release smoke checks must reject broken images, not merely execute their probes."""

import asyncio
import io
import json
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from scripts import benchmark, container_smoke


class SmokeProbeTest(unittest.TestCase):
    def test_preparation_requires_success_and_json_diagnostics(self):
        for status, stderr, error in (
            (1, "failed to prepare", RuntimeError),
            (0, "not JSON", json.JSONDecodeError),
        ):
            with (
                self.subTest(status=status),
                patch.object(
                    container_smoke,
                    "docker",
                    return_value=SimpleNamespace(returncode=status, stderr=stderr),
                ),
                self.assertRaises(error),
            ):
                container_smoke.prepare("image", Path("assets"))

    def test_missing_assets_require_one_matching_failure_and_exit_one(self):
        failure = {"event": "container_startup_failed", "asset": "index"}
        cases = [
            (0, [failure]),
            (2, [failure]),
            (1, []),
            (1, [failure, failure]),
            (1, [failure | {"asset": "model"}]),
            (1, [failure | {"event": "unexpected_failure"}]),
        ]
        for status, records in cases:
            with (
                self.subTest(status=status, records=records),
                patch.object(
                    container_smoke,
                    "docker",
                    return_value=SimpleNamespace(
                        returncode=status, stderr="\n".join(map(json.dumps, records))
                    ),
                ),
                self.assertRaisesRegex(RuntimeError, "Expected one index startup error"),
            ):
                container_smoke.failure("image", Path("assets"), "index")

    def test_readiness_exhaustion_fails_even_after_transient_network_errors(self):
        for response in ({"status": "starting"}, urllib.error.URLError("not yet listening")):
            with (
                self.subTest(response=response),
                patch.object(container_smoke.time, "monotonic", side_effect=[0, 1, 181]),
                patch.object(container_smoke.time, "sleep"),
                patch.object(container_smoke, "request", side_effect=[response]),
                self.assertRaisesRegex(RuntimeError, "never became ready"),
            ):
                container_smoke.wait_ready(8000)

    def test_readiness_recovers_from_transient_failures(self):
        replies = [TimeoutError(), ConnectionError(), {"status": "ready"}]
        with (
            patch.object(container_smoke.time, "sleep"),
            patch.object(container_smoke, "request", side_effect=replies),
        ):
            # The positive control makes rejecting every image fail this same test.
            self.assertIsNone(container_smoke.wait_ready(8000))


class SmokeClient:
    def __init__(self, *, tools=None, version="26.09d", concepts=1, error="invalid_request"):
        self.tools = container_smoke.MCP_TOOLS if tools is None else tools
        self.manifest = {"version": version, "concepts": concepts}
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def list_tools(self):
        return SimpleNamespace(tools=[SimpleNamespace(name=name) for name in self.tools])

    async def read_resource(self, _uri):
        return SimpleNamespace(
            model_dump=lambda **_kwargs: {"contents": [{"text": json.dumps(self.manifest)}]}
        )

    async def call_tool(self, _name, _arguments):
        return SimpleNamespace(structured_content={"error": {"code": self.error}})


class SmokeSurfaceTest(unittest.TestCase):
    def test_protocol_probes_reject_wrong_profile_manifest_and_validation(self):
        cases = (
            ({"tools": set()}, "unified profile"),
            ({"version": "older"}, "another index manifest"),
            ({"concepts": 0}, "another index manifest"),
            ({"error": "upstream_unavailable"}, "before upstream I/O"),
        )
        with TemporaryDirectory() as directory:
            assets = Path(directory)
            (assets / "manifest.json").write_text('{"release_version": "26.09d"}')
            for changes, message in cases:
                with (
                    self.subTest(changes=changes),
                    patch.object(container_smoke, "Client", return_value=SmokeClient(**changes)),
                    self.assertRaisesRegex(RuntimeError, message),
                ):
                    asyncio.run(container_smoke.surface(8000, assets))


class SmokeEngine:
    def __init__(self, exit_code="0"):
        self.exit_code = exit_code
        self.running = False
        self.removed = False

    def docker(self, *args, **_kwargs):
        operation = args[0]
        if operation == "run":
            self.running = True
        elif operation == "stop":
            self.running = False
        elif operation == "rm":
            self.running, self.removed = False, True
        output = self.exit_code if operation == "inspect" else '{"event":"diagnostic"}\n'
        return SimpleNamespace(stdout=output, stderr="")


class SmokeCleanupTest(unittest.TestCase):
    def test_failed_diagnostics_still_remove_the_owned_container(self):
        for failure_at in ("collect", "write"):
            with self.subTest(failure_at=failure_at), TemporaryDirectory() as directory:
                root = Path(directory)
                engine = SmokeEngine()

                def docker(*args, failure_at=failure_at, engine=engine, **options):
                    if args[0] == "logs" and failure_at == "collect":
                        raise TimeoutError("Cannot collect diagnostics")
                    return engine.docker(*args, **options)

                with (
                    patch.object(container_smoke, "ROOT", root),
                    patch.object(container_smoke, "docker", docker),
                    patch.object(
                        container_smoke, "wait_ready", side_effect=RuntimeError("startup")
                    ),
                    patch.object(Path, "write_text", side_effect=PermissionError("Cannot write")),
                    self.assertRaises((TimeoutError, PermissionError)) as raised,
                ):
                    container_smoke.serve("image", root)
                self.assertEqual(str(raised.exception.__context__), "startup")
                self.assertFalse(engine.running)
                self.assertTrue(engine.removed)

    def test_failed_health_and_shutdown_keep_diagnostics_and_remove_owned_container(self):
        cases = (({"status": "bad"}, "0", "Health endpoint"), ({"status": "ok"}, "1", "gracefully"))
        for health, exit_code, message in cases:
            with self.subTest(message=message), TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "tmp").mkdir()
                engine = SmokeEngine(exit_code)
                with (
                    patch.object(container_smoke, "ROOT", root),
                    patch.object(container_smoke, "docker", engine.docker),
                    patch.object(container_smoke, "wait_ready"),
                    patch.object(container_smoke, "request", return_value=health),
                    patch.object(container_smoke, "surface"),
                    self.assertRaisesRegex(RuntimeError, message),
                ):
                    container_smoke.serve("image", root)
                self.assertFalse(engine.running)
                self.assertTrue(engine.removed)
                self.assertEqual(
                    json.loads((root / "tmp/container-smoke.log").read_text()),
                    {"event": "diagnostic"},
                )


class BenchmarkCommandTest(unittest.TestCase):
    def test_nonpositive_repetitions_are_rejected_without_creating_measurements(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            for repetitions in ("0", "-1"):
                with (
                    self.subTest(repetitions=repetitions),
                    patch(
                        "sys.argv",
                        ["benchmark", "--output", str(output), "--repetitions", repetitions],
                    ),
                    redirect_stdout(io.StringIO()),
                    self.assertRaisesRegex(ValueError, "At least one measured repetition"),
                ):
                    benchmark.main()
                self.assertFalse(output.exists())
