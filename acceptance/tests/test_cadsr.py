"""The caDSR tools' own requirements (spec/requirements.yaml), and the cross-cutting ones only a
caDSR answer shows, each test against its tool.

What a test expects it reads from the recordings (the `recorded` fixture). A fact it cannot
read there is named beside it, with the fixture file that holds it.
"""

from http import HTTPStatus

import pytest

from nci_si_acceptance.results import error_code

# Recorded both ways: recorded/cadsr/data-element-2200604.json answers a request that names
# Accept: application/json, data-element-2200604-html.json, with HTML, any other.
DATA_ELEMENT = "2200604"
# Unknown to caDSR, which answers HTTP 200 with DataElement null (data-element-unknown.json).
UNKNOWN = "99999999"
# No number, which caDSR refuses with HTTP 200 and apiResponse type E (data-element-refused.json).
REFUSED = "notanumber"


def _accepts(log):
    """The Accept header of each caDSR request in `log`."""

    return [
        {name.lower(): value for name, value in entry["headers"].items()}.get("accept")
        for entry in log
        if entry["surface"] == "cadsr"
    ]


# A server that answered the same call before may serve it from its cache, asking nothing.
@pytest.mark.own_server
@pytest.mark.tool("get_data_element")
@pytest.mark.requirement("X-15")
def test_a_server_that_leaves_out_accept_gets_html_and_never_parses_it(tools, upstream):
    before = len(upstream.log())

    result = tools.call("get_data_element", {"publicId": DATA_ELEMENT})

    accepts = _accepts(upstream.log()[before:])
    assert accepts
    if result.is_error:
        assert error_code(result) == "upstream_unavailable", result.content
    else:
        # The content came from JSON: every request asked for it, as the contracts prescribe.
        assert set(accepts) == {"application/json"}
        assert result.content.get("publicId") == DATA_ELEMENT


@pytest.mark.tool("get_data_element")
@pytest.mark.requirement("X-15")
def test_a_failure_inside_an_http_200_is_an_error_never_an_empty_success(tools, recorded):
    answer = recorded("recorded/cadsr/data-element-unknown.json")["response"]
    # caDSR answers the unknown id with HTTP 200, no data element and a note that says so.
    assert (answer["status"], answer["body"]["DataElement"]) == (200, None)

    result = tools.call("get_data_element", {"publicId": UNKNOWN})

    assert error_code(result) == "not_found", result.content


@pytest.mark.scenario("cadsr/html-for-json")
@pytest.mark.tool("get_data_element")
@pytest.mark.requirement("X-15")
def test_html_where_json_was_asked_for_is_an_upstream_error(tools, recorded):
    answer = recorded("scenarios/cadsr/html-for-json/data-element-2200604.json")["response"]
    # The scenario answers the request for JSON with HTML and HTTP 200.
    assert answer["status"] == HTTPStatus.OK
    assert answer["body"].startswith("<BODY")

    result = tools.call("get_data_element", {"publicId": DATA_ELEMENT})

    assert error_code(result) == "upstream_unavailable", result.content


@pytest.mark.tool("get_data_element")
@pytest.mark.requirement("X-15")
def test_a_refusal_inside_an_http_200_is_an_invalid_request(tools, recorded):
    answer = recorded("recorded/cadsr/data-element-refused.json")["response"]
    assert (answer["status"], answer["body"]["apiResponse"]["type"]) == (200, "E")

    result = tools.call("get_data_element", {"publicId": REFUSED})

    assert error_code(result) == "invalid_request", result.content
