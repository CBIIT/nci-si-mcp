"""Server-rendered local evidence pages; no raw report content or client scripting."""

from __future__ import annotations

from html import escape
from typing import Any

from scripts.evidence_benchmark import comparable

FOIA_URL = (
    "https://www.nih.gov/institutes-nih/nih-office-director/"
    "office-communications-public-liaison/freedom-information-act-office"
)

# Published NCIDS tokens; full component/identity assurance is tracked in #196.
STYLE = """
:root{color-scheme:light;font:1rem/1.6 system-ui,-apple-system,sans-serif;color:#1b1b1b;
background:#f0f0f0}*{box-sizing:border-box}body{margin:0}a{color:#004971;text-underline-offset:.2em}
a:hover{text-decoration-thickness:2px}h1,h2,h3{line-height:1.25;overflow-wrap:anywhere}
h1{font-size:clamp(1.7rem,4vw,2.5rem);letter-spacing:-.03em;margin:.25rem 0 1rem}
h2{font-size:1.35rem;margin-top:2rem}h3{font-size:1.1rem}p{max-width:78ch}
.shell{max-width:78rem;margin:auto;padding:1.25rem 2rem}.topbar{background:#00314b;color:white}
.topbar .shell{display:flex;justify-content:space-between;gap:1rem;flex-wrap:wrap;
align-items:center}
.brand{font-size:1.1rem;font-weight:750;letter-spacing:.02em}.topbar a{color:white}
nav{display:flex;gap:1.5rem;flex-wrap:wrap}
.eyebrow{text-transform:uppercase;font-size:.75rem;letter-spacing:.1em;
font-weight:750;color:#3d4551}.skip{position:absolute;left:1rem;top:-8rem;background:white;
padding:.75rem;z-index:2}.skip:focus{top:.5rem}.notice{background:#d4e7f2;border-left:4px solid
#004971;padding:.75rem 1rem;border-radius:0 .4rem .4rem 0;font-size:.9rem;max-width:none}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,22rem),1fr));gap:1rem;
margin:1.5rem 0}.card{background:white;border:1px solid #cbd8e1;border-radius:.65rem;
padding:1.25rem;
box-shadow:0 2px 5px #12364a08}.card h2{margin:0 0 1rem;font-size:1rem}.card p{margin:.5rem 0}
.badge{display:inline-block;background:#edf3f6;border:1px solid #a6bbc7;border-radius:2rem;
padding:.1rem .65rem;font-size:.8rem;font-weight:700;margin:.25rem .25rem .25rem 0}
.interrupted,.cancelled,.unavailable{background:#fdf2bf;color:#5c4809;border-color:#936f38}
.failed{background:#fde2ea;color:#700824;border-color:#b60d43}
.meta{color:#3d4551;font-size:.9rem}.run-list{list-style:none;padding:0;display:grid;gap:.6rem}
.run-list li{background:white;border:1px solid #cbd8e1;border-radius:.5rem;padding:1rem 1.25rem}
.run-link{font-weight:700}.scroll{overflow-x:auto;border:1px solid #cbd8e1;border-radius:.5rem;
background:white;margin:1rem 0 1.5rem}table{border-collapse:collapse;width:100%;font-size:.9rem}
caption{text-align:left;padding:.75rem 1rem;font-weight:700;color:#1b1b1b}
th,td{text-align:left;border-top:1px solid #d9e3ea;padding:.7rem 1rem;overflow-wrap:anywhere}
th{background:#edf3f6;font-size:.8rem}tbody tr:nth-child(even){background:#f7fafc}
:focus-visible{outline:3px solid #004971;outline-offset:3px}
.topbar :focus-visible{outline-color:white}
form{display:flex;gap:1rem;align-items:end;flex-wrap:wrap;background:white;border:1px solid #cbd8e1;
border-radius:.5rem;padding:1.25rem;margin:1rem 0}label{display:grid;gap:.35rem;font-weight:650}
input,select,button{font:inherit;max-width:100%;min-height:2.75rem;border:1px solid #7892a3;
border-radius:.35rem;padding:.5rem .75rem}input,select{background:white;color:#1b1b1b}
button,.action{background:#004971;color:white;font-weight:650;cursor:pointer;border-radius:.35rem}
button:hover,.action:hover{background:#00314b}.action{display:inline-block;padding:.65rem 1rem;
text-decoration:none}.help-link{font-size:.9rem}.help-nav{display:flex;flex-wrap:wrap;gap:1rem}
code{overflow-wrap:anywhere;background:#edf3f6;padding:.1rem .25rem;border-radius:.2rem}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#edf3f6;padding:1rem;border-radius:.4rem}
footer{margin-top:2rem;font-size:.85rem;line-height:1.5;color:white;background:#00314b}
footer .shell{padding-top:1.25rem;padding-bottom:1rem;display:grid;gap:1rem}
footer a{color:white}footer :focus-visible{outline-color:white}
footer p,footer ul{margin:0}.footer-main{display:flex;justify-content:space-between;
align-items:start;gap:1rem 2rem;flex-wrap:wrap}
.footer-identity strong{display:block;font-size:1.4rem;line-height:1.3}
.footer-identity span{font-size:1rem}.footer-identity .footer-context{margin-top:.65rem}
.footer-contact{text-align:right;margin-left:auto}
.footer-contact h2{font-size:1.2rem;margin:0 0 .3rem}
footer .footer-agencies{display:block;margin-top:.65rem}
footer .footer-agencies ul{display:block}footer .footer-bottom{display:flex;
justify-content:space-between;align-items:baseline;gap:.5rem 1.5rem;flex-wrap:wrap}
footer ul{list-style:none;padding:0;display:flex;gap:.35rem 1.25rem;flex-wrap:wrap}
footer a{display:inline-block;padding:.15rem 0}
section[id]{scroll-margin-top:1rem}@media(max-width:40rem){.shell{padding:1rem}form,label{width:100%}
input,select{width:100%}th,td{padding:.5rem}.card{padding:1rem}
.footer-contact{text-align:left;margin-left:0}}
"""


