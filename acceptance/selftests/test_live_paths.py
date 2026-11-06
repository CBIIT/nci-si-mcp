"""Managed transport paths use real local receivers and never contact an upstream."""

import os
import socket
import subprocess
import sys
import threading
import urllib.request
from contextlib import nullcontext
from dataclasses import replace
from http import HTTPStatus
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from nci_si_acceptance import environments, live_launcher
from nci_si_acceptance.client import server_environment
from nci_si_acceptance.denied_surfaces import DeniedSurfaces
from nci_si_acceptance.fixture_server import FixtureServer, FixtureSet, Response, load_fixtures
from nci_si_mcp import cli, http_client

FIXTURES = Path(__file__).parents[1] / "fixtures"
FURNISHED_PREFIX = Path(live_launcher.__file__).resolve().parents[3] / ".venv"


def test_real_furnished_child_ignores_ambient_os_proxy(tmp_path):
    with (
        FixtureServer(load_fixtures(FIXTURES)) as upstream,
        FixtureServer(FixtureSet({}, {})) as proxy,
    ):
        environment = server_environment("fixture", tmp_path, upstream.url)
        shadow = tmp_path / "external"
        shadow.mkdir()
        marker = tmp_path / "external-code-ran"
        (shadow / "sitecustomize.py").write_text(f"open({str(marker)!r}, 'w').close()")
        environment["PYTHONPATH"] = str(shadow)
        # All five core bases are already loopback fixtures before the missing-entrypoint red.
        script = (
            "import sys, urllib.request as u; "
            f"u.getproxies=lambda: {{'http': {proxy.url!r}}}; "
            "u.proxy_bypass=lambda host: False; "
            "from nci_si_acceptance.live_launcher import main; "
            "sys.argv=['launcher','index-sample','C4817']; raise SystemExit(main())"
        )
        ran = subprocess.run(  # noqa: S603 - fixed isolated interpreter; all destinations are loopback fixtures.
            [sys.executable, "-I", "-c", script],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert ran.returncode == 0, ran.stderr
        assert upstream.log(), "the approved local destination must receive the actual request"
        assert proxy.log() == [], "neither environment nor OS proxy discovery may reroute the child"
        assert not marker.exists()


def test_owned_refusal_has_no_echo_or_forwarding_and_closes_on_exception(capfd):
    denied = DeniedSurfaces()
    with pytest.raises(RuntimeError, match="owner interrupted"), denied as url:
        address = url.removeprefix("http://").split(":")
        port = int(address[1])
        with pytest.raises(HTTPError) as error:
            build_opener(ProxyHandler({})).open(url + "/private-marker?secret-marker", timeout=2)
        assert error.value.code == HTTPStatus.FORBIDDEN
        assert error.value.read() == b"surface disabled by run policy"
        raise RuntimeError("owner interrupted")
    output = capfd.readouterr()
    assert "private-marker" not in output.out + output.err
    assert "secret-marker" not in output.out + output.err
    assert not denied.thread.is_alive()
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0


def test_partial_request_cannot_hold_the_refusal_listener_open():
    denied = DeniedSurfaces()
    with denied as url:
        port = int(url.rsplit(":", 1)[1])
        with socket.create_connection(("127.0.0.1", port), timeout=2) as peer:
            peer.sendall(b"GET /incomplete-private-marker")
            expected = b"HTTP/1.0 403 Forbidden\r\n"
            # Bound the status read independently; buffered reads tolerate TCP fragmentation.
            with peer.makefile("rb") as response:
                assert response.readline(len(expected) + 1) == expected
    assert not denied.thread.is_alive()


def test_live_environment_requires_an_owned_refusal_endpoint(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith(("NCI_SI_", "PYTHON")) or key.lower().endswith("_proxy"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_ENVIRONMENT", "evs-only")
    with pytest.raises(ValueError, match="owned refusal"):
        server_environment("live", tmp_path, None)


@pytest.mark.parametrize("phase", ["startup", "serve", "close"])
def test_listener_failures_are_visible_sanitized_and_leave_no_owned_resource(
    monkeypatch, capfd, phase
):
    denied = DeniedSurfaces()
    port = denied._server.server_address[1]
    thread_errors = []
    monkeypatch.setattr(
        threading, "excepthook", lambda failure: thread_errors.append(str(failure.exc_value))
    )
    close = denied._server.server_close

    def fail():
        raise RuntimeError("private-marker")

    def fail_close():
        close()
        raise OSError("private-marker")

    if phase == "startup":
        monkeypatch.setattr(denied.thread, "start", fail)
    elif phase == "serve":
        monkeypatch.setattr(denied._server, "handle_request", fail)
    else:
        monkeypatch.setattr(denied._server, "server_close", fail_close)
    with pytest.raises(RuntimeError, match="owned refusal listener") as error, denied:
        if phase == "serve":
            denied.thread.join(2)
    expected = {"startup": "owned refusal listener failed to start"}.get(
        phase, "owned refusal listener did not finish cleanly"
    )
    assert str(error.value) == expected
    assert not denied.thread.is_alive()
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0
    output = capfd.readouterr()
    assert "private-marker" not in output.out + output.err
    assert thread_errors == []


@pytest.mark.parametrize("error", [OSError, ValueError, RuntimeError])
def test_refusal_shutdown_distinguishes_expected_and_unexpected_errors(monkeypatch, error):
    denied = DeniedSurfaces()
    entered = threading.Event()
    stopped = []

    def interrupted():
        entered.set()
        stopped.append(denied._stop.wait(2))
        raise error("private-marker")

    monkeypatch.setattr(denied._server, "handle_request", interrupted)
    expectation = (
        pytest.raises(RuntimeError, match=r"\Aowned refusal listener did not finish cleanly\Z")
        if error is RuntimeError
        else nullcontext()
    )
    with expectation, denied:
        assert entered.wait(2)
    assert stopped == [True]
    assert not denied.thread.is_alive()
    with socket.socket() as probe:
        assert probe.connect_ex(denied._server.server_address) != 0


@pytest.mark.parametrize(
    ("boundary", "message"),
    [
        ("module", "imported an unexpected module origin"),
        ("prefix", "requires the furnished project environment"),
        ("hook", "transport integration is unavailable"),
    ],
)
def test_launcher_refuses_untrusted_installation_before_cli(
    monkeypatch, tmp_path, boundary, message
):
    marker = tmp_path / "cli-ran"
    monkeypatch.setattr(cli, "main", lambda: marker.write_text("ran"))
    monkeypatch.setattr(sys, "argv", ["launcher", "serve"])
    monkeypatch.setattr(sys, "prefix", str(FURNISHED_PREFIX))
    if boundary == "module":
        monkeypatch.setattr(live_launcher.nci_si_acceptance, "__file__", str(tmp_path / "other.py"))
    elif boundary == "prefix":
        monkeypatch.setattr(sys, "prefix", str(tmp_path))
    else:
        monkeypatch.setattr(live_launcher.http_client, "_OPENER", None)
    with pytest.raises(ValueError) as error:
        live_launcher.main()
    assert (
        str(error.value)
        == "controlled live " + ("launcher " if boundary != "hook" else "") + message
    )
    assert not marker.exists()


@pytest.mark.parametrize("credential", [None, "private-marker"])
def test_furnished_child_never_forwards_cross_origin_redirect_or_credentials(tmp_path, credential):
    fixtures = load_fixtures(FIXTURES)
    with FixtureServer(FixtureSet({}, {})) as forbidden:
        redirect = Response(302, {"Location": forbidden.url + "/must-not-arrive"})
        redirected = replace(
            fixtures,
            ordinary={
                key: replace(fixture, responses=(redirect,))
                for key, fixture in fixtures.ordinary.items()
            },
        )
        with FixtureServer(redirected) as selected, DeniedSurfaces() as denied:
            origins = dict.fromkeys(environments.SURFACES)
            origins["evs"] = selected.base_url("evs")
            credentials = {"NCI_SI_EVS_LICENSE_KEY": credential} if credential else {}
            environment = server_environment(
                "live",
                tmp_path,
                None,
                environment=environments.Environment("local-test", origins, credentials, "a" * 64),
                denied_url=denied,
            )
            ran = subprocess.run(  # noqa: S603 - furnished interpreter, selected/forbidden receivers local only.
                [*environments.LAUNCHER, "index-sample", "C4817"],
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert ran.returncode != 0
            assert forbidden.log() == []
            received = selected.log()
            assert len(received) == 1
            headers = {key.lower(): value for key, value in received[0]["headers"].items()}
            assert headers.get("x-evsrestapi-license-key") == credential
            assert "private-marker" not in ran.stdout + ran.stderr


@pytest.fixture
def callable_launcher(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    assert (
        Path(live_launcher.__file__).resolve()
        == root / "acceptance/src/nci_si_acceptance/live_launcher.py"
    )
    assert Path(http_client.__file__).resolve() == root / "src/nci_si_mcp/http_client.py"
    monkeypatch.setattr(sys, "prefix", str(root / ".venv"))
    monkeypatch.setattr(http_client, "_OPENER", http_client._OPENER)
    return live_launcher.main


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["serve"], ["serve", "--transport", "stdio"]),
        (["index-sample", "C4817", "C2991"], ["index-sample", "C4817", "C2991"]),
        ([], None),
        (["index-sample"], None),
        (["index-sample", "bad"], None),
        (["other"], None),
    ],
)
def test_imported_launcher_dispatch_and_refusal(
    monkeypatch, callable_launcher, arguments, expected
):
    seen = []
    expected_exit = 17

    def dispatched():
        seen.append(sys.argv.copy())
        return expected_exit

    monkeypatch.setattr(cli, "main", dispatched)
    monkeypatch.setattr(sys, "argv", ["launcher", *arguments])
    if expected is None:
        with pytest.raises(ValueError) as error:
            callable_launcher()
        assert str(error.value) == "controlled live launcher requires a furnished operation"
        assert seen == []
    else:
        assert callable_launcher() == expected_exit
        assert seen == [["nci-si-mcp", *expected]]


def test_imported_launcher_transport_never_proxies_or_forwards_redirects(
    monkeypatch, callable_launcher
):
    expected_exit = 23
    fixtures = load_fixtures(FIXTURES)
    with FixtureServer(FixtureSet({}, {})) as forbidden, FixtureServer(FixtureSet({}, {})) as proxy:
        redirect = Response(302, {"Location": forbidden.url + "/must-not-arrive"})
        redirected = replace(
            fixtures,
            ordinary={
                key: replace(fixture, responses=(redirect,))
                for key, fixture in fixtures.ordinary.items()
            },
        )
        with FixtureServer(redirected) as selected:
            monkeypatch.setattr(urllib.request, "getproxies", lambda: {"http": proxy.url})
            monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
            monkeypatch.setattr(
                http_client, "_OPENER", build_opener(ProxyHandler({"http": proxy.url}))
            )
            monkeypatch.setattr(sys, "argv", ["launcher", "serve"])

            def transported():
                request = Request(  # noqa: S310 - owned loopback fixture URL.
                    selected.base_url("evs") + "/api/v1/metadata/terminologies",
                    headers={"X-EVSRESTAPI-License-Key": "private-marker"},
                )
                with pytest.raises(HTTPError) as error:
                    http_client._OPENER.open(request, timeout=2)
                with error.value:
                    assert proxy.log() == []
                    assert forbidden.log() == []
                    assert error.value.code == HTTPStatus.FOUND
                return expected_exit

            monkeypatch.setattr(cli, "main", transported)
            assert callable_launcher() == expected_exit
            received = selected.log()
            assert len(received) == 1
            assert received[0]["fixture"] is not None
            assert {key.lower(): value for key, value in received[0]["headers"].items()}[
                "x-evsrestapi-license-key"
            ] == "private-marker"
