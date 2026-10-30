# OpenSSF Scorecard

What this repository's [OpenSSF Scorecard](https://scorecard.dev/viewer/?uri=github.com/CBIIT/nci-si-mcp)
result is, what keeps it below 10, and what is done about it. Scorecard measures repository
security practices, not the server's behaviour or its acceptance outcomes. This file is updated in
the change that moves a check.

**Latest result: 6.0 of 10**, for commit `bf7df79` on 30 Oct 2026, from
[`.github/workflows/scorecard.yml`](.github/workflows/scorecard.yml).

## How the score is computed

Scorecard averages its checks weighted by risk: Critical 10, High 7.5, Medium 5, Low 2.5. A check
that does not apply scores -1 and is left out. A check's cost below is its weight times its
shortfall from 10, divided by the total weight (100 for this result); the costs add up to the
distance from 10.

A mitigation is taken only where it is good practice for this project on its own merits. None is
taken to move the number: no approvals without a real review, and no badge claims without the work
behind them.

## Checks below 10

| Check | Risk | Score | Cost | Why | Mitigation | Status |
|---|---|---|---|---|---|---|
| Vulnerabilities | High | 0 | 0.75 | 13 OSV advisories, all in `virtualenv` 21.14.3. It is a development tool in the `lint` group, used by pre-commit, and is not in the server, its extras or the image. The `pip-audit` job audited only the runtime groups. | Update `virtualenv` to 21.15.1, and have `pip-audit` audit every locked group. | Planned for 2 Nov 2026, when 21.15.1 passes the five-day dependency age policy ([#411](https://github.com/CBIIT/nci-si-mcp/issues/411)). |
| Code-Review | High | 0 | 0.75 | Changes are reviewed in GitHub comments headed `## Reviewer`, as [AGENTS.md](AGENTS.md) describes, not by GitHub approvals, which Scorecard counts. Both roles act from one account, and GitHub does not let a pull request's author approve it. | Approving reviews from a second account held by a person who reviews the change. | Decision for the project coordinator. |
| Signed-Releases | High | 0 | 0.75 | No release carries a signature or provenance file. Since the release workflow change of [#390](https://github.com/CBIIT/nci-si-mcp/issues/390), each published image gets build-provenance and SBOM attestations. Whether Scorecard counts attestations that are not attached to the release itself is not yet verified. | Verify with the next release; if needed, attach the provenance to the release. The check averages the last five releases, so it rises with each one. | Next release. |
| Branch-Protection | High | 3 | 0.53 | `main` requires pull requests and the aggregate `required` check, but no approving review, stale-review dismissal or code owners. Scorecard reads it with the workflow's default token, which cannot see admin-only settings. | Required approval and a `CODEOWNERS` file, once a second reviewing account exists. | Follows the Code-Review decision. |
| Fuzzing | Medium | 0 | 0.50 | No fuzzing integration. The unit, acceptance and mutation suites are deterministic, which Scorecard does not count as fuzzing. | A fuzzer (for example ClusterFuzzLite with Atheris) on the parsing boundaries. | Not planned; new scope. |
| Security-Policy | Medium | 4 | 0.30 | [SECURITY.md](SECURITY.md) is found but holds no absolute link or address for reporting. | Add the link to the private vulnerability reporting form. A promised response time would raise it further, but only the project coordinator can make that commitment. | Planned for 2 Nov 2026 ([#412](https://github.com/CBIIT/nci-si-mcp/issues/412)). |
| CII-Best-Practices | Low | 0 | 0.25 | Not enrolled in the OpenSSF Best Practices badge. | Self-certify; the passing level scores 5. | Not planned. |
| Contributors | Low | 3 | 0.18 | The contributors belong to one organisation; a full score needs at least three. | None on our side. | Accepted. |
| Pinned-Dependencies | Medium | 9 | 0.05 | Three commands are not pinned by hash. `npm exec … playwright` in `ci.yml` runs the version that `docs/site-assets/package-lock.json` pins. The uv and nvm installers in `local-run.yml` are copied from the documented developer setup, which that workflow exists to test. | None: pinning only the workflow would stop it testing the documented commands. | Accepted ([#412](https://github.com/CBIIT/nci-si-mcp/issues/412)). |
| Packaging | Medium | -1 | — | Not detected: the image is published by the project's own release workflow. A check that does not apply is left out of the score. | — | — |

Every other check scores 10: Binary-Artifacts, CI-Tests, Dangerous-Workflow,
Dependency-Update-Tool, License, Maintained, SAST and Token-Permissions.

## Expected path

| After | Expected score |
|---|---|
| The changes planned for 2 Nov 2026 (Vulnerabilities, Security-Policy) | about 7.0 |
| Provenance counted on five releases | about 7.8 |
| A second reviewing account (Code-Review, Branch-Protection) | about 9 |

A full 10 would also need fuzzing, the best-practices badge at its highest level and contributors
from three organisations. It is not a target.

## Updating this file

When a change moves a check, update its row, the costs and the latest result line in that change.
The public result is at `https://api.scorecard.dev/projects/github.com/CBIIT/nci-si-mcp`, and the
workflow keeps its SARIF output as the `scorecard-sarif` artifact for five days.
