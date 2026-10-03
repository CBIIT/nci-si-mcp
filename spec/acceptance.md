## 4. Acceptance

The acceptance suite (`acceptance/`, [README](../acceptance/README.md)) tests the requirements
against a server: in fixture mode against the recorded and crafted upstream answers of
`acceptance/fixtures/`, and in live mode for the tests marked live-capable. A run gives each
required tool one outcome:

| Outcome | Meaning |
|---|---|
| PASS | Every gate, every cross-cutting test and every test of the tool passes, in fixture mode and, for the live-capable tests, in live mode |
| PASS (fixture only) | Passes in fixture mode; each live failure is a test with a documented upstream limitation, named per test |
| FAIL | A test fails in fixture mode, or a live-capable test fails live without a documented upstream limitation |
| INCOMPLETE | The tests that ran passed, but others could not run for want of a capability: a hardening candidate, never PASS |
| NO FIXTURE | An upstream request found no fixture; the report names it |
| NOT RUN | No test of the tool ran, for example in a live run or without the operator's prepare step |
| NO TESTS | The suite has no test for the tool: a defect of the suite |
| NOT IMPLEMENTED | The server exposes the tool neither by name nor through the baseline tool map (the Prototype Baseline Assessment) |

An upstream limitation excuses a failing live test only test by test, each with its
requirement named; one known limitation does not excuse another live failure of the same tool,
and each such limitation is an entry in the upstream requirements package. A
gate that fails live fails every tool, as a failing live test does. A module is accepted when
every one of its tools is PASS or PASS (fixture only) and the gates pass; INCOMPLETE, NOT RUN, NO FIXTURE, NO TESTS and NOT IMPLEMENTED are not accepted.
Every report names the suite version, the fixture-set version,
a digest over the suite, and the tools whose tests have never run against an implementation.

### Settings the suite gives a server

The suite starts each server under test with these settings, and with no other `NCI_SI_*`
setting of the operator's environment. A server is configured by them: in fixture mode every
upstream base URL names the fixture server, and a scenario that needs a credential, a short
timeout or a log level sets it.

| Setting | When the suite sets it | Format |
|---|---|---|
| `NCI_SI_UPSTREAM_MODE` | Always | `fixture` or `live` |
| `NCI_SI_DATA_DIR` | Always | A directory of the server's own, fresh for each server; a copy of the prepare command's where one is given |
| `NCI_SI_EVS_BASE_URL`, `NCI_SI_EVS_FHIR_BASE_URL`, `NCI_SI_CADSR_BASE_URL`, `NCI_SI_CADSR_FTP_URL`, `NCI_SI_SSIS_FACADE_URL`, `NCI_SI_SSIS_SPARQL_URL` | Fixture mode | A base URL, to which the server adds the platform's own paths as it does to the production one (`…/evs` + `/api/v1/…`; `…/cadsr` + `/NCIAPI/1.0/api/…`) |
| `NCI_SI_EVS_LICENSE_KEY` | Live mode, from the operator; the `license/restricted` scenario | The key, sent as the `X-EVSRESTAPI-License-Key` header, only with licensed content |
| `NCI_SI_CADSR_CREDENTIAL` | Live mode, from the operator; the `cadsr/credentialed` and `cadsr/match-timeout` scenarios | `user:password`, sent as HTTP Basic authentication (`Authorization: Basic` and its base64), as every caDSR contract declares |
| `NCI_SI_TIMEOUT_SECONDS` | The `upstream/unavailable` scenario | Seconds an upstream request may take |
| `NCI_SI_MATCH_TIMEOUT_SECONDS` | The `cadsr/match-timeout` scenario | Seconds a caDSR match request may take |
| `NCI_SI_LOG_LEVEL` | The `license/restricted` scenario | `DEBUG`, so that a secret logged as a detail shows |
| `NCI_SI_ACCEPTANCE_INDEX_CODES` | The prepare command only | A file of the concept codes to index, one per line |

The Prototype Baseline Assessment reads the outcomes of a run against the furnished prototype:
a tool that passes is a reuse candidate, one that fails or is INCOMPLETE a hardening candidate,
and one NOT IMPLEMENTED new development.

The suite does not test response time and throughput (the benchmark), the ranking quality of
semantic search (the retrieval evaluation set), security controls (the contractor's security
tests), the platform APIs themselves (the conformance suite), or operator procedures such as
building, activating and rolling back the index (the contractor's integration tests and the
deployment guide). It tests what each of these must show at the tool surface: a structured
timeout error, the index release in provenance, correlation, and no secret in a result or log.

Changes to this specification, the suite, the fixture set and the request forms follow a
versioned change request, an impact assessment and the written approval of the branch chief or
a delegate, from the furnished tag. The request forms, prompt templates and resource definitions
are furnished as initial versions for the EVS and caDSR teams to refine through that record.
The suite and the fixture set are versioned independently. A contractor may propose a test, but
may not substitute its own tests for the suite as the basis of acceptance.
Every suite test cites the requirements it enforces, and at the furnished tag every requirement
is cited by one; a report whose digest is not that of an approved release reads MODIFIED;
changes under `spec/` and `acceptance/` need a code owner's approval; and the change log records
the approval and the requirement each change serves.
