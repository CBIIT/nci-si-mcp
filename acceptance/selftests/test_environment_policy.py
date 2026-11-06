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
