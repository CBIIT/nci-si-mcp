"""The register of request forms is generated from the manifest and is current."""

import shutil

import yaml

from nci_si_acceptance.record import FIXTURES
from nci_si_acceptance.register import REGISTER, main, render

MANIFEST = yaml.safe_load((FIXTURES / "manifest.yaml").read_text(encoding="utf-8"))
VIEWS = render()


def test_the_register_on_disk_is_what_the_manifest_generates():
    on_disk = {path.name: path.read_text(encoding="utf-8") for path in REGISTER.glob("*.md")}

    assert on_disk == VIEWS


def test_every_form_names_its_operation_and_why_it_has_this_form():
    entries = [*MANIFEST["record"]["requests"], *MANIFEST["record"].get("derived", [])]

    assert [entry["fixture"] for entry in entries if not entry.get("operation")] == []
    assert [entry["fixture"] for entry in entries if not entry.get("rationale")] == []


def test_each_team_sees_its_own_surfaces_and_the_combined_view_all():
    expand = "/ValueSet/$expand"

    assert expand in VIEWS["evs.md"]
    assert expand not in VIEWS["cadsr.md"]
    assert "None yet." in VIEWS["cadsr.md"]
    assert all(
        entry["path"].split("?")[0] in VIEWS["all.md"] for entry in MANIFEST["record"]["requests"]
    )


def test_the_unpinned_operations_show_the_release_their_payload_reports():
    rows = [
        line
        for line in VIEWS["all.md"].splitlines()
        if line.startswith("| OP-E06 | `GET evs /api/v1/subset/ncit_26.09d")
    ]

    assert rows == [
        "| OP-E06 | `GET evs /api/v1/subset/ncit_26.09d/C177537` "
        "| `GET evs /api/v1/subset/ncit/C177537` | 26.09d |"
    ]


def test_a_cell_escapes_the_pipe_that_would_end_it():
    assert "Thesaurus.owl\\|26.09d" in VIEWS["evs.md"]


def test_the_command_writes_the_views_next_to_the_fixtures(tmp_path, capsys):
    shutil.copytree(FIXTURES, tmp_path / "fixtures")

    assert main(["--fixtures", str(tmp_path / "fixtures")]) == 0

    assert "Wrote the register" in capsys.readouterr().out
    assert {path.name for path in (tmp_path / "request-forms").glob("*.md")} == set(VIEWS)
    assert (tmp_path / "request-forms" / "all.md").read_text(encoding="utf-8") == VIEWS["all.md"]
