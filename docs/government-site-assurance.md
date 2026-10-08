# Government website assurance

This is the Phase 7 verification plan and applicability record, **not a declaration of
Section 508 conformance or authorization to deploy**. It covers public documentation and the
local administration companion, including complete user journeys. The owner requested this
review on 8 October 2026. Design credit: the Semantic Infrastructure (SI) team.

## Accessibility baseline

The Revised Section 508 Standards incorporate WCAG 2.0 Level A and AA; our design target remains
WCAG 2.2 AA. A percentage score or automated scan does not establish conformance. Use the federal
ICT Testing Baseline to organize automated, manual and assistive-technology evidence.
[Section 508 applicability](https://www.section508.gov/develop/applicability-conformance/),
[federal testing guidance](https://www.section508.gov/buy/).

| Check | Evidence required in #196 |
|---|---|
| Structure | Page titles, language, landmarks, reading order, headings and table header associations |
| Keyboard/focus | Skip links, search, navigation, filters, forms, controls; visible ordered focus and no traps |
| Visual presentation | Measured contrast, 200% text enlargement, 400% zoom/reflow, mobile widths, no color-only status |
| Interaction | Accessible names/roles/values, instructions, errors, announcements and target sizes |
| Alternatives | Diagram descriptions, equivalent chart tables, meaningful links and accessible downloads |
| Resilience | Essential no-JavaScript reading, reduced motion, no flashing or unexpected timeouts |
| Assistive technology | Screen-reader navigation and form feedback across representative complete journeys |

Current evidence is limited to the issue tests and browser observations recorded on the
documentation PR. The full matrix is pending #196. Unperformed checks must say **not verified**,
with a reproducible procedure and responsible role. Fix confirmed defects in the active milestone.

### Preliminary dashboard checks (8 October 2026)

The #193 working implementation was checked using synthetic test evidence, not live acceptance
results. Browser accessibility-tree inspection found named form controls and captioned tables;
keyboard submission of a tool filter returned the expected subset. Wide tables gained named,
focusable scroll regions, with a regression test that failed before the fix. This is not a
screen-reader test or a completed keyboard/reflow audit.

The dashboard's declared foreground/background pairs were measured using WCAG relative luminance:
body text on white **11.66:1**, teal links on white **7.52:1**, table text on shaded cells **10.41:1**,
and focus outlines **5.43:1** on white / **4.85:1** on shaded cells. These measurements cover those
pairs only. The refined palette also measures teal on pale slate **6.99:1**, secondary text on
slate **5.84:1**, white on navy **12.71:1**, and amber status text **7.80:1**.
Browser-native controls, all states and the public documentation theme still require
the #196 review. Final evidence must identify the tested release commit.

## Federal and HHS applicability

HHS web policy covers digital modernization, accessibility, plain language, information collection,
records, branding, domains, disclaimers and privacy. Apply requirements to the actual service;
this local prototype is not an agency principal website.
[HHS web policies](https://www.hhs.gov/digital/policies/web/index.html).

| Area | Repository checks | Deployment evidence/decision |
|---|---|---|
| Identity and links | About, policies, accessibility and external-reference notices; truthful prototype label | NCI approves branding, contacts and applicable links; official `.gov`/HTTPS statements must match the actual host |
| Privacy | Inventory storage, search, logs, forms, analytics and third-party requests; describe actual behavior | Privacy/platform roles assess notices, logging, analytics, retention and any collection approval; no tracking or surveys added by default |
| Usability | Responsive search/navigation, understandable stories/errors, current version/content labels | Confirm intended audiences, languages and public feedback arrangements |
| Security | Keep public docs and local functionality accessible; exclude operational results from public builds | Platform verifies HTTPS, domains, headers, vulnerability reporting and UAT/PROD admin protection |
| Records | Trace commits and document content ownership/artifact lifetime | Agency records role determines applicable schedules; 30-day CI retention is not a federal records policy |
| Governance | Record findings, methods and unresolved applicability | NCI web/accessibility/platform roles accept deployed readiness and required assessments |

Federal guidance distinguishes principal agency sites from secondary sites. Review About,
accessibility, privacy, external-link notices, FOIA, USA.gov and vulnerability reporting;
do not assume every principal-site obligation must be duplicated here.
[Required content and links](https://digital.gov/resources/required-web-content-and-links).

Use current OMB M-23-22 and 21st Century IDEA guidance; M-23-22 superseded M-17-06.
[Digital-first public experience](https://digital.gov/resources/delivering-digital-first-public-experience).

## Release disposition

#196 records the tested commit, methods, findings/fixes and remaining limitations. Deployment-only
evidence stays explicitly pending. #197 still blocks UAT/PROD admin exposure without approved
platform integration. This review does not change repository visibility or introduce a custom
identity service or institutional login for local users. Agency policy and production settings
remain owner/platform decisions.
