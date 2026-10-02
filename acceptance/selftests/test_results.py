"""What the suite's tests read from a result and from the upstream request log."""

import pytest

from nci_si_acceptance.results import error_code, requests_naming
from nci_si_acceptance.tools import Result

RECORD = {"code": "invalid_request", "message": "no such include value"}


@pytest.mark.parametrize(
    ("is_error", "content", "code"),
    [
        (True, {"error": RECORD}, "invalid_request"),
        # Content beside the record, a record that is no object, or no error flag: no code.
        (True, {"error": RECORD, "concept": {"code": "C4817"}}, None),
        (True, {"error": "invalid_request"}, None),
        (True, ["invalid_request"], None),
        (True, "Error executing tool", None),
        (False, {"error": RECORD}, None),
    ],
)
def test_an_error_result_has_a_code_only_as_one_error_record(is_error, content, code):
    assert error_code(Result("get_concept", is_error, content, {})) == code


def entry(path, **params):
    return {"path": path, "params": {name: [value] for name, value in params.items()}}


def test_a_request_names_a_code_in_its_path_or_a_parameter_but_not_inside_another_code():
    log = [
        entry("/api/v1/concept/ncit_26.09d", list="C4817,C12578", include="summary"),
        entry("/api/v1/concept/ncit_26.09d/C116977"),
        entry("/api/v1/concept/ncit_26.09d/C48170"),
        entry("/api/v1/metadata/terminologies", terminology="ncit"),
    ]

    assert requests_naming(log, ["C4817", "C116977"]) == log[:2]
