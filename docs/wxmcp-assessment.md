# WxMCPServer desk assessment

**Owner decision: defer runtime integration and adoption; retain direct MCP.**
[#181](https://github.com/CBIIT/nci-si-mcp/issues/181) remains open in Backlog; the
[deferral and research strategy](deferred-capabilities.md) records the entry criteria. This is a source-based assessment,
not a deployed gateway test, certification or judgment about every IBM offering.

Design credit: the **Semantic Infrastructure (SI) team**, *MCP Architecture*, SI Team
Meeting, 1 October 2026, which proposed examining wxMCP. Their proposed server was not
built; this repository supplies the semantic implementation.

## Version and evidence boundary

Primary sources were read on 8 October 2026 at IBM/WxMCPServer commit
`ad2c975369fd2079151d6589eae16679b29e9044` (commit date 10 August 2026):

| Source | Observed fact |
| --- | --- |
| [Package manifest](https://github.com/IBM/WxMCPServer/blob/ad2c975369fd2079151d6589eae16679b29e9044/manifest.v3) | Package version 1.3.2, development status |
| [README](https://github.com/IBM/WxMCPServer/blob/ad2c975369fd2079151d6589eae16679b29e9044/README.md) | Targets MCP 2025-06-18; hosting requires Integration Server or Microservices Runtime, tested with 11.1; API-catalog integration exposes existing APIs as tools |
| [API descriptor](https://github.com/IBM/WxMCPServer/blob/ad2c975369fd2079151d6589eae16679b29e9044/resources/APIs/WxMCP-Server/WxMCP-Server-API-1.5.yaml) | Descriptor version 1.5.0; JSON-RPC POST `/mcp`, configurable parameter prefixes and structured/text/both results |
| [Local-flow instructions](https://github.com/IBM/WxMCPServer/blob/ad2c975369fd2079151d6589eae16679b29e9044/resources/IS/README.md) | A separate path derives tools from OpenAPI-based local Flow Services and OAuth scopes; does not require the external Tool Catalog API |
| [Initialize handler](https://github.com/IBM/WxMCPServer/blob/ad2c975369fd2079151d6589eae16679b29e9044/ns/wx/mcp/server/services/mcpInitialize/flow.xml) | Reports server version 1.0.0 and echoes the requested protocol version; this is not proof of compatibility with that version |
| [Package license](https://github.com/IBM/WxMCPServer/blob/ad2c975369fd2079151d6589eae16679b29e9044/LICENSE) | Apache-2.0 package license; runtime entitlement and support terms were not established |

Package, descriptor and reported server versions are distinct. A future experiment must pin
all of them and the runtime image/configuration, not cite an unqualified “latest.” No IBM
runtime access, entitlement, effort/spend ceiling or hosted experiment has been approved here.
No package was installed or executed. No credentials or service resources were acquired.
The API descriptor's authentication enum also lists only `API_KEY` and `OAUTH`, whereas the
local-flow instructions describe `INTERNAL` and `THIRD_PARTY`; resolve that contract difference
against the chosen runtime before generating a client or configuring its trust boundary.

The direct reference is this repository's `6b8e911d8885d1398efbb850ae0667be9f34fa47`
milestone commit, its locked MCP SDK 2.2.0 and 964 fixture acceptance cases. Its supported
protocol evidence includes 2026-07-28 and 2025-11-25. Fixture-backed caDSR behavior remains
distinct from live credentialed validation. [#186](https://github.com/CBIIT/nci-si-mcp/pull/186)
records passing direct-server CI, including HTTP fixture acceptance.

## Compatibility matrix

“Untested” below means no integration execution, not a passing or failing runtime test.
“Source limitation” is an observed implementation or documented constraint at the pinned
commit. Proposed adapter work is an inference, not an implementation commitment.

| Requirement / representative case | Direct reference | Pinned wxMCP evidence and remaining check |
| --- | --- | --- |
| Release-pinned EVS concept and unknown code | `get_concept`, X-1/X-3/X-15; retrieved identity/release and structured errors | Untested. An API wrapper needs our handler's release verification and error normalization; calling EVS directly would bypass them. Compare C4817 and unknown-code cases field by field, including provenance. |
| caDSR element and HTTP-200 error envelope | `get_data_element`, X-15; recorded/crafted contracts | Untested. JSON transport alone does not establish webMethods envelope classification. Run the existing masked-success fixtures through the reused handler; no live caDSR claim. |
| Grounding workflow, graph mismatch and bounds | `ground_value-1` through `ground_value-4` and release checks | Untested. Requires the existing Python workflow or new glue to it; recreating this logic in Flow Services is outside scope. Preserve graph identity, partial counts and child permissions. |
| Exact tool names, arguments and schema | Registry-derived contracts and P-1/P-2 | Untested mapping. Documented generated names/prefixes and OpenAPI import require comparison against every public signature, not just successful invocation. |
| Structured errors and outputs | Protocol error flag plus shared error record | Structured response mode is documented, but our output unions, correlation and `isError` semantics are untested. HTTP status alone is insufficient. |
| Prompts | Existing guided workflow prompts | Source limitation: [prompt list handler](https://github.com/IBM/WxMCPServer/blob/ad2c975369fd2079151d6589eae16679b29e9044/ns/wx/mcp/server/services/mcpPromptsList/flow.xml) returns an empty list. It does not preserve our prompt catalogue. |
| Resources and templates | Two concrete resources and five templates | Source limitation: [resource list](https://github.com/IBM/WxMCPServer/blob/ad2c975369fd2079151d6589eae16679b29e9044/ns/wx/mcp/server/services/mcpResourcesList/flow.xml) and [template list](https://github.com/IBM/WxMCPServer/blob/ad2c975369fd2079151d6589eae16679b29e9044/ns/wx/mcp/server/services/mcpResourcesTemplatesList/flow.xml) return empty lists. |
| Protocol negotiation / transport | Real stdio, HTTP, legacy and single-exchange tests | Untested interoperability. Echoing a version is not negotiation evidence. Validate initialize, notifications, errors, headers and session lifecycle with our supported versions. |
| Authentication and credential delegation | Separate inbound authority and origin-bound upstream credentials | README acknowledges token pass-through on the API path as a remaining limitation. Local-flow authentication is a different path; do not generalize that limitation to it. Audience/token exchange, issuer/tenant/client ownership and no forwarding of SI bearer tokens require explicit verification. |
| Caller capability and cache isolation | X-25–X-28; per-request policy and private/no-store responses | Untested. Product/scoped discovery alone does not prove nested-call denial, revocation, cross-principal isolation, cursor authorization or protected headers. Run existing denied-child and replay cases unchanged. |
| Correlation and safe audit | A6.2, X-12 and completion records | Untested end-to-end propagation/redaction across an additional hop. Compare success, denial, invalid input and upstream failure, including actual attempt counts. |
| Release/session consistency | X-22; explicit overrides, implicit pins, replica/restart tests | Untested. A second server cannot silently substitute per-request selection for a session pin. Test affinity, wrong replica, restart and principal changes. |
| Capability refusal and request bounds | No invented data; `capability_unavailable`, shared budgets | Untested. Preserve absent caDSR features, retries, sentinel rows, truncation and total workflow budget. Gateway retries must count against the same effective ceiling. |
| Content forms | JSON MCP output; some upstream operations consume form bodies/export text | Documented API-path limitation: JSON sending/receiving only. It may wrap a JSON-facing SI handler; it is not evidence it can directly replace every upstream client. |
| Latency, resources and operations | [Measured fixture task baseline](assisted-evaluation.md) | Gateway latency, request overhead, resource use and reliability unmeasured. No comparative benefit/cost number is claimed. |

The requirement names above refer to this repository's [specification](specification.md), not
claims about IBM conformance. The full acceptance suite remains unchanged; these representative
cases would precede, not replace, full validation of an adopted path.

## Integration value and cost

The API-catalog path could reuse an organization's existing API inventory and policies.
However, this server already exposes MCP directly and its semantic handlers are Python, not
an OpenAPI business API or local webMethods Flow Services. **Inference:** preserving those
handlers through either documented path would require additional integration glue; the sources
do not establish a transparent proxy for our existing MCP endpoint. A narrow adapter's necessity
and shape remain unproven. A broad new REST surface or duplicated domain implementation would
trigger this issue's stop condition.

An extra runtime/catalog/configuration and protocol mapping would add deployment, patching,
schema-drift and identity/session obligations. These are identified maintenance responsibilities,
not measured engineering hours or license prices. Direct MCP already has the portable governed
access boundary; production identity remains independently deferred. No measured integration
benefit currently offsets the demonstrated prompt/resource gaps and untested security semantics.

## Re-entry and repeatability

Before any experiment, obtain an owner-approved runtime/access arrangement, engineering/time/
spend ceiling and decision criteria. Prefer the smallest path that reuses handlers. Verify
audiences and any token exchange; never assume token pass-through is approved delegation.
Stop if the path requires broad REST exposure, duplicate semantics, weakened conformance or
exceeds the ceiling. Adoption needs its own scope and owner decision even if a trial passes.

To repeat the desk assessment, retrieve the pinned files linked above, compare their manifest,
descriptor and initialization versions, and inspect the three list handlers. For example:

```bash
gh api 'repos/IBM/WxMCPServer/contents/manifest.v3?ref=ad2c975369fd2079151d6589eae16679b29e9044' --header 'Accept: application/vnd.github.raw+json'
```

After approvals, run the three representative scenarios and then the existing harness against
the candidate endpoint, retaining every failure/unsupported/untested row. Record direct and
gateway build/configuration, runtime, protocol, release/data and equal request budgets. Report
all failed/incomplete runs, latency and operational overhead. Do not change fixture expectations
to make the integration pass. No gateway experiment or platform adoption is delivered by this
desk assessment. The owner approved deferral on 8 October 2026; runtime work remains in Backlog.
