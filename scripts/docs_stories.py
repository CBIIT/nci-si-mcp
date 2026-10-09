"""Partition the generated acceptance catalogue without changing its narrative or evidence."""

from __future__ import annotations

import re
from pathlib import Path

GROUPS = (
    (
        "terminology",
        "Terminology and search",
        "Find, inspect and explore NCIt concepts.",
        (
            "terminology-catalogue",
            "concept-detail",
            "batch-concepts",
            "lexical-search",
            "indexed-search",
            "search-retired",
            "hierarchy",
            "value-set",
            "neighbourhood",
            "subsets-and-mappings",
            "retired-replacements",
            "relationship-catalogue",
        ),
    ),
    (
        "registry",
        "Data elements and forms",
        "Understand caDSR metadata and permitted values.",
        (
            "registry-state",
            "data-element",
            "registry-capabilities",
            "data-element-search",
            "matching-candidates",
            "forms",
            "code-maps",
            "registry-contexts",
        ),
    ),
    (
        "cross-domain",
        "Connect terminology and metadata",
        "Trace concepts across registry and commons data.",
        (
            "concept-uses",
            "permissible-value-concept",
            "stored-values",
            "release-alignment",
        ),
    ),
    (
        "workflows",
        "Research workflows",
        "Follow complete domain tasks across several operations.",
        (
            "ground-value",
            "expand-cohort",
            "harmonize-dictionary",
        ),
    ),
    (
        "trust",
        "Reliable, traceable answers",
        "Understand versions, limits, attribution and failures.",
        (
            "trace-an-answer",
            "choose-release",
            "keep-session-release",
            "reuse-content",
            "distinguish-empty-and-failed",
            "respect-upstream-capacity",
            "protect-credentials",
            "preserve-attribution",
            "page-and-bound-results",
            "preserve-input-intent",
        ),
    ),
    (
        "integration",
        "Application integration and access",
        "Discover the interface and understand permitted access.",
        (
            "discover-tools",
            "interpretable-answers",
            "prompts-and-resources",
            "governed-caller-access",
        ),
    ),
)


def _sections(source: str) -> dict[str, str]:
    chunks = re.split(r'<a id="([a-z][a-z0-9-]*)"></a>\n\n', source)
    pairs = list(zip(chunks[1::2], chunks[2::2], strict=True))
    sections = dict(pairs)
    expected = [key for _, _, _, keys in GROUPS for key in keys]
    if len(sections) != len(pairs) or set(sections) != set(expected):
        raise ValueError("Every catalogue story must have exactly one website section")
    return sections


def _case_count(section: str) -> int:
    match = re.search(r"<summary>Exact executable cases \((\d+)\)</summary>", section)
    if match is None:
        raise ValueError("Story is missing its executable case count")
    count = int(match[1])
    if len(re.findall(r"<code>.*?</code>", section)) != count:
        raise ValueError("Story case count disagrees with its evidence")
    return count


def _title(section: str) -> str:
    if not section.startswith("## "):
        raise ValueError("Story is missing its title")
    return section.splitlines()[0][3:]


def _card(title: str, href: str, description: str, meta: str) -> str:
    return (
        f'<article class="story-card"><h2><a href="{href}">{title}</a></h2>'
        f'<p>{description}</p><p class="story-card__meta">{meta}</p></article>'
    )


def _group_pages(
    group: tuple[str, str, str, tuple[str, ...]],
    sections: dict[str, str],
) -> tuple[dict[str, str], str]:
    slug, title, description, keys = group
    filename = f"stories-{slug}.md"
    pages: dict[str, str] = {}
    cards = []
    for key in keys:
        section = sections[key]
        count = _case_count(section)
        goal = re.search(r"\*\*User goal:\*\* (.+)", section)
        if goal is None:
            raise ValueError("Story is missing its user goal")
        cards.append(_card(_title(section), f"story-{key}.html", goal[1], f"{count} cases"))
        pages[f"story-{key}.md"] = (
            f"# {_title(section)}\n\n"
            f"[All behavioral stories](behavioural-tests.md) / [{title}]({filename})\n\n"
            f"**{count} acceptance cases · Expected behavior, not a run result**\n\n"
            + section.partition("\n")[2].lstrip()
        )
    pages[filename] = (
        f"# {title}\n\n[All behavioral stories](behavioural-tests.md)\n\n{description}\n\n"
        "Choose a user story. Each page explains its scenario "
        "and keeps exact test IDs expandable.\n\n"
        '<div class="story-grid">' + "\n".join(cards) + "</div>\n"
    )
    total = sum(_case_count(sections[key]) for key in keys)
    return pages, _card(
        title, filename.replace(".md", ".html"), description, f"{len(keys)} stories · {total} cases"
    )


def partition_catalogue(source: str) -> dict[str, str]:
    """Keep the canonical Markdown unchanged; split only its public website presentation."""
    sections = _sections(source)
    pages: dict[str, str] = {}
    cards = []
    for group in GROUPS:
        generated, card = _group_pages(group, sections)
        pages.update(generated)
        cards.append(card)
    intro = source.partition("| User story | Cases |")[0]
    summary = re.search(r"\*\*\d+ MCP acceptance cases,.*?\*\*", intro)
    if summary is None:
        raise ValueError("Catalogue is missing its inventory summary")
    overview = (
        "# Behavioral stories\n\n"
        "Explore what the MCP server is expected to do, organized around real user tasks. "
        "Choose a topic, then a story: each explains the situation, action and expected answer.\n\n"
        f"{summary[0]}\n\n"
        "> These are expected behaviors, not execution results. "
        "Fixture coverage does not establish live-service access.\n\n"
    )
    overview += '## Explore by task\n\n<div class="story-grid">' + "\n".join(cards) + "</div>\n\n"
    # Preserve incoming catalogue anchors as compact links, not duplicated case content.
    overview += "## Find a specific story\n\n<details><summary>All user stories</summary>\n\n"
    for key, section in sections.items():
        overview += f'<p id="{key}"><a href="story-{key}.html">{_title(section)}</a></p>\n'
    explanation = intro.partition(summary[0])[2].strip()
    pages["behavioural-tests.md"] = (
        overview + "\n</details>\n\n## How to read these stories\n\n" + explanation + "\n"
    )
    return pages


def stage_story_pages(content: Path) -> None:
    catalogue = content / "docs/behavioural-tests.md"
    for name, text in partition_catalogue(catalogue.read_text()).items():
        (catalogue.parent / name).write_text(text)