def text(value: Any) -> str:
    return escape("unknown" if value is None else str(value), quote=True)


def page(title: str, content: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{text(title)} · NCI SI local validation</title><style>{STYLE}</style></head>
<body id="top"><a class="usa-skipnav skip" href="#main">Skip to content</a>
<header class="topbar"><div class="shell"><span class="brand">NCI SI · Validation</span>
<nav aria-label="Main"><a href="/">Results</a><a href="/jobs">Run checks</a>
<a href="/configuration">Configuration</a>
<a href="/help">Help &amp; guide</a></nav></div>
</header><main id="main" class="shell"><p class="eyebrow">Local evidence workspace</p>
<h1>{text(title)}</h1><p class="notice">No login is required locally.
UAT/PROD administration is disabled pending platform integration.</p>
{content}</main><footer><div class="shell"><div class="footer-main">
<div class="footer-identity"><p><strong>National Cancer Institute</strong>
<span>at the National Institutes of Health</span></p>
<p class="footer-context">Semantic Infrastructure · Local validation prototype<br>
<a href="/help#provenance">Understand the evidence</a></p></div>
<div class="footer-contact"><h2>Contact us</h2>
<a href="https://github.com/CBIIT/nci-si-mcp/issues">Contact project maintainers</a>
<nav class="footer-agencies" aria-label="Government agencies"><ul>
<li><a href="https://www.hhs.gov">U.S. Department of Health and Human Services</a></li>
<li><a href="https://www.nih.gov">National Institutes of Health</a></li>
<li><a href="https://www.cancer.gov">National Cancer Institute</a></li>
<li><a href="https://www.usa.gov">USA.gov</a></li>
</ul></nav></div></div><div class="footer-bottom"><nav aria-label="Policies"><ul>
<li><a href="https://www.cancer.gov/policies/disclaimer">Disclaimer Policy</a></li>
<li><a href="https://www.cancer.gov/policies/accessibility">Accessibility</a></li>
<li><a href="{FOIA_URL}">FOIA</a></li>
<li><a href="https://www.hhs.gov/vulnerability-disclosure-policy">
HHS Vulnerability Disclosure</a></li>
<li><a href="https://www.cancer.gov/policies/privacy-security">Privacy and Security</a></li>
</ul></nav><a href="#top">Back to top ↑</a></div></div></footer></body></html>"""


def _table(title: str, headers: list[str], rows: list[list[Any]]) -> str:
    heading = "".join(f'<th scope="col">{text(value)}</th>' for value in headers)
    body = "".join("<tr>" + "".join(f"<td>{text(v)}</td>" for v in row) + "</tr>" for row in rows)
    return (
        f'<div class="scroll" tabindex="0" role="region" aria-label="{text(title)}">'
        f"<table><caption>{text(title)}</caption><thead><tr>{heading}</tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def _run_link(row: dict[str, Any] | None, absent: str) -> str:
    if row is None:
        return absent
    return (
        f'<a class="run-link" href="/runs/{text(row["run_id"])}">View run {row["sequence"]}</a> '
        f'<span class="badge {text(row["state"])}">{text(row["state"].capitalize())}</span>'
        f'<span class="meta">{text(row["kind"].capitalize())} · '
        f"Mode: {text(row.get('mode'))}</span>"
    )


def history_page(rows: list[dict[str, Any]]) -> str:
    latest = rows[0] if rows else None
    complete = next((row for row in rows if row["inventory_complete"]), None)
    body = "<p>Review recorded acceptance results and benchmark measurements. "
    body += (
        '<a href="/help#getting-started">Import your first report or learn the workflow</a>.</p>'
    )
    body += '<div class="cards"><section class="card"><h2>Latest attempt</h2><p>'
    body += _run_link(latest, "No recorded attempt") + "</p></section>"
    body += '<section class="card"><h2>Latest complete evidence</h2><p>'
    body += _run_link(complete, "No complete evidence") + "</p></section></div>"
    body += "<p>Complete describes recorded inventory, not a PASS verdict or live-service proof. "
    body += "Order is local import sequence, not a report-supplied clock. "
    body += (
        'Retention bounds this history. <a href="/help#run-status">How run status works</a>.</p>'
    )
    body += '<h2>Retained attempts</h2><ol class="run-list">'
    body += "".join(f"<li>{_run_link(row, '')}</li>" for row in rows)
    return page("Validation history", body + "</ol>" + _comparison_form(rows))


def _comparison_form(rows: list[dict[str, Any]]) -> str:
    benchmarks = [row for row in rows if row["kind"] == "benchmark"]
    if not benchmarks:
        return ""
    options = "".join(
        f'<option value="{text(row["run_id"])}">Sequence {row["sequence"]}: '
        f"{text(row['state'])} ({text(row.get('mode'))})</option>"
        for row in benchmarks
    )
    return (
        '<h2>Compare benchmarks</h2><p><a href="/help#benchmarks">'
        "When is comparison meaningful?</a>"
        '</p><form action="/compare" method="get">'
        f'<label>First run <select name="left">{options}</select></label>'
        f'<label>Second run <select name="right">{options}</select></label>'
        "<button>Compare recorded conditions</button></form>"
    )


def _acceptance(evidence: dict[str, Any], tool: str, story: str) -> str:
    cases = evidence["cases"]
    selected = _filter_cases(cases, tool, story)
    form = f'''<form method="get"><label>Tool <input name="tool" value="{text(tool)}"></label>
<label>Story <input name="story" value="{text(story)}"></label><button>Filter</button></form>'''
    body = (
        f'<h2>Acceptance cases</h2>{form}<p role="status">{len(selected)} of {len(cases)} cases</p>'
    )
    body += (
        '<p class="help-link"><a href="/help#acceptance">Understand verdicts and filters</a></p>'
    )
    tools = evidence["tools"]
    if tools is None:
        body += "<p>No report was produced; no outcome is inferred.</p>"
    else:
        body += _table(
            "Tool verdicts",
            ["Tool", "Native verdict", "Gate-only failure"],
            [[name, row["outcome"], row["gates_only"]] for name, row in tools.items()],
        )
    body += _table(
        "Acceptance cases",
        ["Case ID", "Tool", "Story", "Expected", "Recorded outcome", "Gate"],
        [
            [r["id"], r["tool"], r["story"], r["expected"], r["outcome"], r["gate"]]
            for r in selected
        ],
    )
    return body


def _filter_cases(cases: list[dict[str, Any]], tool: str, story: str) -> list[dict[str, Any]]:
    return [
        row
        for row in cases
        if (not tool or row["tool"] == tool) and (not story or row["story"] == story)
    ]


def _benchmark(evidence: dict[str, Any]) -> str:
    body = (
        "<h2>Benchmark measurements</h2><p>Low sample counts do not establish an SLO or capacity. "
    )
    body += (
        "Fixture timing does not establish live-service performance. Cold/warm definitions belong "
    )
    body += "to the recorded transport; a new HTTP session does not prove a cold server.</p>"
    body += (
        '<p><a href="/help#benchmarks">Reading latency, errors and comparison conditions</a></p>'
    )
    rows = []
    labels = evidence.get("phase_labels", {"cold": "cold", "warm": "warm"})
    for case in evidence["cases"] or []:
        for phase, label in labels.items():
            data = case[phase]
            rows.append(
                [
                    case["tool"],
                    label,
                    len(data["samples"]),
                    data["errors"],
                    _latency(data["summary"].get("p50Ms")),
                    _latency(data["summary"].get("p95Ms")),
                ]
            )
    body += _table(
        "Benchmark measurements", ["Tool", "Phase", "Samples", "Errors", "p50 ms", "p95 ms"], rows
    )
    body += "<h3>Comparison fingerprint</h3>"
    return body + _table(
        "Comparison fingerprint",
        ["Dimension", "Digest (unknown blocks comparison)"],
        [[key, evidence["fingerprint"][key]] for key in sorted(evidence["fingerprint"])],
    )


def _latency(value: float | None) -> str:
    return "unknown" if value is None else f"{value:.2f}"


def run_page(record: dict[str, Any], *, tool: str = "", story: str = "") -> str:
    evidence = record["evidence"]
    body = "<p>Origin unverified: imported locally. Checksums bind bytes; they do not authenticate "
    body += "the runner. Server identity not independently verified.</p>"
    body += '<p><a href="/help#provenance">What does this provenance establish?</a></p>'
    body += _table(
        "Run provenance",
        ["Field", "Recorded value"],
        [
            [label, evidence.get(name)]
            for name, label in {
                "kind": "Evidence type",
                "state": "Run state",
                "mode": "Mode",
                "transport": "Transport",
                "inventory_complete": "Inventory complete",
                "runner_commit": "Runner source commit",
                "server_commit": "Reported server source commit",
                "started_at": "Started at",
                "finished_at": "Finished at",
            }.items()
        ],
    )
    body += f"<p>Local sequence {record['sequence']}; bundle SHA-256 "
    body += f"<code>{text(record['checksum'])}</code>.</p>"
    if evidence["state"] == "unverified":
        body += "<p>Original inventory unavailable. "
        body += "Legacy bytes are retained without interpreted outcomes.</p>"
    elif evidence["kind"] == "acceptance":
        body += _acceptance(evidence, tool, story)
    else:
        body += _benchmark(evidence)
    return page("Run " + evidence["run_id"], body)


def comparison_page(left: dict[str, Any], right: dict[str, Any]) -> str:
    evidence = [left["evidence"], right["evidence"]]
    if any(row["kind"] != "benchmark" or row["state"] == "unverified" for row in evidence):
        return page(
            "Benchmark comparison",
            "<p>Comparison unavailable: verified benchmark format required.</p>",
        )
    ready, reasons = comparable(*evidence)
    if not ready:
        return page(
            "Benchmark comparison",
            "<p>Comparison blocked: "
            + text(", ".join(reasons))
            + "</p><p>These recorded conditions are unknown, different or incomplete. "
            '<a href="/help#benchmarks">How to interpret comparison requirements</a>.</p>',
        )
    body = "<p>Recorded fingerprints match. Imported origin and server identity remain unverified. "
    body += "These descriptive measurements establish no SLO or capacity claim.</p>"
    for row in evidence:
        body += "<h2>Run " + text(row["run_id"]) + "</h2>" + _benchmark(row)
    return page("Benchmark comparison", body)
