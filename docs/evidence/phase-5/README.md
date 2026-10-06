# Phase 5 measurement evidence

See the [method and reproduction commands](../../benchmark.md). These are sequential
workstation measurements, not Cloud One capacity or production latency commitments.

## Acceptance

| Run | Passed | Skipped / not live | Failed gates | Unrun gates |
|---|---:|---:|---:|---:|
| [fixture](acceptance-fixture.json) | 944 | 0 | 0 | 0 |
| [http-fixture](acceptance-http-fixture.json) | 940 | 4 | 0 | 0 |
| [live](acceptance-live.json) | 16 | 928 | 0 | 25 |

All 29 tools are PASS in the [combined renderer's output](acceptance-combined.md).
Its fixture verdicts do not establish live content acceptance: only 16 protocol tests
ran live; 928 cases, including 25 resource/correlation gates, did not. HTTP's four
skips are the specified remote-unprepared cases. No expected fixture outcomes changed.

Final acceptance suite digest: `7f435ac6463e3aaca174f367cf55a9e6f9213509109bb75e7d4c4fe6742675d5`;
the suite is MODIFIED, not an approved furnished release. The benchmarks retain their actual
earlier digest `a1e3ceeabb3bc0beffeb686a5786e28d1e395259fefc3a6aeda96093ab52a29b`.
A [verified documentation-only change](suite-documentation-change.json) to the digested fixture
README explains the difference; no measured code, test or fixture response changed.

## Benchmarks

Every scenario contains 20 cold and 20 warm calls; an additional warm primer is excluded.
p50/p95 are milliseconds; bytes and outbound attempts are means per call. Errors remain
in timing distributions. Cold excludes process initialization. No semantic index is loaded.

### Fixture

[Raw samples](benchmark-fixture.json), begun 2026-10-06T21:35:51.257157+00:00.

| Tool | State | p50 ms | p95 ms | Error rate | Mean bytes | Mean attempts | Codes |
|---|---|---:|---:|---:|---:|---:|---|
| `get_concept` | cold | 15.5 | 18.2 | 0% | 423.0 | 1.00 | ok |
| `get_concept` | warm | 1.4 | 1.6 | 0% | 423.0 | 1.00 | ok |
| `search_concepts` | cold | 17.4 | 20.4 | 0% | 5343.0 | 1.00 | ok |
| `search_concepts` | warm | 2.7 | 3.4 | 0% | 5343.0 | 1.00 | ok |
| `get_data_element` | cold | 21.6 | 25.1 | 0% | 537.0 | 1.00 | ok |
| `get_data_element` | warm | 1.7 | 2.0 | 0% | 537.0 | 1.00 | ok |
| `get_form` | cold | 21.6 | 24.6 | 0% | 106122.0 | 1.00 | ok |
| `get_form` | warm | 6.2 | 6.6 | 0% | 106122.0 | 1.00 | ok |
| `match_data_elements` | cold | 23.4 | 25.9 | 0% | 1266.0 | 1.00 | ok |
| `match_data_elements` | warm | 1.7 | 2.2 | 0% | 1266.0 | 1.00 | ok |
| `search_data_elements` | cold | 28.3 | 32.1 | 0% | 5783.0 | 1.00 | ok |
| `search_data_elements` | warm | 5.8 | 6.4 | 0% | 5783.0 | 1.00 | ok |
| `find_data_elements_for_concept` | cold | 22.6 | 26.9 | 0% | 13829.0 | 3.00 | ok |
| `find_data_elements_for_concept` | warm | 4.5 | 4.9 | 0% | 13829.0 | 3.00 | ok |
| `ground_value` | cold | 33.2 | 37.6 | 0% | 53659.0 | 5.00 | ok |
| `ground_value` | warm | 12.2 | 14.4 | 0% | 53659.0 | 5.00 | ok |
| `expand_cohort` | cold | 33.0 | 36.9 | 0% | 38539.0 | 8.00 | ok |
| `expand_cohort` | warm | 12.8 | 13.4 | 0% | 38539.0 | 8.00 | ok |
| `harmonize_data_dictionary` | cold | 27.9 | 30.9 | 0% | 7019.0 | 3.00 | ok |
| `harmonize_data_dictionary` | warm | 3.6 | 4.0 | 0% | 7019.0 | 3.00 | ok |

Server/runner source SHA-256: `4dbc25c5e1fa26b309b676ee8f1e6491c02afbdc913ef9846a6efcf50da90893`.

### Live

[Raw samples](benchmark-live.json), begun 2026-10-06T21:38:10.663506+00:00.

| Tool | State | p50 ms | p95 ms | Error rate | Mean bytes | Mean attempts | Codes |
|---|---|---:|---:|---:|---:|---:|---|
| `get_concept` | cold | 620.7 | 728.3 | 0% | 428.0 | 1.00 | ok |
| `get_concept` | warm | 608.1 | 653.2 | 0% | 428.0 | 1.00 | ok |
| `search_concepts` | cold | 674.9 | 838.7 | 0% | 5393.0 | 1.00 | ok |
| `search_concepts` | warm | 681.7 | 1714.6 | 0% | 5393.0 | 1.15 | ok |
| `get_data_element` | cold | 933.1 | 12501.8 | 0% | 540.0 | 1.00 | ok |
| `get_data_element` | warm | 879.1 | 11410.5 | 0% | 540.0 | 1.00 | ok |
| `get_form` | cold | 12151.1 | 18727.5 | 0% | 106125.0 | 1.10 | ok |
| `get_form` | warm | 12361.8 | 20063.7 | 0% | 106125.0 | 1.10 | ok |
| `match_data_elements` | cold | 686.6 | 953.9 | 100% | 339.0 | 1.00 | upstream_unavailable |
| `match_data_elements` | warm | 624.9 | 699.0 | 100% | 339.0 | 1.00 | upstream_unavailable |
| `search_data_elements` | cold | 701.6 | 16050.6 | 100% | 538.0 | 1.00 | upstream_unavailable |
| `search_data_elements` | warm | 602.5 | 675.9 | 100% | 538.0 | 1.00 | upstream_unavailable |
| `find_data_elements_for_concept` | cold | 2204.4 | 2967.7 | 0% | 13846.0 | 3.10 | ok |
| `find_data_elements_for_concept` | warm | 2188.7 | 2279.6 | 0% | 13846.0 | 3.00 | ok |
| `ground_value` | cold | 3535.6 | 3767.9 | 0% | 53730.0 | 5.00 | ok |
| `ground_value` | warm | 3418.5 | 3743.6 | 0% | 53730.0 | 5.00 | ok |
| `expand_cohort` | cold | 6521.1 | 9232.6 | 0% | 38924.0 | 8.10 | ok |
| `expand_cohort` | warm | 5718.4 | 7562.1 | 0% | 38924.0 | 8.00 | ok |
| `harmonize_data_dictionary` | cold | 682.3 | 12807.9 | 100% | 339.0 | 1.10 | upstream_unavailable |
| `harmonize_data_dictionary` | warm | 651.5 | 715.9 | 100% | 339.0 | 1.00 | upstream_unavailable |

Server/runner source SHA-256: `4dbc25c5e1fa26b309b676ee8f1e6491c02afbdc913ef9846a6efcf50da90893`.

Explicit NCIt calls address 26.09d; each raw sample records the actually resolved release.
caDSR calls are unpinned and never invent a registry identifier. Live matching has no
issued credential; refusals and unsupported keyword search are observations, not successful
capability or latency claims. Fixture matching uses crafted contract answers.

The [interrupted earlier live run](benchmark-live-interrupted.json) retains three completed
scenario sets. Its form call exceeded the former 60-second client timeout before audit
completion, so its attempt count is unknown. Final runs use 300 seconds; ordinary acceptance
keeps its 60-second default. The interrupted run has an earlier source/suite identity and
does not contribute to the final percentiles.
