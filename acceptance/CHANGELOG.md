# Change log of the specification and the acceptance suite

The NCI SI MCP project coordinator is the code owner of `spec/` and `acceptance/`.

From the furnished tag, every change to `spec/` or to the suite is an entry here with three
things: the change, the requirement it serves, and its approval reference, the written approval of
the branch chief or a delegate ([spec/acceptance.md](../spec/acceptance.md)). Each approved
release of the suite has its digest in [approved.yaml](approved.yaml); a report from any other
tree renders MODIFIED.

## Unreleased: before the furnished tag

Changes until then are development under the plan and need no approval reference. The suite is
under construction: the per-tool tests, the protocol gates, the prompts and resources, and the
fixture set pinned to NCIt 26.09d.

- Phase 5 #41 (A11.1–A11.2): allow the stdio client helper's caller to select its read
  timeout, retaining 60 seconds for acceptance tests. The benchmark uses 300 seconds so
  upstream retries can finish and provide their actual audit counts. A real-session
  self-test verifies the override; no assertion, request form or fixture outcome changes.
  Correct the fixture README's claim of live verification for credentialed content: the
  current live-capable tests cover protocol behavior only.

<!-- The shape of a release, from the furnished tag on:

## v1.0.0: <date>

Digest: <64 hex characters, as in approved.yaml>

- <the change> (serves <requirement id>; approval: <reference>)
-->
