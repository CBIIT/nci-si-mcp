# Domain-user stories and user acceptance plan

Status: planned; UAT has not run.

These four proposed journeys need confirmation by domain users. The existing
[behavioral stories](behavioural-tests.md) include curator and analyst roles and document
automated operation contracts; they do not establish that users completed these journeys.
Delivering this plan does not establish user acceptance.

## Client and readiness

Owner decision, 25 October 2026: NCI/NIH users have access to Claude Code (desktop),
ChatGPT (Codex) and GitHub Copilot. All three can host a local stdio MCP server.
Decision owner: NCI client/platform owner.

Before each client's session, record NCI's permission to connect a local MCP server and
the model, version and provider. These remain unconfirmed per client; if any is missing,
record that client as blocked. Never substitute another client.
The facilitator retains the permission decision link and recruits the confirming roles below;
no participants or signoffs are recorded yet.

The owner also confirms access to AWS Bedrock models through Cloud One. Bedrock is a model
provider, not an MCP client. Record the client separately from its provider: direct or Bedrock
via Cloud One. Each client/provider pairing is a separate acceptance configuration.

Use the unified profile: all four [specified prompts](../spec/prompts.yaml) need EVS and caDSR.
Each prompt starts with `resolve_release`; retain its NCIt release for later calls and leave
`registryRelease` unset while caDSR publishes none. Preserve provenance, absent fields,
errors and truncation in the answer. No missing upstream content is inferred or backfilled.
Credentialed caDSR CDE Match and lists-of-values evidence remains contract-fixture evidence
until credentials are issued; an upstream or access gap can block a scenario.

## Story: `protocol_authoring`

Role: protocol author.
Need: choose reusable data elements for the concepts collected by a protocol.
Benefit: explain the choice of established capture definitions rather than recreate them.
Prompt: `protocol_authoring`, with `concepts` containing the protocol's NCIt codes.
Tools: `resolve_release`, `find_data_elements_for_concept`, `get_data_element`, `get_form`.

Given: a protocol concept list and, in a second variant, an existing form public ID.
When: the author requests candidates, compares their context, workflow status, registration
status and permissible values, and asks for form wording only when its public ID is known.
Then: the answer identifies the selected CDE, rejected alternatives and reasons, and the release;
with the form ID it also preserves question wording and module order.
Pass criteria: the author can justify reuse from returned metadata; absent statuses are marked
absent, not assumed standard/current. A class-level request considers descendants. No form
keyword search or invented form ID is presented as supported.
Role confirms: protocol author responsible for the protocol's data capture definitions.
Gaps: automated checks cover returned metadata, not whether the author's selection is useful;
form discovery without a public ID is unavailable. A release/status preference is model guidance.
Evidence: operation checks below support the scenario, not a completed client conversation.

- CDE fields and statuses: [`acceptance/tests/test_cadsr.py::test_a_data_element_is_its_own_fields_alone_with_its_version_and_statuses`](../acceptance/tests/test_cadsr.py).
- Concept/descendant candidates: [`acceptance/tests/test_cross_domain.py::test_the_data_elements_are_the_concept_s_and_with_expansion_its_descendants_too`](../acceptance/tests/test_cross_domain.py).
- Form modules/status: [`acceptance/tests/test_cadsr.py::test_a_form_returns_its_modules_and_its_status_unchanged_a_retired_one_too`](../acceptance/tests/test_cadsr.py).

## Story: `crdc_model_alignment`

Role: data-commons engineer.
Need: align a CRDC model field with its CDE and the literals a commons actually stores.
Benefit: avoid joins that silently substitute a preferred concept term or mix content states.
Prompt: `crdc_model_alignment`, with `field` and `commons`.
Tools: `resolve_release`, `get_code_map`, `resolve_stored_value`, `get_release_alignment`.

Given: a model field (or CDE public ID), its commons, and a case without value-level binding.
When: the engineer obtains the map by ID or pages by context to the exact `crdcName`, reads
coverage and `valueLevelBinding`, resolves concept codes, and checks release alignment.
Then: the answer distinguishes actual stored literals, confidence/evidence and unavailable
bindings, and explains `intervalDays` and any alignment warning before recommending a join.
Pass criteria: every reported stored literal is supported by its returned binding; the unbound
case reports no stored value, never the preferred term as a literal. Content-state differences
remain visible, including warnings; the engineer can explain which joins are usable and why.
Role confirms: data-commons engineer responsible for the model field's mapping.
Gaps: no client-model interpretation or usable-join decision has been user-validated; absent
upstream value bindings cannot be reconstructed by this server.
Evidence: mapping and alignment contracts are checked independently of the conversation.

- Map coverage/binding: [`acceptance/tests/test_cadsr.py::test_a_code_map_is_a_data_element_s_values_users_and_coverage`](../acceptance/tests/test_cadsr.py).
- Stored literal: [`acceptance/tests/test_cross_domain.py::test_a_gdc_value_resolves_through_the_mapset_its_source_names`](../acceptance/tests/test_cross_domain.py).
- No binding: [`acceptance/tests/test_cross_domain.py::test_a_commons_without_a_value_level_binding_stores_no_value_and_says_so`](../acceptance/tests/test_cross_domain.py).
- Alignment warning: [`acceptance/tests/test_cross_domain.py::test_every_dataset_is_dated_with_the_largest_interval_and_a_warning_above_the_threshold`](../acceptance/tests/test_cross_domain.py).

