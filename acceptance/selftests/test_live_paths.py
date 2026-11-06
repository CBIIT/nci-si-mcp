"""Managed transport paths use real local receivers and never contact an upstream."""

import importlib
import importlib.util
import os
import socket
import subprocess
import sys
import threading
from dataclasses import replace
from http import HTTPStatus
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

import pytest

from nci_si_acceptance import environments, live_launcher
from nci_si_acceptance.client import server_environment
from nci_si_acceptance.fixture_server import FixtureServer, FixtureSet, Response, load_fixtures
from nci_si_mcp import cli

FIXTURES = Path(__file__).parents[1] / "fixtures"


def listener():
    name = "nci_si_acceptance.denied_surfaces"
    assert importlib.util.find_spec(name) is not None, (
        "blocked surfaces need an owned refusal endpoint"
    )
    return importlib.import_module(name).DeniedSurfaces()


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
    denied = listener()
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
    denied = listener()
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
    denied = listener()
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
        with FixtureServer(redirected) as selected, listener() as denied:
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
