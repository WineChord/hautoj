#!/usr/bin/env python3
"""Shared, side-effect-free helpers for HAUTOJ repository checks."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Iterable, Iterator
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_CONTESTS = 78
EXPECTED_PUBLIC_CONTESTS = 76
EXPECTED_PROBLEMS = 526
EXPECTED_OCCURRENCES = 588
EXPECTED_TOPICS = 10

MARKDOWN_FENCE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<marker>`{3,}|~{3,})(?P<info>.*)$"
)
SNIPPET_DIRECTIVE = re.compile(
    r'(?m)^[ \t]*--8<--[ \t]+"(?P<target>[^"\r\n]+)"[ \t]*$'
)
INLINE_LINK = re.compile(
    r'(?P<image>!)?\[(?P<label>[^\]\r\n]*)\]'
    r'\((?P<target><[^>\r\n]+>|[^)\r\n]+)\)'
)
AUTOLINK = re.compile(r"<(?P<target>https?://[^<>\s]+)>")
HTML_LINK = re.compile(
    r"""(?is)<(?P<tag>a|img|source)\b[^>]*?\b"""
    r"""(?P<attribute>href|src)\s*=\s*(?P<quote>["'])"""
    r"""(?P<target>.*?)(?P=quote)"""
)
EXPLICIT_HEADING_ID = re.compile(r"\s+\{#(?P<identifier>[A-Za-z0-9_.:-]+)\}\s*$")


class Reporter:
    """Collect deterministic diagnostics without stopping at the first error."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def add(self, location: str | Path, message: str) -> None:
        if isinstance(location, Path):
            location = relative(location)
        self.errors.append(f"{location}: {message}")

    def require(self, condition: bool, location: str | Path, message: str) -> None:
        if not condition:
            self.add(location, message)

    def finish(self) -> None:
        if not self.errors:
            return
        for error in sorted(set(self.errors)):
            print(error, file=__import__("sys").stderr)
        raise SystemExit(1)


@dataclass(frozen=True)
class CodeBlock:
    path: Path
    line: int
    language: str
    source: str
    skipped: bool


@dataclass(frozen=True)
class Link:
    path: Path
    line: int
    target: str
    label: str
    is_image: bool
    kind: str


def relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def read_text(path: Path, reporter: Reporter | None = None) -> str:
    try:
        data = path.read_bytes()
        text = data.decode("utf-8")
    except (OSError, UnicodeError) as error:
        if reporter is None:
            raise
        reporter.add(path, f"无法按 UTF-8 读取：{error}")
        return ""
    if "\x00" in text and reporter is not None:
        reporter.add(path, "文本文件包含 NUL 字节")
    return text


def load_json(path: Path, reporter: Reporter) -> Any:
    if not path.is_file():
        reporter.add(path, "文件不存在")
        return {}
    try:
        return json.loads(read_text(path))
    except (json.JSONDecodeError, OSError, UnicodeError) as error:
        reporter.add(path, f"JSON 无法解析：{error}")
        return {}


def markdown_files(*, include_downloads: bool = True) -> list[Path]:
    files: list[Path] = []
    readme = ROOT / "README.md"
    if readme.is_file():
        files.append(readme)
    for base in (ROOT / "docs", ROOT / "includes"):
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            if not include_downloads and "downloads" in path.parts:
                continue
            files.append(path)
    return files


def _fence_language(info: str) -> str:
    token = info.strip().split(maxsplit=1)
    if not token:
        return ""
    return token[0].strip("{}").lower()


def code_blocks(path: Path, reporter: Reporter | None = None) -> list[CodeBlock]:
    """Extract fenced blocks, preserving their source line and indentation."""

    text = read_text(path, reporter)
    lines = text.splitlines()
    blocks: list[CodeBlock] = []
    active_marker = ""
    active_indent = ""
    active_language = ""
    active_line = 0
    active_source: list[str] = []
    active_skip = False
    for number, line in enumerate(lines, 1):
        match = MARKDOWN_FENCE.match(line)
        if not active_marker:
            if not match:
                continue
            active_marker = match.group("marker")
            active_indent = match.group("indent")
            active_language = _fence_language(match.group("info"))
            active_line = number
            active_source = []
            preceding = "\n".join(lines[max(0, number - 4) : number - 1])
            active_skip = bool(
                re.search(r"<!--\s*compile:skip(?:\s+[^>]*)?\s*-->", preceding)
            )
            continue
        if (
            match
            and match.group("marker")[0] == active_marker[0]
            and len(match.group("marker")) >= len(active_marker)
            and not match.group("info").strip()
        ):
            blocks.append(
                CodeBlock(
                    path=path,
                    line=active_line,
                    language=active_language,
                    source="\n".join(active_source) + "\n",
                    skipped=active_skip,
                )
            )
            active_marker = ""
            active_indent = ""
            active_language = ""
            active_line = 0
            active_source = []
            active_skip = False
            continue
        if active_indent and line.startswith(active_indent):
            line = line[len(active_indent) :]
        active_source.append(line)
    if active_marker and reporter is not None:
        reporter.add(f"{relative(path)}:{active_line}", "代码块未闭合")
    return blocks


def mask_fenced_code(text: str) -> str:
    """Replace fenced-code characters with spaces while retaining line numbers."""

    output: list[str] = []
    active_marker = ""
    for line in text.splitlines(keepends=True):
        body = line[:-1] if line.endswith("\n") else line
        ending = "\n" if line.endswith("\n") else ""
        match = MARKDOWN_FENCE.match(body)
        if not active_marker:
            if match:
                active_marker = match.group("marker")
                output.append(" " * len(body) + ending)
            else:
                output.append(body + ending)
            continue
        output.append(" " * len(body) + ending)
        if (
            match
            and match.group("marker")[0] == active_marker[0]
            and len(match.group("marker")) >= len(active_marker)
            and not match.group("info").strip()
        ):
            active_marker = ""
    return "".join(output)


def snippet_targets(path: Path, reporter: Reporter | None = None) -> list[tuple[int, str]]:
    text = mask_fenced_code(read_text(path, reporter))
    return [
        (line_number(text, match.start()), match.group("target"))
        for match in SNIPPET_DIRECTIVE.finditer(text)
    ]


def _clean_link_target(raw: str) -> str:
    target = raw.strip()
    if target.startswith("<") and target.endswith(">"):
        return target[1:-1].strip()
    # Markdown permits an optional quoted title after the URL.
    match = re.match(r"""^(?P<url>\S+?)(?:\s+["'][^"']*["'])?$""", target)
    return match.group("url") if match else target


def links(path: Path, reporter: Reporter | None = None) -> list[Link]:
    text = mask_fenced_code(read_text(path, reporter))
    found: list[Link] = []
    occupied: list[tuple[int, int]] = []
    for match in INLINE_LINK.finditer(text):
        raw_target = match.group("target").strip()
        # Problem statements frequently contain mathematical forms such as
        # ``a[i](0<a[i]<=n)``.  They resemble Markdown links lexically but are
        # not valid destinations and must remain problem semantics.
        if (
            not raw_target.startswith("<")
            and (
                re.search(r"(?:<=|>=|≤|≥)", raw_target)
                or re.search(r"\[[^\]]*\]", raw_target)
                or "<" in raw_target
                or ">" in raw_target
            )
        ):
            continue
        occupied.append(match.span())
        found.append(
            Link(
                path=path,
                line=line_number(text, match.start()),
                target=_clean_link_target(raw_target),
                label=match.group("label").strip(),
                is_image=bool(match.group("image")),
                kind="markdown",
            )
        )
    for match in AUTOLINK.finditer(text):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        found.append(
            Link(
                path=path,
                line=line_number(text, match.start()),
                target=match.group("target"),
                label=match.group("target"),
                is_image=False,
                kind="autolink",
            )
        )
    for match in HTML_LINK.finditer(text):
        found.append(
            Link(
                path=path,
                line=line_number(text, match.start()),
                target=match.group("target").strip(),
                label="",
                is_image=match.group("tag").lower() in {"img", "source"},
                kind="html",
            )
        )
    return found


def url_without_fragment(target: str) -> tuple[str, str]:
    before, separator, after = target.partition("#")
    return unquote(before), unquote(after) if separator else ""


def is_external(target: str) -> bool:
    return bool(urlsplit(target).scheme or target.startswith("//"))


def iter_text_files() -> Iterator[Path]:
    """Yield public text/config inputs, excluding generated output and check code."""

    allowed_suffixes = {
        ".css",
        ".html",
        ".js",
        ".json",
        ".md",
        ".py",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
    excluded_parts = {".git", ".venv", "__pycache__", "node_modules", "site"}
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in allowed_suffixes:
            continue
        relative_parts = path.relative_to(ROOT).parts
        if excluded_parts.intersection(relative_parts):
            continue
        yield path


def heading_ids(path: Path, reporter: Reporter | None = None) -> set[str]:
    """Approximate Python-Markdown's Unicode TOC IDs for source link checks."""

    text = mask_fenced_code(read_text(path, reporter))
    identifiers: set[str] = {""}
    used: dict[str, int] = {}
    for line in text.splitlines():
        match = re.match(r"^\s{0,3}#{1,6}\s+(?P<title>.+?)\s*#*\s*$", line)
        if not match:
            continue
        title = match.group("title")
        explicit = EXPLICIT_HEADING_ID.search(title)
        if explicit:
            identifier = explicit.group("identifier")
        else:
            title = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", title)
            title = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", title)
            title = re.sub(r"[`*_~]", "", title).strip().lower()
            identifier = "".join(
                character
                for character in title
                if character.isalnum() or character in {" ", "-", "_"}
            )
            identifier = re.sub(r"[\s-]+", "-", identifier).strip("-")
        if not identifier:
            continue
        count = used.get(identifier, 0)
        used[identifier] = count + 1
        if count:
            identifier = f"{identifier}_{count}"
        identifiers.add(identifier)
    return identifiers


def unique_ordered(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))
