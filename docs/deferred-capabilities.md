# Deferred orchestration and gateway capabilities

**Decision, 8 October 2026:** the owner directed that [#180](https://github.com/CBIIT/nci-si-mcp/issues/180)
and the remaining runtime/adoption work in [#181](https://github.com/CBIIT/nci-si-mcp/issues/181)
move to **Backlog**, remain open and be marked deferred. Phase 6 delivers the portable access
boundary and evaluation/desk-assessment evidence, without an internal LLM, an `ask` tool or a
wxMCP adapter. This is a prioritization decision, not rejection of the underlying ideas.

Design credit: **Semantic Infrastructure (SI) team**, *MCP Architecture*, SI Team Meeting,
1 October 2026. Their proposed server was not built; this codebase supersedes the proposal.
This document develops the team's orchestration and integration suggestions while preserving
the validated semantic core. The [architecture ledger](decisions/001-si-architecture-alignment.md)
and `spec/` retain the operative contracts; this research strategy changes none of them.

## Why defer

| Issue | Delivered evidence | Deferred work and reason |
| --- | --- | --- |
| #180: evidence-backed `ask` orchestration | [Offline evaluation mechanics](assisted-evaluation.md): 12 tasks, two fixed-recipe arms, three repetitions; actual MCP calls and outcome scoring | Actual-model research, server pilot and shipping. The 72 runs do not measure LLM planning, analyst benefit or server-side superiority. Provider, full-transcript handling, spend, generated-capability amendment and shipping criteria remain unapproved. An external agent can test the need before the server takes on model dependencies, generated outputs and admission/cancellation costs. |
| #181: wxMCP integration | [Version-pinned desk assessment](wxmcp-assessment.md), including source limitations and unexecuted conformance checks | Hosted/runtime comparison, any adapter and adoption. There is no demonstrated consumer requirement, approved runtime entitlement, experiment ceiling or measured advantage over direct MCP. The pinned implementation has prompt/resource catalogue limitations; preserving our semantics may require additional glue. Those source observations are not a failed runtime test or a judgment about every IBM product. |

Neither deferral weakens mandatory authentication, authorization, evidence, release or cache
requirements. Production identity/hosting and live credentialed caDSR validation remain separate
unvalidated dependencies. A gateway is not required merely because an orchestrator uses MCP;
the two backlog decisions must be evaluated independently.

## Relevant public precedents

Sources below were consulted on 8 October 2026. They establish possible approaches, not
project-specific authorization, funding, permitted data or production readiness.

- [NIH Cloud Lab](https://cloud.nih.gov/resources/cloudlab/) provides eligible participants
  up to 90 days and $500 in cloud credits for public/non-sensitive experimentation, including
  GenAI. It is a useful precedent for a bounded pilot, not a sensitive-data production service.
- [NIH's account of NHLBI Chat](https://irp.nih.gov/catalyst/32/2/news-you-can-use-nhlbi-chat)
  describes an agency-controlled pilot and sensitive-information restrictions. This historical
  example does not establish current NCI API approval.
- [GSA USAi](https://www.gsa.gov/artificial-intelligence) offers a unified model API and usage
  monitoring. Its [rules](https://www.usai.gov/rules-of-behavior/) require minimum necessary
  data, human validation and agency-policy compliance. Its [privacy policy](https://www.usai.gov/privacy/)
  describes interaction logging and agency records obligations: provider isolation is not
  the same as no retention. Confirm actual agency eligibility and programmatic access.
- [IDC AI assistants](https://learn.canceridc.dev/ai-assistants/agents), also linked by
  [NCI GDC](https://gdc.cancer.gov/content/gdc-and-artificial-intelligence-ai), already use
  MCP to discover valid filters, refine and size cohorts, and supply citations. This is direct
  cancer-research precedent for external orchestration, not proof that an LLM belongs inside SI.
- [CRDC core services](https://datacommons.cancer.gov/explore/core-standards-services) describe
  CDA metadata discovery and separate controlled-file access. The [NCI trials API](https://www.cancer.gov/syndication/api)
  exposes public CTRP information but excludes biomarker, regulatory and all accrual data.
  A proposed workflow must respect these actual source boundaries.

Provider availability must be rechecked at experiment entry. The GSA page announces removal
of Anthropic integrations while the USAi landing page still lists that provider; a public
product list is not an approved-provider list. Likewise, [Bedrock retention](https://docs.aws.amazon.com/bedrock/latest/userguide/data-retention.html)
and [cross-region inference](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html)
are deployment/model-specific. AWS hosting, no-training terms and a compliance label alone do
not settle retention, geographic routing or the project's authorization boundary.

## Research strategy and signals to reconsider #180

Start with domain-reviewed tasks and a deterministic baseline, then an external agent using
the existing SI MCP plus explicitly approved resource connectors. CTRP/CDA/GDC/IDC discovery
is not supplied by the current SI tool catalogue. Reuse compatible services or scope narrow
read-only connectors; do not imply their presence or silently add a new platform client.

| Candidate use case | Potential orchestration benefit | Evidence needed; failure to avoid |
| --- | --- | --- |
| CRDC cohort discovery: find disease-specific imaging and genomic resources | Clarify disease scope, resolve SI terminology, inspect actual resource filters and adapt the query | Correct counts and identifiers, useful analyst time savings, explicit missing modalities. Collection-level co-occurrence must never imply matched subjects; joins require documented identifiers and source evidence. |
| Public CTRP trial landscape: narrow trials by disease and available criteria | Explain terminology and interactively refine supported filters | Correct, cited public trial records and explainable refinements. No patient eligibility decisions or inferred biomarker/accrual facts absent from the public source. |
| Dictionary harmonization: map ambiguous study variables | Ask whether a variable means diagnosis, history, measurement or treatment before selecting candidates | Domain reviewers prefer supported mappings and useful clarification over current workflows. Preserve competing mappings, unknowns and unmatched columns; begin with public/synthetic dictionaries and contract-crafted caDSR fixtures. |
| Trial-to-data research feasibility | Connect trial terminology with public resource discovery and explain coverage limitations | An evidence-backed feasibility assessment. Shared disease or intervention does not establish shared participants, comparable populations or sufficient analytic data. |

Prefer SI plus IDC metadata discovery as the first experiment: there is a documented MCP path
and no need to download images. Cross-commons linkage and trial-to-data analysis are later,
more demanding tasks. Only use approved metadata projections; public accessibility is not
blanket permission to send participant-level records or confidential research intent to a model.

1. **Entry specification:** answer the questions below and obtain the explicit experiment
   decision. Do not buy services, introduce an executable pilot or send transcripts beforehand.
2. **Task design:** propose 24 domain-reviewed tasks, with a development/held-out split and
   ambiguity, empty results, unavailable services, malicious source text and unsupported joins.
   Keep held-out answers from prompt development. Freeze releases, metadata snapshots and tools.
3. **External-agent comparison:** run deterministic and external-agent approaches against the
   same tasks, with three repetitions and pre-agreed resource limits. Record incomplete runs,
   refusals and costs; independently review factual support. The existing fixed-recipe evidence
   remains mechanics evidence only, not an actual-model baseline.
4. **Need decision:** measure task correctness, analyst time, unsupported claims/joins, useful
   clarifications, evidence coverage, latency and total cost. Agree minimum meaningful benefit
   with domain reviewers before running. Report uncertainty and failure categories, not only
   averages; zero observed security failures in a small sample is not proof of safety.
5. **Server-placement decision:** only if a real unmet need remains, compare a bounded internal
   candidate with the external agent under comparable model, data and budgets. A demonstrated
   need for orchestration alone is insufficient: show why server placement improves evidence
   enforcement, reproducibility or caller experience enough to justify its operational costs.
6. **Shipping decision:** require separate owner approval, the A9.2 capability amendment,
   specified public schemas/annotations and paired held-out evidence. Remove an unsuccessful
   unshipped candidate and its unused dependencies; retain the honest evaluation record.

Reopen implementation when tasks show repeatable benefit that deterministic workflows cannot
reasonably provide, the approved external approach cannot meet a documented requirement, and
the bounded server candidate satisfies pre-registered benefit and safety criteria. A repeated
fixed sequence, a model demonstration or provider availability alone is not that signal.

## Questions that define a model experiment

All numeric limits below are **suggestions for approval**, not authorized spending or existing
government policy. Use the smallest experiment capable of answering the need question.

| Question / specification | Suggested answer or way to obtain one |
| --- | --- |
| Which provider, model revision and endpoint may the application call? | Ask NCI/CBIIT whether approved API access already exists through an agency service, USAi or Cloud One. Employee chat entitlement does not establish server API entitlement. Pin endpoint, model revision, regions and contract; compare an economical and stronger model only if both are permitted. |
| What may enter the complete transcript? | Begin with synthetic questions, public terminology/dictionaries and approved public metadata projections. Classify caller input, retrieved/licensed text, tool arguments/results, traces and attachments with data owners. Exclude patient narratives, controlled genomics, internal submissions/accrual and credentials. A public-data query can itself disclose an unpublished hypothesis. |
| What is retained, by whom and for how long? | Separately specify provider abuse logs/state/caches, application logs and retained evaluation records. Prefer no provider training and the least retention allowed by the approved service; verify actual configuration. Follow agency records schedules and holds for evidence rather than inventing a universal deletion period. Obtain privacy/security and records-owner answers. |
| Where may data leave the process? | Allowlist exact providers, resource origins and regions. Disable arbitrary URLs, generated SQL/SPARQL, shell execution and downloads for the first pilot. Validate redirect and routing behavior; no silent cross-region or global fallback. Data minimization must occur before every provider/tool dispatch. |
| What are the hard spend and resource boundaries? | Propose a $250 total ceiling, at most ten tool calls per task, bounded tokens/deadlines/retries and sequential planning. Price the frozen task set before approval; include inference, compute, storage and egress. Reserve worst-case cost before dispatch, cap concurrency and stop on exhaustion. Alerts alone are not enforcement; use approved account-wide controls rather than creating a quota service. |
| How does a generated answer remain trustworthy? | Host-built evidence ledger from successful retrieved outputs; unchanged cited records, exact identities/releases and distinct generated prose. Fail or report partial on errors, unsupported claims, ambiguity or truncation. Domain review must assess prose support: valid citations alone are insufficient. |
| Whose permission applies, and what happens on cancellation? | Inherit the caller's authority and aggregate request budget at every child step. No inferred privileges or recursive planners. Stop new calls after expiry/cancellation; bound in-flight work and report provider cancellation limitations. Test concurrent spend exhaustion and direct-tool responsiveness. |
| What counts as useful and safe enough to ship? | Have domain and security reviewers pre-register acceptable task accuracy, analyst benefit, evidence support, latency/cost and direct-tool guardrails. Unauthorized execution, invented evidence or joins block shipping; report all refusals and incomplete runs. Agree who owns prompt/model changes and rollback. |

## Signals and experiment specification for #181

Reconsider wxMCP when an identified NCI consumer needs a platform capability such as an existing
approved API catalogue, supported operational integration or a mandated gateway path that direct
MCP cannot supply adequately. Ask that consumer and platform owner to demonstrate the gap.
Do not introduce a gateway solely to wrap working MCP tools or to obtain an LLM orchestrator.

| Question / indicator | Suggested next evidence |
| --- | --- |
| What concrete consumer or operator requirement is unmet? | Name the workflow, owner and measurable benefit; compare direct MCP behind the approved boundary first. Distinguish platform policy from convenience. |
| Which IBM package/runtime and integration path are available? | Refresh the pinned desk assessment; establish runtime entitlement, support, hosting/access and exact versions. Separate API-catalog and local Flow Service paths, including their different credential contracts. |
| Is a narrow adapter sufficient? | Trace the three existing representative operations: pinned EVS/error, caDSR masked error and cross-domain workflow with provenance/bounds. Reuse handlers; reject broad REST exposure or duplicated domain logic. |
| Does the gateway preserve security and MCP semantics? | Test audience/token exchange, principal/cache isolation, schemas/errors, tools/prompts/resources, session pins, correlation and request bounds using the unchanged acceptance reference. Token pass-through is not assumed to be approved delegation. |
| Is its operating benefit worth the cost? | Approve an engineering ceiling (suggested two working days for an initial feasibility spike), paid/runtime ceiling and support owner before execution. Measure conformance, added latency, deployment/upgrade effort and failure recovery against direct MCP. |
| When must the experiment stop? | Stop on unavailable entitlement, incompatible credentials, weakened conformance, broad new API/duplicate logic or budget exhaustion. Keep failed and untested rows visible. Adoption needs a separate scoped decision; feasibility is not deployment approval. |

## Backlog and completion records

Both issues remain open in Backlog with a link to this document; no milestone closing keyword
should close them. #181's completed desk assessment remains credited separately from its
deferred runtime work. Review the decisions when a concrete consumer request, approved model
environment or platform opportunity supplies the missing evidence; elapsed time alone does not
authorize implementation. Phase 6's [assurance record](phase-6-assurance.md) tracks delivered
behavior and release verification without representing either deferred capability as shipped.
