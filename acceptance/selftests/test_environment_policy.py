"""Reviewed policy validation rejects raw ambiguity without exposing the rejected input."""

import pytest
import yaml

from nci_si_acceptance import environments


def policy_with_origin(monkeypatch, tmp_path, origin):
    document = yaml.safe_load(environments.POLICY.read_bytes())
    document["allowed"]["evs"] = origin
    document["environments"]["evs-only"]["evs"] = origin
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(document))
    monkeypatch.setattr(environments, "POLICY", path)
    monkeypatch.setenv(environments.VARIABLE, "evs-only")


@pytest.mark.parametrize(
    "origin",
    [
        "https://api-evsrest.nci.nih.gov\t",
        "https://api-evsrest.nci.nih.gov?",
        "https://api-evsrest.nci.nih.gov#",
        " https://api-evsrest.nci.nih.gov",
        "https://api-evsrest.nci.nih.gov/space path",
        "https://api-evsrest.nci.nih.gov\n",
        "https://api-evsrest.nci.nih.gov/\x80",
    ],
)
def test_raw_ambiguous_origin_is_rejected_without_echo(monkeypatch, tmp_path, origin):
    policy_with_origin(monkeypatch, tmp_path, origin)
    with pytest.raises(ValueError, match="invalid reviewed environment origin") as error:
        environments.selection("live")
    assert origin not in str(error.value)


@pytest.mark.parametrize(
    "origin",
    ["https://api-evsrest.nci.nih.gov:8443/api/v1", "https://api-evsrest.nci.nih.gov/a%20b"],
)
def test_explicit_paths_ports_and_encoded_path_data_stay_exact(monkeypatch, tmp_path, origin):
    policy_with_origin(monkeypatch, tmp_path, origin)
    selected = environments.selection("live")
    assert selected.origins["evs"] == origin


@pytest.mark.parametrize("defect", ["missing", "extra", "duplicate", "permission", "unknown"])
def test_policy_structure_and_permission_are_closed(monkeypatch, tmp_path, defect):
    raw = environments.POLICY.read_text()
    edits = {
        "missing": ("  ssis-sparql: null", ""),
        "extra": ("allowed:", "allowed:\n  private-marker: null"),
        "duplicate": ("allowed:", "allowed:\n  evs: https://private-marker.invalid"),
        "permission": ("    cadsr: null", "    cadsr: https://private-marker.invalid"),
        "unknown": ("environments:", "private-marker:\nenvironments:"),
    }
    old, new = edits[defect]
    assert old in raw
    path = tmp_path / "policy.yaml"
    path.write_text(raw.replace(old, new, 1))
    monkeypatch.setattr(environments, "POLICY", path)
    monkeypatch.setenv(environments.VARIABLE, "evs-only")
    with pytest.raises(ValueError) as error:
        environments.selection("live")
    assert "private-marker" not in str(error.value)


def test_shipped_policy_is_exactly_the_reviewed_evs_only_permission():
    expected = {
        "evs": "https://api-evsrest.nci.nih.gov",
        "evs-fhir": "https://api-evsrest.nci.nih.gov/fhir/r4",
        "cadsr": None,
        "cadsr-ftp": None,
        "cadsr-contracts": None,
        "ssis": None,
        "ssis-sparql": None,
    }
    assert yaml.safe_load(environments.POLICY.read_bytes()) == {
        "allowed": expected,
        "environments": {"evs-only": expected},
    }
