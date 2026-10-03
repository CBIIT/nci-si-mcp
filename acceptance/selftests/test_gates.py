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
P6_CALLED = "test_tools_list_is_the_same_after_a_content_call"
P6_UNAVAILABLE = "test_tools_list_is_the_same_while_the_platform_is_unavailable"
P7 = "test_a_correlation_identifier_goes_upstream_and_comes_back"
P10 = "test_every_tool_is_annotated_read_only_idempotent_and_open_world"
P11 = "test_no_description_or_schema_shows_what_the_tool_does_not_offer"
P12 = "test_each_tool_takes_the_parameters_the_specification_names"
P8_PROMPTS = "test_prompts_list_names_the_prompts_of_the_profile_with_their_arguments"
P8_MESSAGES = (
    "test_a_prompt_returns_messages_naming_the_tools_it_states_and_only_tools_of_the_profile"
)
P8_RESOURCES = "test_resources_and_templates_list_the_uri_templates_of_the_profile"
P9 = "test_a_resource_read_equals_its_tool_s_answer_with_provenance_and_the_same_caching"
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
    ("no-prompts", P8_PROMPTS),
    ("prompt-outside-profile", P8_PROMPTS),
    ("prompt-outside-profile", P8_MESSAGES),
    ("no-resources", P8_RESOURCES),
    ("uri-differs", P8_RESOURCES),
    ("no-resources", P9),
    ("resource-no-ttl", P9),
    ("resource-no-provenance", P9),
    ("resource-other-content", P9),
    ("resource-wrong-mime", P9),
]


def test_every_gate_passes_against_a_server_that_meets_them(outcomes):
    passed = outcomes("tests/test_protocol.py")

    assert set(passed.values()) == {"passed"}
    # A parametrized gate has a case for each resource the profile serves.
    assert {case.partition("[")[0] for case in passed} == {gate for _, gate in DEFECTS}


def _cases(found, gate):
    """The outcomes of the cases of `gate`: a parametrized gate has one for each resource."""

    return {case: outcome for case, outcome in found.items() if case.partition("[")[0] == gate}


@pytest.mark.parametrize(("defect", "gate"), DEFECTS)
def test_each_gate_fails_on_its_own_defect(outcomes, monkeypatch, defect, gate):
    monkeypatch.setenv("COMPLIANT_SERVER_DEFECT", defect)

    failing = _cases(outcomes("tests/test_protocol.py", "-k", gate), gate)

    assert failing
    assert set(failing.values()) == {"failed"}


# The prompt and resource gates.
SURFACE = (P8_PROMPTS, P8_MESSAGES, P8_RESOURCES, P9)
# Defects seen in the unified profile, which lists all four prompts and serves every resource:
# those that need a prompt to show in, and the resource defects in each resource.
UNIFIED_DEFECTS = [
    ("prompt-missing", P8_PROMPTS),
    ("prompt-missing", P8_MESSAGES),
    ("argument-undeclared", P8_PROMPTS),
    ("prompt-omits-tool", P8_MESSAGES),
    ("prompt-empty", P8_MESSAGES),
    ("uri-differs", P8_RESOURCES),
    ("resource-no-ttl", P9),
    ("resource-no-provenance", P9),
    ("resource-other-content", P9),
    ("resource-wrong-mime", P9),
]
# Six resources, the data element with two URI templates.
RESOURCE_READS = 7


@pytest.fixture
def unified(compliant, monkeypatch):
    """The compliant server serving the unified profile, tested as such."""

    monkeypatch.setenv("NCI_SI_ACCEPTANCE_PROFILE", "unified")
    monkeypatch.setenv("COMPLIANT_SERVER_PROFILE", "unified")


def test_the_prompt_and_resource_gates_pass_in_the_unified_profile(unified, outcomes):
    found = outcomes(*(f"tests/test_protocol.py::{gate}" for gate in SURFACE))

    assert set(found.values()) == {"passed"}
    assert len(_cases(found, P9)) == RESOURCE_READS


@pytest.mark.parametrize(("defect", "gate"), UNIFIED_DEFECTS)
def test_each_prompt_and_resource_gate_fails_on_its_own_defect_in_the_unified_profile(
    unified, outcomes, monkeypatch, defect, gate
):
    monkeypatch.setenv("COMPLIANT_SERVER_DEFECT", defect)

    failing = _cases(outcomes("tests/test_protocol.py", "-k", gate), gate)

    assert set(failing.values()) == {"failed"}
    assert len(failing) == (RESOURCE_READS if gate == P9 else 1)
