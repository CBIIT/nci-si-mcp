"""Disposable real HTTP surfaces; only the expensive worker is a controlled test double."""

import hashlib
import json
import sys
import threading
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from scripts.portal_http import create_server as portal_server  # noqa: E402
from scripts.portal_jobs import JobController  # noqa: E402
from scripts.portal_store import EvidenceStore  # noqa: E402
from scripts.static_server import create_server as docs_server  # noqa: E402

from test_portal_store import run_bundle  # noqa: E402


def serve(stack, server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stack.callback(thread.join, 5)
    stack.callback(server.server_close)
    stack.callback(server.shutdown)
    return f"http://127.0.0.1:{server.server_port}"


def failed_lookup_bundle():
    records = run_bundle()
    report = json.loads(records["report"])
    report["tests"]["tests/test_example.py::test_lookup"]["outcome"] = "failed"
    report["tools"]["lookup"].update(outcome="FAIL", counts={"failed": 1})
    records["report"] = json.dumps(report).encode()
    envelope = json.loads(records["envelope"])
    envelope.update(
        state="failed", exit_code=1, report_sha256=hashlib.sha256(records["report"]).hexdigest()
    )
    records["envelope"] = json.dumps(envelope).encode()
    return records


def main():
    with TemporaryDirectory(prefix="browser-", dir=ROOT / "tmp") as temporary, ExitStack() as stack:
        directory = Path(temporary)
        execution = directory / "execution.txt"

        def execute(_job, _directory, cancelled):
            execution.write_text("running", encoding="utf-8")
            try:
                if not cancelled.wait(30):
                    raise TimeoutError("Browser did not cancel its controlled worker")
                return {"state": "cancelled", "exit_code": None, "reason": "cancelled"}
            finally:
                execution.write_text("stopped", encoding="utf-8")

        jobs = JobController(directory / "jobs", execute=execute, commit=lambda: "a" * 40)
        stack.callback(jobs.close)
        store = EvidenceStore(directory / "results.sqlite")
        store.import_bundle(failed_lookup_bundle())
        addresses = {
            "docs": serve(stack, docs_server(Path(sys.argv[1]), port=0)),
            "portal": serve(stack, portal_server(store, jobs=jobs, port=0)),
            "execution": str(execution),
        }
        sys.stdout.write(json.dumps(addresses) + "\n")
        sys.stdout.flush()
        sys.stdin.read()


if __name__ == "__main__":
    main()
