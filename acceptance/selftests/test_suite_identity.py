"""The suite's identity: a digest over its own files, which a report states and which makes it an
acceptance report only when an approved release of the suite carries it."""

import json
import re
import shutil

import pytest
import yaml

from nci_si_acceptance.report import Collector, render
from nci_si_acceptance.suite_identity import (
    approved_digests,
    fixture_set_version,
    identity,
    suite_digest,
)


def recording(day):
    return json.dumps({"kind": "recorded", "recorded_on": day}).encode()


FILES = {
    "acceptance/tests/test_a.py": b"def test_a():\n    pass\n",
    "acceptance/fixtures/manifest.yaml": b"evs:\n  release: ncit_26.09d\n",
    "acceptance/fixtures/recorded/evs/old.json": recording("2026-10-01"),
    "acceptance/fixtures/recorded/evs/new.json": recording("2026-10-03"),
    "acceptance/fixtures/crafted/version.json": b'{"kind": "crafted"}',
    "acceptance/request-forms/evs.md": b"# EVS\n",
    "acceptance/src/nci_si_acceptance/client.py": b"VALUE = 1\n",
    "spec/tools.yaml": b"tools: []\n",
}
# Each is one of the five kinds of file the digest covers.
COVERED = [
    "acceptance/tests/test_a.py",
    "acceptance/fixtures/crafted/version.json",
    "acceptance/fixtures/manifest.yaml",
    "spec/tools.yaml",
    "acceptance/request-forms/evs.md",
    "acceptance/src/nci_si_acceptance/client.py",
]
# What a change leaves the digest as it was.
NOT_COVERED = [
    "acceptance/selftests/test_a.py",
    "acceptance/approved.yaml",
    "acceptance/tests/__pycache__/test_a.cpython-314.pyc",
    "acceptance/tests/.pytest_cache/state",
    "acceptance/.coverage",
    "acceptance/tests/.coverage.host.123",
    "spec/.DS_Store",
    "src/nci_si_mcp/server.py",
]


def build(root, files=FILES):
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return root


def approving(tmp_path, *digests):
    path = tmp_path / "approved.yaml"
    entries = [
        {"version": "v1.0.0", "digest": d, "approval": "ref", "date": "2026-12-01"} for d in digests
    ]
    path.write_text(yaml.safe_dump(entries), encoding="utf-8")
    return path


def rendered(root, approved):
    report = Collector().report("fixture") | {"suite": identity(root)}
    combined = {name: (row["outcome"], []) for name, row in report["tools"].items()}
    return render(report, combined, "fixture only", approved)


def test_the_digest_does_not_depend_on_file_order_or_on_where_the_tree_lies(tmp_path):
    forward = build(tmp_path / "one" / "deep")
    backward = build(tmp_path / "two", dict(reversed(FILES.items())))
    copied = shutil.copytree(forward, tmp_path / "three")

    assert suite_digest(forward) == suite_digest(backward) == suite_digest(copied)


def test_line_ends_do_not_change_the_digest_of_a_text_file_but_a_binary_file_is_hashed_as_is(
    tmp_path,
):
    text = {"acceptance/tests/test_a.py": b"a\nb\n"}
    crlf = {"acceptance/tests/test_a.py": b"a\r\nb\r\n"}
    binary = {"acceptance/fixtures/blob.bin": b"\0a\nb\n"}
    binary_crlf = {"acceptance/fixtures/blob.bin": b"\0a\r\nb\r\n"}

    digests = [
        suite_digest(build(tmp_path / str(n), files))
        for n, files in enumerate([text, crlf, binary, binary_crlf])
    ]

    assert digests[0] == digests[1]
    assert digests[2] != digests[3]


def test_the_boundary_between_two_files_is_part_of_the_digest(tmp_path):
    one = build(tmp_path / "one", {"spec/a.yaml": b"x", "spec/b.yaml": b"yz"})
    two = build(tmp_path / "two", {"spec/a.yaml": b"xy", "spec/b.yaml": b"z"})
    renamed = build(tmp_path / "three", {"spec/a.yaml": b"x", "spec/c.yaml": b"yz"})

    digests = {suite_digest(one), suite_digest(two), suite_digest(renamed)}
    assert len(digests) == len([one, two, renamed])


@pytest.mark.parametrize("name", COVERED)
def test_changing_any_suite_file_turns_the_report_modified(tmp_path, name):
    root = build(tmp_path / "suite")
    approved = approving(tmp_path, suite_digest(root))
    assert rendered(root, approved).startswith("# ACCEPTANCE REPORT")

    (root / name).write_bytes(FILES[name] + b"\n# changed\n")

    text = rendered(root, approved)
    assert text.startswith("# MODIFIED")
    assert "not an approved release of the suite (digest " in text.lower()


@pytest.mark.parametrize("name", NOT_COVERED)
def test_changing_what_is_not_the_suite_leaves_the_digest_alone(tmp_path, name):
    root = build(tmp_path / "suite")
    before = suite_digest(root)

    build(root, {name: b"changed"})

    assert suite_digest(root) == before


def test_a_digest_listed_as_approved_renders_an_acceptance_report_that_states_the_identity(
    tmp_path,
):
    root = build(tmp_path / "suite")
    digest = suite_digest(root)

    text = rendered(root, approving(tmp_path, "0" * 64, digest))

    assert text.startswith("# ACCEPTANCE REPORT\n")
    assert "MODIFIED" not in text
    assert f"Suite digest: {digest}." in text
    assert "Fixture set: ncit_26.09d, recorded 2026-10-03." in text
    assert re.search(r"Suite version: \S+\.", text)


def test_a_suite_not_listed_renders_modified_with_the_start_of_its_digest(tmp_path):
    root = build(tmp_path / "suite")
    digest = suite_digest(root)

    text = rendered(root, approving(tmp_path))

    assert text.startswith("# MODIFIED\n")
    assert f"Not an approved release of the suite (digest {digest[:12]}…)." in text
    assert f"Suite digest: {digest}." in text


def test_an_empty_approval_record_approves_nothing(tmp_path):
    path = tmp_path / "approved.yaml"
    path.write_text("# empty until the furnished tag\n[]\n", encoding="utf-8")

    assert approved_digests(path) == set()


def test_the_fixture_set_is_the_pin_and_the_latest_recording_date(tmp_path):
    root = build(tmp_path / "suite")

    assert fixture_set_version(root) == "ncit_26.09d, recorded 2026-10-03"
    shutil.rmtree(root / "acceptance/fixtures/recorded")
    assert fixture_set_version(root) == "ncit_26.09d, no recording"


def test_the_repository_itself_has_a_digest_and_an_approval_record():
    suite = identity()

    assert re.fullmatch(r"[0-9a-f]{64}", suite["digest"])
    assert re.fullmatch(r"ncit_\S+, recorded \d{4}-\d\d-\d\d", suite["fixture_set"])
    assert isinstance(approved_digests(), set)
