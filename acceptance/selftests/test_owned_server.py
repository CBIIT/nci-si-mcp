"""The server the harness owns is ready when `start` returns and gone when its scope ends."""

import os
import socket
from pathlib import Path

import pytest

from nci_si_acceptance.client import OwnedServer, server_environment

# The fixture upstream is never contacted: the tests only start and stop the server.
UPSTREAM = "http://127.0.0.1:9"


def listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def owned(directory: Path, **options) -> OwnedServer:
    return OwnedServer(
        server_environment("fixture", directory / "data", UPSTREAM),
        directory / "server.log",
        data_dir=directory / "data",
        require_index=False,
        **options,
    )


def settings(directory: Path) -> dict[str, str]:
    environment = server_environment("fixture", directory / "data", UPSTREAM)
    return {key: value for key, value in environment.items() if key.startswith("NCI_SI_")}


def test_owned_server_is_reaped_when_the_scope_raises(tmp_path):
    with pytest.raises(KeyError), owned(tmp_path) as server:
        server.start(settings(tmp_path))
        assert listening(server.port)
        raise KeyError
    assert not listening(server.port)


def test_restart_serves_again_and_stop_is_final(tmp_path):
    with owned(tmp_path) as server:
        server.start(settings(tmp_path))
        server.restart(settings(tmp_path))
        assert listening(server.port)
        server.stop()
        assert not listening(server.port)
        server.stop()


def test_a_server_that_exits_before_readiness_fails_naming_its_log(tmp_path):
    with owned(tmp_path) as server:
        with pytest.raises(
            RuntimeError, match=f"exited before readiness; see {tmp_path}/server.log"
        ):
            server.start(settings(tmp_path) | {"NCI_SI_LOG_LEVEL": "not-a-level"})
        assert not listening(server.port)
    assert (tmp_path / "server.log").read_text()


def test_a_server_not_ready_in_time_is_reaped_and_reported(tmp_path):
    with owned(tmp_path, ready_seconds=0) as server:
        with pytest.raises(TimeoutError, match=f"did not become ready; see {tmp_path}/server.log"):
            server.start(settings(tmp_path))
        assert not listening(server.port)


def test_stop_kills_a_server_that_ignores_termination(tmp_path, monkeypatch):
    # sitecustomize runs in the child before the server: it records the child's pid and makes
    # SIGTERM stick, even against the server's own handler installation.
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "sitecustomize.py").write_text(
        "import os, signal\n"
        f"open({str(tmp_path / 'child.pid')!r}, 'w').write(str(os.getpid()))\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "signal.signal = lambda *_arguments: None\n"
    )
    monkeypatch.setattr("nci_si_acceptance.client.STOP_SECONDS", 1)
    environment = server_environment("fixture", tmp_path / "data", UPSTREAM) | {
        "PYTHONPATH": str(hooks)
    }
    server = OwnedServer(
        environment, tmp_path / "server.log", data_dir=tmp_path / "data", require_index=False
    )
    server.start(settings(tmp_path))
    pid = int((tmp_path / "child.pid").read_text())
    server.stop()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_a_second_start_is_refused_and_the_first_server_stays_owned(tmp_path):
    with owned(tmp_path) as server:
        server.start(settings(tmp_path))
        with pytest.raises(RuntimeError, match="already running"):
            server.start(settings(tmp_path))
        assert listening(server.port)
    assert not listening(server.port)


def test_a_foreign_listener_on_the_port_is_not_taken_for_the_server(tmp_path):
    with owned(tmp_path) as server, socket.socket() as foreign:
        foreign.bind(("127.0.0.1", server.port))
        foreign.listen()
        with pytest.raises(RuntimeError, match=f"already in use; see {tmp_path}/server.log"):
            server.start(settings(tmp_path))
