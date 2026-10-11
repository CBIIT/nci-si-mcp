"""Exercise only owned local companion containers, including an isolated fixture job."""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from contextlib import ExitStack
from http import HTTPStatus
from http.client import HTTPConnection
from typing import Any

from scripts.portal_jobs import ACTIVE
from scripts.processes import wait_until

COMPOSE = "container/compose.local.yaml"


def docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(  # noqa: S603 - fixed commands and owned Docker resources, no shell
        ["docker", *args],  # noqa: S607 - local container engine executable
        capture_output=True,
        text=True,
        check=check,
        timeout=60,
    )
    return result.stdout.strip()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def request(port: int, path: str, body: str | None = None) -> tuple[int, str]:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {
        "Origin": f"http://127.0.0.1:{port}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        connection.request("GET" if body is None else "POST", path, body, headers)
        response = connection.getresponse()
        return response.status, response.read().decode()
    finally:
        connection.close()


def ready(port: int) -> None:
    def healthy() -> bool:
        try:
            return request(port, "/health")[0] == HTTPStatus.OK
        except OSError:
            return False  # Startup may not yet have bound its socket; the deadline remains fixed.

    if not wait_until(healthy, 45, interval=0.2):
        raise TimeoutError("Companion did not become healthy")


def inspect_boundary(container: str) -> None:
    installer = docker(
        "exec",
        container,
        "python",
        "-c",
        "import importlib.util; print(importlib.util.find_spec('pip'))",
    )
    require(installer == "None", "Build-time package installer remains in runtime")
    record = json.loads(docker("inspect", container))[0]
    config = record["HostConfig"]
    require(config["ReadonlyRootfs"], "Companion root is writable")
    require(record["Config"]["User"] == "65532:65532", "Companion is not nonroot")
    require(config["Memory"] > 0 and config["PidsLimit"] > 0, "Missing resource limits")
    require(
        all(
            mount["Destination"] == "/state"
            for mount in record["Mounts"]
            if mount["Type"] != "tmpfs"
        ),
        "Unexpected companion mount",
    )
    networks = list(record["NetworkSettings"]["Networks"])
    require(len(networks) == 1, "Companion has an extra network")
    require(
        json.loads(docker("network", "inspect", networks[0]))[0]["Internal"],
        "Fixture network has upstream egress",
    )


def blocked_connectivity(container: str, target: str) -> None:
    script = (
        "import socket,sys; s=socket.socket(); s.settimeout(2); "
        "result=s.connect_ex((sys.argv[1],int(sys.argv[2]))); "
        "s.close(); print(result)"
    )
    result = docker("exec", container, "python", "-c", script, target, "8000")
    require(int(result) != 0, "Fixture worker can reach the separate serving MCP")
    result = docker("exec", container, "python", "-c", script, "1.1.1.1", "443")
    require(int(result) != 0, "Fixture worker can reach an external network")


def benchmark(admin: str) -> None:
    run_id = uuid.uuid4().hex
    status, _ = request(8081, "/jobs", f"run_id={run_id}&profile=benchmark-http-fixture")
    require(status == HTTPStatus.SEE_OTHER, "Fixture benchmark was not admitted")

    states: list[str] = []

    def finished() -> bool:
        value: dict[str, Any] = json.loads(
            docker("exec", admin, "cat", "/state/evidence.jobs/jobs.json")
        )
        states.append(next(row for row in value["jobs"] if row["run_id"] == run_id)["state"])
        return states[-1] not in ACTIVE

    if not wait_until(finished, 300, interval=1):
        raise TimeoutError("Container benchmark did not terminate within its smoke bound")
    require(states[-1] == "completed", f"Fixture benchmark ended as {states[-1]}")
    status, page = request(8081, "/runs/" + run_id)
    require(
        status == HTTPStatus.OK and "cold" in page.lower() and "warm" in page.lower(),
        "Benchmark results are unavailable",
    )


def _serving_probe(network: str, name: str) -> tuple[str, int]:
    docker("network", "create", network)
    docker(
        "run",
        "-d",
        "--name",
        name,
        "--network",
        network,
        "--read-only",
        "--tmpfs",
        "/tmp:rw,size=64m,mode=1777",  # noqa: S108 - private tmpfs in the owned container
        "--memory",
        "512m",
        "--pids-limit",
        "64",
        "-p",
        "127.0.0.1::8000",
        "--entrypoint",
        "python",
        "-e",
        "NCI_SI_DATA_DIR=/tmp/data",
        "-e",
        "NCI_SI_EMBEDDING_PROVIDER=hashing",
        "-e",
        "NCI_SI_TRANSPORT=streamable-http",
        "-e",
        "NCI_SI_HTTP_HOST=0.0.0.0",
        "-e",
        "NCI_SI_HTTP_AUTH_MODE=trusted-local",
        "nci-si-admin:local",
        "-m",
        "nci_si_mcp.cli",
        "serve",
    )
    record = json.loads(docker("inspect", name))[0]
    port = int(record["NetworkSettings"]["Ports"]["8000/tcp"][0]["HostPort"])
    return record["NetworkSettings"]["Networks"][network]["IPAddress"], port


def _remove_owned(kind: str, name: str) -> None:
    options = ("--all",) if kind == "container" else ()
    found = docker(kind, "ls", *options, "--quiet", "--filter", f"name=^{name}$")
    if found:
        force = ("--force",) if kind == "container" else ()
        docker(kind, "rm", *force, name)


def cleanup(compose: tuple[str, ...], serving: str, network: str) -> None:
    # Every owned cleanup is attempted even if an earlier engine operation fails.
    with ExitStack() as pending:
        pending.callback(_remove_owned, "network", network)
        pending.callback(_remove_owned, "container", serving)
        pending.callback(docker, *compose, "down", "--volumes")


def main() -> None:
    project = "nci-si-smoke-" + uuid.uuid4().hex[:10]
    compose = ("compose", "-f", COMPOSE, "-p", project)
    serving = project + "-serving"
    network = project + "-probe"
    try:
        docker(*compose, "up", "-d")
        ready(8080)
        ready(8081)
        require("National Cancer Institute" in request(8080, "/")[1], "Docs identity missing")
        admin = docker(*compose, "ps", "-q", "administration")
        inspect_boundary(admin)
        address, port = _serving_probe(network, serving)
        ready(port)
        blocked_connectivity(admin, address)
        benchmark(admin)
        docker(*compose, "stop", "administration")
        require(
            request(port, "/health")[0] == HTTPStatus.OK, "Stopping admin stopped the serving MCP"
        )
    finally:
        try:
            if sys.exception() is not None:
                print(docker(*compose, "ps", "--all", check=False))
                print(docker(*compose, "logs", "--no-color", "--tail", "30", check=False))
        finally:
            cleanup(compose, serving, network)
    print("Companion health, isolation, fixture benchmark and independent shutdown passed")


if __name__ == "__main__":
    main()
