# Retrieval evaluation

Retrieval quality is evaluated separately from specification acceptance. The evaluation uses
versioned NCIt queries and expected concept codes, not a claim that an embedding model is
clinically validated. The initial judgments are engineering regression scenarios pending NCI
subject-matter expert (SME) validation.

## Metrics and evidence

Every query contributes once to each metric. Hit@1 and Hit@5 are the fractions of queries with
at least one expected code in the first one or five results. MRR@10 is the mean of the reciprocal
rank of the first expected code in the first ten results; a miss contributes zero. Multiple
expected codes are alternatives for ranking. Nevertheless, every listed expected code must
exist in the candidate corpus: a missing code makes the evaluation incomplete and prevents a
passing gate, even if another alternative ranks first. Missing queries or concepts never shrink
the denominator.

The report retains the ranked codes and first expected rank for every query, all three modes
(BM25, semantic, hybrid), the evaluation version, candidate build id, release, provider, model,
dimensions, corpus size, thresholds and pass/fail result. Semantic search is named `vector` in
the internal scorer and its report. The score summary is the smaller of semantic and hybrid
MRR@10. Detailed reports live in the index database; public `index_manifest` records gain no
fields.

Evaluation reads an explicit completed candidate without activating it. A concurrent activation
cannot substitute a different candidate's data. If retention removes the candidate during
evaluation, evaluation fails instead of storing a mixed or partial report. Production rebuilds
retain their classification but require fresh evaluation. Developer samples remain samples;
sample updates cannot modify an active production index. Older unclassified manifests remain
usable for rollback and are never described as evaluated. Rebuilding an unclassified snapshot
creates a production build requiring evaluation; the refusal identifies the original snapshot
and offers evaluation or explicit recreation with `index-sample` in a separate data directory.

## Calibration and CI

Production floors must come from a real-model run against the full production corpus and its
actual indexed fields. Record per-query results, Hit@1, Hit@5 and MRR@10 before choosing floors.
The agreed initial margins are 0.05 MRR and one Hit@5 miss for a set under twenty queries.
Recalibrate when the model, release, judgments or indexing behavior changes; never borrow the
deterministic test provider's floors for production.

The [developer baseline](evaluation/ncit-v1-developer-baseline.json) used SapBERT on 146 concepts.
Its incomplete recorded fields make it unsuitable for production calibration. In particular,
the Neoplasm payload lacks synonyms: BM25 and hybrid miss `tumor` in the first ten results,
while semantic search ranks Neoplasm first. Production calibration therefore uses the subsequent
[full-corpus evidence](evaluation/ncit-v1-full-corpus.json) supplies version
`ncit-26.09d-sapbert-v1`: semantic Hit@5 ≥ 10/12 and MRR@10 ≥ 0.8354166666666666;
hybrid Hit@5 ≥ 11/12 and MRR@10 ≥ 0.9083333333333333. BM25 and Hit@1 are reported but
not gated. The sample-derived semantic MRR floor of 0.95 would have refused this full build.
The [version notes](evaluation/ncit-v1-notes.md) explain the build, model revision, release,
field counts, per-query ranks, latency experiment and reproduction procedure.

CI runs `tests/test_evaluation_sample.py` as part of `pdm run test`. It builds the 146-concept
recorded sample with hashing embeddings and uses the explicitly test-only set in
`tests/fixtures/evaluation/test-only-v1.json`. This checks the production scoring, reporting,
persistence and activation paths; it does not measure semantic quality. The test asserts the
BM25 baseline and exact-name rankings directly. Separate gate tests cover missing evidence,
failed metrics, missing expected codes, calibration mismatches, rebuilds and rollback.

Run these checks locally with:

```bash
pdm run pytest tests/test_evaluation_sample.py tests/test_evaluation_gate.py tests/test_evaluation.py tests/test_evaluation_sets.py
```

## NCI SME validation protocol

1. Prepare representative tasks covering preferred names, synonyms and genuine conceptual
   queries, including ambiguous queries and relevant distractors. Freeze the release, corpus,
   model revision and indexing configuration used for judgment.
2. Have at least two domain reviewers independently identify relevant NCIt concepts. Preserve
   their judgments and rationales before exposing the ranker's ordering.
3. Adjudicate disagreements with a designated domain reviewer. Record the decision and the
   expected-code alternatives; do not edit judgments merely to improve a model's score.
4. Measure all modes on the full corpus. Review per-query failures alongside aggregate metrics,
   agree regression margins and scope limitations, and record explicit SME approval before
   calling a set SME-validated. Until then, label its floors engineering regression floors.
5. Publish a new evaluation-set version whenever judgments or floors change. Retain the old
   set, reports, model/release/build identity and approval evidence. Revalidate after a release
   or model change, including retired or replaced expected concepts, before adopting new floors.

This protocol does not establish clinical suitability or replace NCI's acceptance criteria.
