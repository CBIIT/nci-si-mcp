"""Validate static-site links and resolve excluded source references to the pinned repository."""

from __future__ import annotations

import html
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit


class _Identifiers(HTMLParser):
    def __init__(self, content: str):
        super().__init__()
        self.identifiers: set[str] = set()
        self.feed(content)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.identifiers.update(value for key, value in attrs if key == "id" and value)


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Documentation link escapes its root: {path.name}")
    return resolved


def _source_path(path: Path, root: Path) -> Path:
    source = _inside(root / path, root)
    if source.name == "index.html" and source.parent.is_dir() and not source.exists():
        source = source.parent
    if not source.exists() and source.suffix == ".html":
        source = source.with_suffix(".md")
    if not source.exists():
        raise ValueError(f"Unresolved documentation link: {path}")
    return source


def _source_link(path: Path, root: Path, commit: str, fragment: str) -> str:
    source = _source_path(path, root)
    relative = source.relative_to(root.resolve()).as_posix()
    suffix = "#" + quote(fragment) if fragment else ""
    kind = "tree" if source.is_dir() else "blob"
    return f"https://github.com/CBIIT/nci-si-mcp/{kind}/{commit}/{quote(relative)}{suffix}"


def _check_fragment(target: Path, fragment: str) -> None:
    if fragment and target.suffix == ".html":
        identifiers = _Identifiers(target.read_text()).identifiers
        if fragment not in identifiers:
            raise ValueError(f"Missing documentation anchor: {target.name}#{fragment}")


class _Page(HTMLParser):
    def __init__(self, path: Path, site: Path, source: Path, commit: str):
        super().__init__(convert_charrefs=False)
        self.path, self.site, self.source, self.commit = path, site, source, commit
        self.output: list[str] = []

    def _local(self, value: str, asset: bool) -> str:
        parts = urlsplit(value)
        if parts.path.startswith("/"):
            raise ValueError(
                f"Site links must support relative deployment paths: {self.path.name}: {value}"
            )
        target = _inside(self.path.parent / unquote(parts.path), self.site)
        if not parts.path:
            target = self.path
        if target.is_dir():
            target /= "index.html"
        if target.is_file():
            _check_fragment(target, unquote(parts.fragment))
            return value
        if asset:
            raise ValueError(f"Missing local documentation asset: {value}")
        return _source_link(
            target.relative_to(self.site.resolve()), self.source, self.commit, parts.fragment
        )

    def _url(self, value: str, asset: bool) -> str:
        parts = urlsplit(value)
        if parts.scheme or parts.netloc:
            if asset or parts.scheme not in {"https", "http", "mailto"}:
                raise ValueError(
                    "Documentation cannot load external runtime assets or unsafe links"
                )
            return value
        return self._local(value, asset)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "img" and urlsplit(attributes.get("src") or "").netloc:
            self.output.append(html.escape(attributes.get("alt") or "Image"))
            return
        rendered = [self._attribute(tag, key, value) for key, value in attrs]
        self.output.append("<" + tag + "".join(rendered) + ">")

    def _attribute(self, tag: str, key: str, value: str | None) -> str:
        if value is None:
            return " " + key
        if key in {"href", "src"}:
            value = self._url(value, tag != "a")
        return f' {key}="{html.escape(value, quote=True)}"'

    def handle_endtag(self, tag: str) -> None:
        self.output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.output.append(data)

    def handle_entityref(self, name: str) -> None:
        self.output.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.output.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.output.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.output.append(f"<!{decl}>")


def rewrite_page(content: str, path: Path, site: Path, source: Path, commit: str) -> str:
    """Preserve relative local navigation; reject unknown targets before publication."""
    parser = _Page(path, site, source, commit)
    parser.feed(content)
    parser.close()
    return "".join(parser.output)