## Story: `uscdi_cancer_curation`

Role: USCDI+ Cancer curator.
Need: review a candidate NCIt concept and caDSR reuse for a cancer data element.
Benefit: document a defensible curation choice and expose values still needing review.
Prompt: `uscdi_cancer_curation`, with `element` and optional `values`.
Tools: `resolve_release`, `search_concepts`, `ground_value`, `match_value_meanings`.

Given: an element name, once with example values and once without them.
When: the curator reviews search alternatives (active before retired), grounds the selected
concept, and compares supplied examples with the permissible values via value-meaning matching.
Then: the answer gives the chosen concept/reason/status, rejected alternatives, CDE registration
statuses, unmatched values and releases; without examples it skips value-meaning matching.
Pass criteria: the curator can trace the recommendation to the search/grounding results and
explain each rejection; the two variants obey the optional-values rule. Missing matches and
statuses remain visible rather than being manufactured to complete the checklist.
Role confirms: curator responsible for the USCDI+ Cancer element's terminology review.
Gaps: automated hop/match contracts do not prove the model chose the right concept or skipped
an optional call; both decisions and the checklist's usefulness require the recorded UAT trace.
Evidence: these checks protect grounding and returned value meanings, not curation signoff.

- Grounding hops: [`acceptance/tests/test_workflow.py::test_each_hop_is_what_its_surface_answers_for_the_concept`](../acceptance/tests/test_workflow.py).
- Value meanings: [`acceptance/tests/test_cadsr.py::test_value_meaning_matches_are_the_platform_s_in_its_order_with_their_rule`](../acceptance/tests/test_cadsr.py).

## Story: `cross_program_harmonization`

Role: harmonisation analyst.
Need: compare two programs' dictionaries through shared CDEs and concepts.
Benefit: distinguish genuine overlap from different capture definitions and unresolved columns.
Prompt: `cross_program_harmonization`, with `first_dictionary` and `second_dictionary`.
Tools: `resolve_release`, `harmonize_data_dictionary`, `get_data_element`, `find_data_elements_for_concept`.

Given: two dictionaries with descriptions/sample values, an unmatched column, shared CDEs,
and different CDEs associated with the same concept.
When: the analyst harmonises each dictionary in batches of at most ten columns, compares
results, and inspects differing CDEs with `include conceptAssociations` before recommending reuse.
Then: the answer distinguishes same-CDE overlaps from same-concept/different-CDE divergences,
supports a recommended CDE through concept lookup, and retains each program's unmatched columns.
Pass criteria: no column disappears; sample-value alignment remains attributable to returned
value meanings. Shared concepts are not declared identical capture definitions. The analyst
can accept or reject each recommendation from the returned definitions and provenance.
Role confirms: harmonisation analyst responsible for the two-program comparison.
Gaps: CDE Match needs credentials for live evidence; automated column tests do not establish
the two-dictionary comparison or the domain suitability of a recommendation.
Evidence: single-dictionary operation checks support, but do not execute, this client journey.

- Columns/unmatched: [`acceptance/tests/test_workflow.py::test_each_column_is_matched_once_and_the_unmatched_are_listed`](../acceptance/tests/test_workflow.py).
- Sample alignment: [`acceptance/tests/test_workflow.py::test_a_column_s_sample_values_align_to_the_value_meanings_vmmatch_gives`](../acceptance/tests/test_workflow.py).

## UAT session and decision

After that client's readiness is confirmed, the facilitator and each confirming role agree realistic
permitted inputs for that story and its variants, then run the named prompt in the approved
client. Capture the first attempt before any correction, retry or changed input. Keep later
attempts separately linked, with their reason; a corrected answer does not erase the original.
The participant reviews the transcript against every pass criterion and records accept, reject
or blocked with reasons and defects. The facilitator records evidence, not inferred signoff.
Acceptance of one story is not acceptance of the other three. Automated fixture passes and
assisted-task evaluation are supporting evidence, not NCI user confirmation or completed UAT.
A story is accepted only for the client, model, version and provider in its recorded run.
Results do not establish acceptance for another client or provider configuration.

## Evidence record

Keep one record per story, client/provider configuration, participant and attempt, with references
to the retained artifacts:

- `story`: prompt name, scenario/variant, agreed inputs and expected pass criteria.
- `participant`: confirming role, session date and explicit confirmation record; no assumed signoff.
- `environment`: client name and version, model name and version, model provider (direct or
  Bedrock via Cloud One), NCI local-MCP permission decision link, transport, profile, server
  version and commit SHA.
- `content`: NCIt release, caDSR registry state/date, live or fixture mode, index build if used.
- `conversation`: submitted prompt arguments and complete conversation, including model choices.
- `trace`: ordered tool names, arguments, results, provenance/correlation IDs, errors, truncation,
  first-attempt artifacts and separately identified retries/corrections.
- `decision`: each criterion's observed outcome, participant accept/reject/blocked and reason,
  defect links, unresolved upstream/access gaps and follow-up owner.
