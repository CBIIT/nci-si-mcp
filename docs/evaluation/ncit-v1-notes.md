# NCIt evaluation v1: full-corpus calibration

The [machine-readable evidence](ncit-v1-full-corpus.json) records the October 5, 2026
engineering calibration. It is not SME validation. The full NCIt 26.09d build contains
213,524 concepts and 928,520 deduplicated name, synonym and definition fields
(4.34855 per concept, range 1–89). Build `ca1181ea3e4e400cb80ed2ea70be8e52` used
SapBERT revision `090663c3ae57bf35ffe4d0d468a2a88d03051a4d`, 768 dimensions,
on an Apple M4 Max with 128 GiB RAM. Download, reconciliation and embedding took
3,295.59 seconds. The candidate was never activated.

| Mode | Hit@1 | Hit@5 | MRR@10 |
|---|---:|---:|---:|
| BM25 | 11/12 | 12/12 | 0.937500 |
| Semantic | 10/12 | 11/12 | 0.885417 |
| Hybrid | 11/12 | 12/12 | 0.958333 |

Semantic first-expected ranks are 8 for `kinase inhibition`, 2 for `tumor` and 1
for the other ten queries. Hybrid ranks `tumor` at 2 and all others at 1. BM25
ranks `tumor` at 4 and all others at 1. The JSON retains every top-ten list.
All expected concepts were present. The approved margins are one Hit@5 miss and
0.05 MRR, applied independently to semantic and hybrid; BM25 and Hit@1 are not gates.
The 146-concept developer sample's proposed semantic MRR floor of 0.95 would have
rejected this full build, which is why sample measurements do not set production floors.

## Latency experiment

The original 4 KiB-page database occupied 4,192,129,024 bytes, including
2,852,413,440 vector bytes. A copy converted to 64 KiB pages occupied 4,218,945,536
bytes and passed SQLite `integrity_check`. Every query report in all three modes was
identical after conversion. No vectors, scoring, precision or field policy changed.

| Layout / mode | Cold seconds | Warm seconds | Peak RSS cold / warm, bytes |
|---|---:|---:|---:|
| 4 KiB semantic | 10.533 | 1.451 | 253427712 / 250413056 |
| 4 KiB hybrid | 9.453 | 1.360 | 253313024 / 262668288 |
| 64 KiB semantic | 2.134 | 1.484 | 255770624 / 256376832 |
| 64 KiB hybrid | 2.279 | 1.512 | 268206080 / 260751360 |

These are single cold/warm pairs for `kinase inhibition`, not latency percentiles
or a twelve-query latency sweep. Each mode used a fresh copy written with macOS
`F_NOCACHE` and `fsync`. This avoids populating the destination OS page cache but
does not guarantee a cold storage-controller cache. A fresh Python process read the
copy for each phase; the warm process reused the database warmed by the cold run.
NumPy loaded before the timer. The timer covered `LocalIndex.search_build`, including
the read snapshot, exact scoring and result projection. Peak process RSS came from
`resource.getrusage` before profiling.

The provider returned the actual precomputed SapBERT vector. Model startup and query
embedding were excluded: embedding took 0.090 seconds for the first query and
0.008–0.012 seconds for the others. The stored profiles are subsequent warm repeats;
they do not attribute the cold latency. The 64 KiB experiment met the agreed 5-second
cold and 2-second warm targets, so float16 storage was not attempted. New files use
64 KiB pages; existing files keep their layout until an explicit offline conversion.

## Reproducing the evidence

Use Python 3.14 with the `index` and `embeddings` extras and a separate data directory.
Select the `sentence-transformers` provider and
`cambridgeltl/SapBERT-from-PubMedBERT-fulltext`; freeze the model revision above in the
model cache. Run `index-build` while the selected monthly release is 26.09d, and verify
the completed manifest's release, corpus count and dimensions before comparing results.
Do not activate the candidate. Run `evaluate --build-id BUILD_ID` to reproduce the
twelve-query report and approved floors. A different served release requires a new
calibration, not comparison with these thresholds.

For layout measurements, checkpoint and close the source database before copying it.
On the copy only, use `PRAGMA journal_mode=DELETE`, `PRAGMA page_size=65536`, `VACUUM`
and `PRAGMA journal_mode=WAL`. Verify `PRAGMA page_size` and `PRAGMA integrity_check`,
then repeat the cold/warm procedure above in otherwise idle processes. Compare the full
evaluation reports before and after conversion, and delete the measurement copies.
The JSON preserves the measured source-file hashes, model revision, hardware, build
identity, query-embedding times, full rankings, profiles and reviewer decision links.
