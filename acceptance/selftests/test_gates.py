"""The protocol gates pass against a server that meets them, and each fails on its own defect
(compliant_server.py)."""

import pytest

pytest_plugins = ["pytester"]

# The gate tests, by requirement.
P1 = "test_tools_list_names_the_tools_of_the_profile_and_no_other"
P2 = "test_every_output_schema_admits_the_error_record_and_refuses_a_malformed_one"
P3 = "test_no_description_holds_placeholder_or_debug_text"
P4 = "test_tool_names_are_verb_led_lowercase_and_underscore_separated"
P5 = "test_tools_list_may_be_cached_and_shared"
P6_CALLED = "test_tools_list_is_the_same_after_a_call_that_pins_a_terminology_and_release"
P6_UNAVAILABLE = "test_tools_list_is_the_same_while_the_platform_is_unavailable"
P7 = "test_a_correlation_identifier_goes_upstream_and_comes_back"
P10 = "test_every_tool_is_annotated_read_only_idempotent_and_open_world"
P11 = "test_no_description_or_schema_shows_what_the_tool_does_not_offer"
P12 = "test_each_tool_takes_the_parameters_the_specification_names"
# Each defect of the gate server, and the gate test that must fail on it.
DEFECTS = [
    ("missing-tool", P1),
    ("no-error-shape", P2),
    ("declares-no-shape", P2),
    ("one-code", P2),
    ("placeholder", P3),
    ("misnamed", P4),
    ("no-ttl", P5),
    ("listing-changes", P6_CALLED),
    ("redescribed", P6_CALLED),
    ("hides-tools", P6_UNAVAILABLE),
    ("no-correlation", P7),
    ("destructive", P10),
    ("not-idempotent", P10),
    ("closed-world", P10),
    ("parameter-renamed", P12),
    ("release-optional", P12),
    ("quotes-not-offered", P11),
    ("lists-not-offered", P11),
    ("patterns-not-offered", P11),
]


def test_every_gate_passes_against_a_server_that_meets_them(outcomes):
    passed = outcomes("tests/test_protocol.py")

    assert set(passed.values()) == {"passed"}
    assert set(passed) == {gate for _, gate in DEFECTS}


@pytest.mark.parametrize(("defect", "gate"), DEFECTS)
def test_each_gate_fails_on_its_own_defect(outcomes, monkeypatch, defect, gate):
    monkeypatch.setenv("COMPLIANT_SERVER_DEFECT", defect)

    assert outcomes("tests/test_protocol.py", "-k", gate)[gate] == "failed"
