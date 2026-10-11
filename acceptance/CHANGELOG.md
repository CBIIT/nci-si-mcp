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
- Phase 15 #265 (§5 report outcomes): a gate that cannot run makes every tool that would pass
  INCOMPLETE: the rendered report shows "INCOMPLETE (gates not run)", the README status table
  lists those tools under INCOMPLETE.
  A skip is classified by the property its site sets (`acceptance_skip`), not by its reason
  text. The weekly live workflow's fixture run runs the permission gates as CI's does. No test
  assertion of the suite or fixture outcome changes.
- Phase 15 #221 (§5 fixture set): the 1,583 fixtures `craft.py` makes are no longer committed;
  `fixture_server.load_fixtures` builds them in memory when the manifest declares them
  (`crafted:`, the digest of what `craft.py` makes now). `pdm run acceptance-craft` writes that
  digest instead of files (`--check` verifies it, `--out DIR` writes the fixtures to read), and
  a crafted name that also lies on disk is refused. The recorded fixtures and every test's
  answers are unchanged.
- Phase 15 review (§5 outcomes, `spec/acceptance.md`): INCOMPLETE also covers a gate that could
  not run (skipped or not implemented); a gate a live run skips by design (fixture mode only)
  leaves its tools as they are. Agreed by the reviewer on #265.

<!-- The shape of a release, from the furnished tag on:

## v1.0.0: <date>

Digest: <64 hex characters, as in approved.yaml>

- <the change> (serves <requirement id>; approval: <reference>)
-->
