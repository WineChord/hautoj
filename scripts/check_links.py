#!/usr/bin/env python3
"""Check internal routes, canonical sources, and safely mirrored images."""

from __future__ import annotations

from collections import Counter, defaultdict
import ipaddress
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from site_checks import (
    ROOT,
    Link,
    Reporter,
    heading_ids,
    is_external,
    links,
    load_json,
    markdown_files,
    mask_fenced_code,
    read_text,
    relative,
    snippet_targets,
    url_without_fragment,
)


CORPUS_PATH = ROOT / "data" / "corpus.json"
IMAGE_MANIFEST_PATH = ROOT / "data" / "image_manifest.json"
SITE_PREFIX = "/hautoj/"
UNSAFE_SCHEMES = {"data", "file", "javascript", "vbscript", "blob"}
SENSITIVE_QUERY_KEYS = re.compile(
    r"(?i)^(?:access_?token|api_?key|auth|authorization|credential|"
    r"password|secret|session|signature|token)$"
)
OFFICIAL_PROBLEM = re.compile(r"^https://acm\.haut\.edu\.cn/problem\.php\?id=\d+$")
OFFICIAL_CONTEST = re.compile(
    r"^https://acm\.haut\.edu\.cn/contest\.php\?(?:cid=\d+|page=[1-9]\d*)$"
)
HTML_ID = re.compile(r"""(?i)\bid\s*=\s*["'](?P<identifier>[^"']+)["']""")


