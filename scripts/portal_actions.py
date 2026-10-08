"""Exact same-origin form admission for fixed local validation actions."""

from __future__ import annotations

import re
from email.message import Message
from urllib.parse import parse_qs

from scripts.portal_jobs import JobController

MAX_FORM_BYTES = 1024


def same_origin(headers: Message, port: int) -> bool:
    hosts = headers.get_all("Host", [])
    origins = headers.get_all("Origin", [])
    sites = headers.get_all("Sec-Fetch-Site", [])
    return (
        len(hosts) == 1
        and hosts[0] in {f"127.0.0.1:{port}", f"localhost:{port}"}
        and origins == ["http://" + hosts[0]]
        and sites in ([], ["same-origin"])
    )


def form_length(headers: Message) -> int:
    lengths = headers.get_all("Content-Length", [])
    media = headers.get_all("Content-Type", [])
    if headers.get_all("Transfer-Encoding") or len(lengths) != 1:
        raise ValueError("An unambiguous bounded form length is required")
    if len(media) != 1 or media[0].lower() not in {
        "application/x-www-form-urlencoded",
        "application/x-www-form-urlencoded; charset=utf-8",
    }:
        raise ValueError("A UTF-8 form is required")
    if re.fullmatch(r"[0-9]{1,4}", lengths[0]) is None:
        raise ValueError("Invalid form length")
    length = int(lengths[0])
    if not 0 < length <= MAX_FORM_BYTES:
        raise ValueError("Form exceeds its size bound")
    return length


def perform(jobs: JobController, target: str, raw: bytes) -> str:
    values = parse_qs(
        raw.decode("utf-8"),
        strict_parsing=True,
        keep_blank_values=True,
        max_num_fields=3,
        encoding="utf-8",
        errors="strict",
    )
    if any(len(value) != 1 for value in values.values()):
        raise ValueError("Repeated form field")
    fields = {name: value[0] for name, value in values.items()}
    if target == "/jobs" and set(fields) == {"run_id", "profile"}:
        row = jobs.submit(fields["run_id"], fields["profile"])
    else:
        row = _cancel(jobs, target, fields)
    return "/jobs/" + row["run_id"]


def _cancel(jobs: JobController, target: str, fields: dict[str, str]) -> dict:
    match = re.fullmatch(r"/jobs/([a-f0-9]{32})/cancel", target)
    if match is None or fields != {"run_id": match[1]}:
        raise ValueError("Choose a fixed run or cancel action")
    return jobs.cancel(match[1])
