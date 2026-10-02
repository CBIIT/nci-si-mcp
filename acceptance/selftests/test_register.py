"""The register of request forms is generated from the manifest and is current."""

import shutil

import pytest
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
        for line in section(VIEWS["all.md"], "## Operations without a pinned form upstream")
        if line.startswith("| OP-E06 |")
    ]

    assert rows == [
        "| OP-E06 | `GET evs /api/v1/subset/ncit_26.09d/C177537` "
        "| `GET evs /api/v1/subset/ncit/C177537` | 26.09d |"
    ]


def section(view, heading):
    """The lines of `view` from `heading` to the next heading of any level."""

    lines = view.splitlines()
    start = lines.index(heading) + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("#")), len(lines))
    return lines[start:end]


def test_what_the_register_asks_of_a_team_comes_before_the_requests():
    view = VIEWS["evs.md"]

    assert view.index("## Operations without a pinned form upstream") < view.index("## Requests")


def test_each_request_says_whether_it_is_recorded_or_crafted_and_for_what():
    subset = ("/subset-gdc-unpinned.json` |", "/OP-E06/subset-gdc.json` |")
    rows = [line for line in section(VIEWS["evs.md"], "## Requests") if line.endswith(subset)]

    assert [row.split(" | ")[3] for row in rows] == [
        "recorded",
        "crafted for OP-E06: every content path accepts {terminology}_{release}. EVS answered"
        ' this pinned path with 404 "Subset not found" on 2 October 2026',
    ]
    assert "scenarios/" not in "\n".join(section(VIEWS["evs.md"], "## Requests"))


def test_a_scenario_states_what_it_provokes_and_a_shared_rationale_once():
    lines = section(VIEWS["evs.md"], "### `release/unknown`")
    rationale = "An unknown release answers 404 on every content path: the server fails closed."

    assert lines[1] == "Every content path of `ncit_99.99z` answers 404. Recorded."
    assert "\n".join(lines).count(rationale) == 1
    assert {line.split(" | ")[-1] for line in lines if line.startswith("| OP-")} == {
        f"`{entry['fixture']}` |"
        for entry in MANIFEST["record"]["requests"]
        if entry["fixture"].startswith("scenarios/release/unknown/")
    }


def test_a_crafted_scenario_names_its_requirement_and_counts_its_fixtures():
    assert section(VIEWS["evs.md"], "### `license/restricted`")[1] == (
        "403 without the licence key; invented content with it. Recorded. Crafted, 1 fixture, for"
        " E-7, SOW v2 §6-§7: the licence key sent from configuration."
    )
    assert section(VIEWS["evs.md"], "### `traversal/starvation`")[1] == (
        "One concept has 300 roles and 2 associations. Crafted, 303 fixtures, for A5.5: a budget"
        " per relationship kind; no kind starved."
    )


def test_the_evs_view_states_the_concept_rules_the_ignored_parameters_and_each_rationale():
    view = VIEWS["evs.md"]
    unknown = section(view, "### `release/unknown`")
    version = next(line for line in section(view, "## Requests") if "/api/v1/version`" in line)

    assert "## Concept requests answered by rule" in view
    assert "## Concept requests answered by rule" not in VIEWS["cadsr.md"]
    assert all("| 404; every parameter ignored |" in line for line in unknown if "`GET " in line)
    assert "| The furnished server's ncit_release_info reads it;" in version


@pytest.mark.parametrize("change", ["undescribed", "absent"])
def test_every_scenario_on_disk_is_described_in_the_manifest_and_no_other(tmp_path, change):
    root = shutil.copytree(FIXTURES, tmp_path / "fixtures")
    if change == "undescribed":
        (root / "scenarios" / "release" / "new").mkdir()
    else:
        shutil.rmtree(root / "scenarios" / "batch")

    name = "release/new" if change == "undescribed" else "batch/silent-drop"
    with pytest.raises(ValueError, match=f"describes the scenarios on disk and no others: {name}$"):
        render(root)


def test_a_cell_escapes_the_pipe_that_would_end_it():
    assert "Thesaurus.owl\\|26.09d" in VIEWS["evs.md"]


def test_the_command_writes_the_views_next_to_the_fixtures(tmp_path, capsys):
    shutil.copytree(FIXTURES, tmp_path / "fixtures")

    assert main(["--fixtures", str(tmp_path / "fixtures")]) == 0

    assert "Wrote the register" in capsys.readouterr().out
    assert {path.name for path in (tmp_path / "request-forms").glob("*.md")} == set(VIEWS)
    assert (tmp_path / "request-forms" / "all.md").read_text(encoding="utf-8") == VIEWS["all.md"]


def test_the_unpinned_list_holds_each_content_form_without_a_release_and_what_it_reports():
    rows = [
        line.split(" | ")
        for line in section(VIEWS["all.md"], "## Operations without a pinned form upstream")
        if line.startswith("| ") and not line.startswith("| Operation")
    ]
    reported = {served.strip("`"): release.rstrip(" |") for *_, served, release in rows}
    maps = "GET evs /api/v1/mapset/NCIt_Maps_To_GDC/maps?term="
    expand = "GET evs-fhir /ValueSet/$expand?url=http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl"

    assert reported == {
        "GET evs /api/v1/subset/ncit/C177537": "26.09d",
        "GET evs /api/v1/mapset": reported["GET evs /api/v1/mapset"],
        "GET evs /api/v1/mapset/NCIt_Maps_To_GDC": "26.09d",
        f"{maps}Ewing sarcoma&fromRecord=0&pageSize=10": "26.09d",
        f"{maps}C4817&fromRecord=0&pageSize=10": "26.09d",
        f"{expand}?fhir_vs=C85492": "26.09d",
    }
    assert "26.09d" in reported["GET evs /api/v1/mapset"].split(", ")
