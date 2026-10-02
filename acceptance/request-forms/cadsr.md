# Upstream request forms, for the caDSR team

Generated from `acceptance/fixtures/manifest.yaml` by `pdm run acceptance-register`; do not edit by hand.

The upstream requests the acceptance suite's fixtures answer, each with the platform operation it
serves (*MCP API Specification* §10, or the requirement where the operation is missing) and why
it has this form. They are initial forms furnished by the government: from the published API
documentation and live checks where the operation exists, a draft where it does not. They are
initial versions, furnished for the EVS and caDSR teams to refine as needed, each change with the
approval of the branch chief or a delegate.

Two kinds of request are answered whatever their form: EVS concept requests, by rules over one
recording per concept (below), and requests whose parameters the service is shown to ignore.

## Operations without a pinned form upstream

These are served unpinned today. The verified fallback is approved for the prototype: the tool
calls the unpinned form, compares the release the payload reports with the one requested, and
fails closed on a difference. NCI's approval under EVS SOW v2.1 item 5 is taken from this list. A
mapset that reports a version of its own, not an NCIt release, cannot be verified that way: it is a
content state of its own, named in provenance and not presented as release-verified.

None yet.

## Requests

None yet.

## Scenarios

Each scenario provokes one case (*Acceptance Suite* §2.3); its fixtures answer before the ordinary
ones while a test selects it. A recorded fixture is what the service answers today. A crafted one
stands in for a case the service does not produce on demand, under the requirement it names, and
answers the ordinary forms above.

None yet.
