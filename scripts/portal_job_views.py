"""Accessible local run controls and explicit lifecycle explanations."""

from __future__ import annotations

import uuid
from typing import Any

from scripts.portal_jobs import ACTIVE
from scripts.portal_views import _table, page, text

LABELS = {
    "acceptance-http-fixture": "Acceptance checks · HTTP fixtures",
    "benchmark-http-fixture": "HTTP benchmark · fixture examples",
    "benchmark-http-remote": "Remote read-only benchmark · configured target",
}


def _link(row: dict[str, Any]) -> str:
    return (
        f'<a href="/jobs/{text(row["run_id"])}">View run {row["sequence"]}</a> '
        f'<span class="badge {text(row["state"])}">{text(row["state"].capitalize())}</span> '
        f'<span class="meta">{text(LABELS[row["profile"]])}</span>'
    )


def jobs_page(rows: list[dict[str, Any]], profiles: tuple[str, ...]) -> str:
    options = "".join(f'<option value="{name}">{text(LABELS[name])}</option>' for name in profiles)
    body = "<p>Run reviewed checks in disposable workers. The serving MCP stays separate. "
    body += '<a href="/help#run-controls">Choose a profile and understand its limits</a>.</p>'
    body += (
        "<p>Runs use the committed checkout source. "
        "<strong>Uncommitted edits are excluded.</strong></p>"
    )
    body += '<form action="/jobs" method="post"><label>Validation profile '
    body += f'<select name="profile">{options}</select></label>'
    body += f'<input type="hidden" name="run_id" value="{uuid.uuid4().hex}">'
    body += "<button>Start validation run</button></form>"
    body += '<p class="meta">One active worker · at most two queued runs · '
    body += "No automatic retry. Remote probes are offered only after explicit operator setup.</p>"
    body += '<div class="cards"><section class="card"><h2>Latest attempt</h2><p>'
    body += _link(rows[0]) if rows else "No submitted run"
    body += '</p></section><section class="card"><h2>Complete evidence</h2><p>'
    body += '<a href="/">Inspect the latest complete recorded inventory</a>. '
    body += "Completeness does not mean PASS.</p></section></div>"
    body += '<h2>Run history</h2><ol class="run-list">'
    body += "".join(f"<li>{_link(row)}</li>" for row in rows)
    return page("Run checks", body + "</ol>", section="/jobs")


def job_page(row: dict[str, Any], *, has_evidence: bool) -> str:
    body = f'<p><span class="badge {text(row["state"])}">{text(row["state"].capitalize())}</span> '
    body += (
        text(LABELS[row["profile"]])
        + '</p><p><a href="/help#run-controls">Status and recovery help</a></p>'
    )
    body += f'<p><a href="/jobs/{row["run_id"]}">Refresh status</a> · '
    body += '<a href="/jobs">All validation runs</a></p>'
    if row["state"] in ACTIVE:
        body += _cancel_form(row)
    if has_evidence:
        body += f'<p><a class="action" href="/runs/{row["run_id"]}">View recorded results</a></p>'
    else:
        body += (
            "<p>No validated result bundle is available yet. Missing results are not passes.</p>"
        )
    body += _table(
        "Run details",
        ["Field", "Recorded value"],
        [
            ["Committed source", row["commit"]],
            ["Submitted (UTC)", row["submitted_at"]],
            ["Started (UTC)", row["started_at"]],
            ["Finished (UTC)", row["finished_at"]],
            ["Process exit", row["exit_code"]],
            ["Stop reason", row["reason"]],
        ],
    )
    return page(f"Validation run {row['sequence']}", body, section="/jobs")


def _cancel_form(row: dict[str, Any]) -> str:
    return (
        f'<form action="/jobs/{row["run_id"]}/cancel" method="post">'
        f'<input type="hidden" name="run_id" value="{row["run_id"]}">'
        '<button>Cancel this run</button></form><p class="meta">Cancellation stops owned work. '
        "It does not prove that an in-flight remote call stopped.</p>"
    )
