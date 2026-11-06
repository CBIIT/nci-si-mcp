"""Managed transport paths use real local receivers and never contact an upstream."""

import importlib
import importlib.util
import os
import socket
import subprocess
import sys
import threading
from http import HTTPStatus
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

import pytest

from nci_si_acceptance.client import server_environment
from nci_si_acceptance.fixture_server import FixtureServer, FixtureSet, load_fixtures

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
            assert peer.recv(4096).startswith(b"HTTP/1.0 403")
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
    with pytest.raises(RuntimeError, match="owned refusal listener"), denied:
        if phase == "serve":
            denied.thread.join(2)
    assert not denied.thread.is_alive()
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0
    output = capfd.readouterr()
    assert "private-marker" not in output.out + output.err
    assert thread_errors == []