def corpus_problems(corpus: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = corpus.get("problems")
    if not isinstance(raw, dict):
        return {}
    return {
        str(pid): value
        for pid, value in raw.items()
        if str(pid).isdigit() and isinstance(value, dict)
    }


def unsafe_hostname(hostname: str | None) -> bool:
    if not hostname:
        return True
    lowered = hostname.lower().rstrip(".")
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    return not address.is_global


def check_external(link: Link, reporter: Reporter) -> None:
    location = f"{relative(link.path)}:{link.line}"
    target = link.target
    if target.startswith("//"):
        reporter.add(location, "禁止省略协议的外部链接")
        return
    parsed = urlsplit(target)
    scheme = parsed.scheme.lower()
    if scheme in UNSAFE_SCHEMES:
        reporter.add(location, f"禁止 {scheme}: 链接")
        return
    if scheme == "mailto":
        if link.is_image:
            reporter.add(location, "图片不能使用 mailto:")
        return
    if scheme != "https":
        reporter.add(location, "外部链接必须使用 HTTPS")
        return
    if parsed.username is not None or parsed.password is not None:
        reporter.add(location, "外部链接不得内嵌用户名或密码")
    if unsafe_hostname(parsed.hostname):
        reporter.add(location, "外部链接不得指向本机、私网或特殊地址")
    for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
        if SENSITIVE_QUERY_KEYS.fullmatch(key):
            reporter.add(location, f"外部链接包含敏感查询参数名：{key}")
    if link.is_image:
        reporter.add(location, "题面图片必须镜像到仓库，禁止远程热链")
    if parsed.hostname == "acm.haut.edu.cn":
        if parsed.path == "/problem.php" and not OFFICIAL_PROBLEM.fullmatch(target):
            reporter.add(location, "HAUTOJ 原题链接必须使用规范 HTTPS 地址")
        if parsed.path == "/contest.php" and not OFFICIAL_CONTEST.fullmatch(target):
            reporter.add(location, "HAUTOJ 周赛链接必须使用规范 HTTPS 地址")


def resolve_local(
    path: Path, target: str, *, browser_relative: bool = False
) -> tuple[Path | None, str]:
    path_part, fragment = url_without_fragment(target)
    path_part = path_part.split("?", 1)[0]
    if not path_part:
        return path, fragment
    if path_part in {"/hautoj", "/hautoj/"}:
        candidate = ROOT / "docs" / "index.md"
    elif path_part.startswith(SITE_PREFIX):
        base = ROOT / "docs"
        path_part = path_part[len(SITE_PREFIX) :]
        candidate = base / path_part
    elif path_part.startswith("/"):
        return None, fragment
    else:
        base = (
            path.parent / path.stem
            if browser_relative and path.suffix.lower() == ".md"
            else path.parent
        )
        candidate = base / path_part
    candidate = candidate.resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError:
        return None, fragment

    choices: list[Path] = [candidate]
    if path_part.endswith("/"):
        choices = [candidate / "index.md", candidate.with_suffix(".md"), candidate]
    elif not candidate.suffix:
        choices.extend((candidate.with_suffix(".md"), candidate / "index.md"))
    for choice in choices:
        if choice.is_file():
            return choice, fragment
    return candidate, fragment


def target_heading_ids(path: Path, reporter: Reporter) -> set[str]:
    identifiers = heading_ids(path, reporter)
    text = mask_fenced_code(read_text(path, reporter))
    identifiers.update(
        unquote(match.group("identifier")) for match in HTML_ID.finditer(text)
    )
    return identifiers


def check_internal(
    link: Link,
    reporter: Reporter,
    heading_cache: dict[Path, set[str]],
) -> Path | None:
    location = f"{relative(link.path)}:{link.line}"
    target, fragment = resolve_local(
        link.path, link.target, browser_relative=link.kind == "html"
    )
    if target is None:
        reporter.add(location, "站内链接必须位于 /hautoj/ 或仓库内部")
        return None
    if not target.is_file():
        reporter.add(location, "站内目标不存在；目标未回显")
        return None
    if target.is_symlink():
        reporter.add(location, "站内链接不得指向符号链接")
    if fragment and target.suffix.lower() == ".md":
        identifiers = heading_cache.setdefault(
            target.resolve(), target_heading_ids(target, reporter)
        )
        if fragment not in identifiers:
            reporter.add(
                location,
                f"站内锚点不存在：{relative(target)}（片段未回显）",
            )
    return target


def check_problem_sources(
    pid: str,
    snippet_path: Path,
    page_path: Path,
    problem: dict[str, Any],
    manifest_by_pid: dict[str, list[dict[str, Any]]],
    reporter: Reporter,
) -> None:
    snippet_links = links(snippet_path, reporter)
    snippet_external = [
        link.target for link in snippet_links if is_external(link.target)
    ]
    expected_problem = f"https://acm.haut.edu.cn/problem.php?id={pid}"
    reporter.require(
        snippet_external == [expected_problem],
        snippet_path,
        "折叠摘要只能包含一次自己的 HAUTOJ 原题外链",
    )
    reporter.require(
        not any(link.is_image for link in snippet_links),
        snippet_path,
        "折叠摘要跨多个层级复用，题面图片应放在完整题解页",
    )

    found = links(page_path, reporter)
    external = [link.target for link in found if is_external(link.target)]
    expected_sources = {
        source
        for source in problem.get("solution_sources", [])
        if isinstance(source, str)
    }
    occurrence_sources = {
        str(occurrence.get("contest_url"))
        for occurrence in problem.get("occurrences", [])
        if isinstance(occurrence, dict)
    }
    image_sources = {
        source
        for source in problem.get("images", [])
        if isinstance(source, str)
    }
    allowed = {
        expected_problem,
        *expected_sources,
        *occurrence_sources,
        *image_sources,
    }
    unexpected = set(external) - allowed
    missing_sources = expected_sources - set(external)
    reporter.require(
        not unexpected,
        page_path,
        f"完整题解页含 {len(unexpected)} 个未登记的外部来源",
    )
    reporter.require(
        not missing_sources,
        page_path,
        f"完整题解页遗漏 {len(missing_sources)} 个 corpus 公开题解来源",
    )
    reporter.require(
        external.count(expected_problem) >= 1,
        page_path,
        "完整题解页必须包含 HAUTOJ 原题链接",
    )

    expected_images = {
        str(item.get("path"))
        for item in manifest_by_pid.get(pid, [])
        if item.get("status") == "available" and isinstance(item.get("path"), str)
    }
    actual_images: Counter[str] = Counter()
    for link in found:
        if not link.is_image or is_external(link.target):
            continue
        if not link.label:
            reporter.add(
                f"{relative(page_path)}:{link.line}",
                "题面图片必须提供有意义的替代文本",
            )
        path_part, _ = url_without_fragment(link.target)
        resolved: Path | None = None
        if path_part.startswith(SITE_PREFIX):
            path_part = path_part[len(SITE_PREFIX) :]
        else:
            resolved, _ = resolve_local(page_path, link.target)
            if resolved is not None:
                try:
                    path_part = str(
                        resolved.resolve().relative_to((ROOT / "docs").resolve())
                    )
                except ValueError:
                    pass
        actual_images[path_part] += 1
        if link.target.startswith("/"):
            valid_form = link.target.startswith(SITE_PREFIX)
        else:
            valid_form = resolved is not None and resolved.is_file()
        if not valid_form:
            reporter.add(
                f"{relative(page_path)}:{link.line}",
                "题面图片必须使用可解析的仓库路径或 /hautoj/ 根路径",
            )
    reporter.require(
        set(actual_images) == expected_images,
        page_path,
        f"题面图片与安全镜像清单不一致：缺少 "
        f"{len(expected_images - set(actual_images))}，多出 "
        f"{len(set(actual_images) - expected_images)}",
    )
    duplicate_images = [key for key, count in actual_images.items() if count != 1]
    reporter.require(
        not duplicate_images,
        page_path,
        f"每张题面图片必须恰好引用一次，异常 {len(duplicate_images)} 张",
    )


def main() -> None:
    reporter = Reporter()
    corpus_raw = load_json(CORPUS_PATH, reporter)
    manifest_raw = load_json(IMAGE_MANIFEST_PATH, reporter)
    corpus = corpus_raw if isinstance(corpus_raw, dict) else {}
    manifest = manifest_raw if isinstance(manifest_raw, dict) else {}
    problems = corpus_problems(corpus)

    manifest_by_pid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    manifest_images = manifest.get("images")
    if isinstance(manifest_images, list):
        for item in manifest_images:
            if isinstance(item, dict):
                manifest_by_pid[str(item.get("problem_id"))].append(item)

    files = markdown_files(include_downloads=True)
    heading_cache: dict[Path, set[str]] = {}
    link_count = 0
    internal_count = 0
    external_count = 0
    image_count = 0
    for path in files:
        for line, target in snippet_targets(path, reporter):
            location = f"{relative(path)}:{line}"
            candidate = Path(target)
            if candidate.is_absolute() or ".." in candidate.parts:
                reporter.add(location, "snippet 路径必须是安全的仓库相对路径")
                continue
            resolved = (ROOT / candidate).resolve()
            try:
                resolved.relative_to(ROOT.resolve())
            except ValueError:
                reporter.add(location, "snippet 路径越出仓库")
                continue
            if not resolved.is_file():
                reporter.add(location, "snippet 不存在；目标未回显")

        for link in links(path, reporter):
            link_count += 1
            if link.is_image:
                image_count += 1
            if is_external(link.target):
                external_count += 1
                check_external(link, reporter)
            else:
                internal_count += 1
                check_internal(link, reporter, heading_cache)

    snippet_dir = ROOT / "includes" / "problems"
    for pid, problem in problems.items():
        snippet_path = snippet_dir / f"haut-{pid}.md"
        page_path = ROOT / "docs" / "problems" / f"{pid}.md"
        if snippet_path.is_file() and page_path.is_file():
            check_problem_sources(
                pid,
                snippet_path,
                page_path,
                problem,
                manifest_by_pid,
                reporter,
            )

    reporter.finish()
    print(
        "链接检查通过："
        f"{len(files)} 个 Markdown 文件，{link_count} 个链接"
        f"（站内 {internal_count}、HTTPS 外链 {external_count}、图片 {image_count}）；"
        "规范来源、锚点、片段路径与本地图片镜像均可解析"
    )


if __name__ == "__main__":
    main()
