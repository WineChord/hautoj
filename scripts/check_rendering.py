#!/usr/bin/env python3
"""Validate source files, generated routes, and representative browser rendering."""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable, Optional, Sequence
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PATH = re.compile(r"(?:/Users/|/var/folders/|file://)", re.IGNORECASE)
PRIVATE_ADDRESS = re.compile(
    r"https?://(?:"
    r"10(?:\.\d{1,3}){3}|"
    r"192\.168(?:\.\d{1,3}){2}|"
    r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}"
    r")(?::\d+)?(?:/|\b)",
    re.IGNORECASE,
)
CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
MAX_VISUAL_PAGES = 5
VIEWPORTS = ((1440, 1000, False, "desktop"), (390, 844, True, "mobile"))
PALETTES = (("default", "light"), ("slate", "dark"))


@dataclass
class PageFacts:
    path: Path
    anchors: set[str] = field(default_factory=set)
    resources: list[tuple[str, str, str]] = field(default_factory=list)
    has_article: bool = False
    problem_details: int = 0
    open_problem_details: int = 0
    cpp_blocks: int = 0
    cpp_tokens: int = 0
    cpp_cjk_characters: int = 0
    title: str = ""


class PageParser(HTMLParser):
    def __init__(self, path: Path) -> None:
        super().__init__(convert_charrefs=True)
        self.facts = PageFacts(path)
        self._article_depth = 0
        self._cpp_div_depth = 0
        self._cpp_code_depth = 0
        self._title_depth = 0
        self._title_buffer: list[str] = []

    @staticmethod
    def classes(attrs: dict[str, Optional[str]]) -> set[str]:
        return set((attrs.get("class") or "").split())

    def handle_starttag(
        self,
        tag: str,
        attrs_sequence: list[tuple[str, Optional[str]]],
    ) -> None:
        attrs = dict(attrs_sequence)
        classes = self.classes(attrs)
        element_id = attrs.get("id")
        if element_id:
            self.facts.anchors.add(element_id)
        if tag == "a" and attrs.get("name"):
            self.facts.anchors.add(attrs["name"] or "")
        if tag == "article" and "md-content__inner" in classes:
            self._article_depth += 1
            self.facts.has_article = True
        elif self._article_depth and tag == "article":
            self._article_depth += 1
        if tag == "details" and "problem" in classes:
            self.facts.problem_details += 1
            if "open" in attrs:
                self.facts.open_problem_details += 1
        if tag == "div":
            if self._cpp_div_depth:
                self._cpp_div_depth += 1
            elif "highlight" in classes and "language-cpp" in classes:
                self._cpp_div_depth = 1
                self.facts.cpp_blocks += 1
        if tag == "code" and self._cpp_div_depth:
            self._cpp_code_depth += 1
        if tag == "span" and self._cpp_code_depth and classes:
            self.facts.cpp_tokens += 1
        if tag == "title":
            self._title_depth += 1
        for attribute in ("href", "src", "poster"):
            value = attrs.get(attribute)
            if value:
                self.facts.resources.append((tag, attribute, value))
        if tag == "source" and attrs.get("srcset"):
            for candidate in (attrs["srcset"] or "").split(","):
                value = candidate.strip().split(" ", 1)[0]
                if value:
                    self.facts.resources.append((tag, "srcset", value))

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, Optional[str]]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "code" and self._cpp_code_depth:
            self._cpp_code_depth -= 1
        if tag == "div" and self._cpp_div_depth:
            self._cpp_div_depth -= 1
        if tag == "article" and self._article_depth:
            self._article_depth -= 1
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
            self.facts.title = "".join(self._title_buffer).strip()

    def handle_data(self, data: str) -> None:
        if self._title_depth:
            self._title_buffer.append(data)
        if self._cpp_code_depth:
            self.facts.cpp_cjk_characters += len(CJK.findall(data))


def source_paths() -> list[Path]:
    paths = [ROOT / "README.md"]
    paths.extend(sorted((ROOT / "docs").rglob("*.md")))
    includes = ROOT / "includes"
    if includes.is_dir():
        paths.extend(sorted(includes.rglob("*.md")))
    return [path for path in paths if path.is_file()]


def scan_markdown(path: Path, errors: list[str]) -> int:
    text = path.read_text(encoding="utf-8")
    label = path.relative_to(ROOT)
    if "\ufffd" in text:
        errors.append(f"{label}: contains the Unicode replacement character")
    if PRIVATE_PATH.search(text):
        errors.append(f"{label}: contains a local filesystem path")
    if PRIVATE_ADDRESS.search(text):
        errors.append(f"{label}: contains a private-network URL")
    active_character: Optional[str] = None
    active_length = 0
    active_line = 0
    cpp_blocks = 0
    for number, line in enumerate(text.splitlines(), 1):
        match = FENCE.match(line)
        if match is None:
            continue
        marker = match.group(1)
        character = marker[0]
        if active_character is None:
            active_character = character
            active_length = len(marker)
            active_line = number
            info = line[match.end() :].strip().split()
            if info and info[0].lower() in {"cpp", "c++"}:
                cpp_blocks += 1
            continue
        if character == active_character and len(marker) >= active_length:
            active_character = None
            active_length = 0
            active_line = 0
    if active_character is not None:
        errors.append(f"{label}:{active_line}: unclosed fenced code block")
    return cpp_blocks


def site_settings() -> tuple[str, str]:
    config = ROOT / "mkdocs.yml"
    if not config.is_file():
        return "/", ""
    text = config.read_text(encoding="utf-8")
    match = re.search(r"(?m)^site_url:\s*['\"]?([^'\"\s]+)", text)
    if match is None:
        return "/", ""
    parsed = urlparse(match.group(1))
    base = parsed.path or "/"
    if not base.startswith("/"):
        base = "/" + base
    if not base.endswith("/"):
        base += "/"
    return base, parsed.netloc.lower()


def parse_generated_pages(
    site_dir: Path,
    errors: list[str],
) -> dict[Path, PageFacts]:
    pages: dict[Path, PageFacts] = {}
    for path in sorted(site_dir.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(site_dir)
        if "\ufffd" in text:
            errors.append(f"{relative}: generated HTML contains replacement text")
        if PRIVATE_PATH.search(text):
            errors.append(f"{relative}: generated HTML contains a local path")
        if PRIVATE_ADDRESS.search(text):
            errors.append(f"{relative}: generated HTML contains a private URL")
        parser = PageParser(path)
        parser.feed(text)
        facts = parser.facts
        pages[path.resolve()] = facts
        if path.name == "index.html" and not facts.has_article:
            errors.append(f"{relative}: documentation article is missing")
        if not facts.title:
            errors.append(f"{relative}: page title is empty")
        if facts.open_problem_details:
            errors.append(
                f"{relative}: {facts.open_problem_details} problem blocks "
                "are expanded by default"
            )
        if facts.cpp_blocks and facts.cpp_tokens < facts.cpp_blocks:
            errors.append(
                f"{relative}: C++ blocks are missing Pygments token markup"
            )
    if not pages:
        errors.append(f"{site_dir}: generated site has no HTML pages")
    return pages


def route_candidates(candidate: Path, raw_path: str) -> list[Path]:
    if not raw_path or raw_path.endswith("/"):
        return [candidate / "index.html"]
    if candidate.suffix:
        return [candidate]
    return [candidate, candidate / "index.html"]


def internal_target(
    page: Path,
    value: str,
    site_dir: Path,
    site_base: str,
    site_host: str,
) -> tuple[Optional[Path], str, Optional[str]]:
    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    if scheme in {"mailto", "tel", "data"}:
        return None, "", None
    if scheme == "javascript":
        if value.strip().lower() == "javascript:void(0)":
            return None, "", None
        return None, "", "javascript URLs are not allowed"
    if scheme in {"http", "https"}:
        if not site_host or parsed.netloc.lower() != site_host:
            return None, "", None
    elif scheme or parsed.netloc:
        return None, "", None
    raw_path = unquote(parsed.path)
    if not raw_path:
        return page, unquote(parsed.fragment), None
    if raw_path.startswith("/"):
        normalized_base = site_base.rstrip("/")
        if raw_path == normalized_base:
            relative = ""
        elif raw_path.startswith(site_base):
            relative = raw_path[len(site_base) :]
        else:
            return (
                None,
                "",
                f"root-relative route escapes the configured base {site_base}",
            )
        candidate = site_dir / relative
    else:
        candidate = page.parent / raw_path
    site_root = site_dir.resolve()
    candidates = [item.resolve() for item in route_candidates(candidate, raw_path)]
    if any(site_root == item or site_root in item.parents for item in candidates):
        for item in candidates:
            if item.is_file():
                return item, unquote(parsed.fragment), None
    return candidates[-1], unquote(parsed.fragment), None


def validate_internal_resources(
    site_dir: Path,
    pages: dict[Path, PageFacts],
    errors: list[str],
) -> None:
    site_base, site_host = site_settings()
    for page, facts in pages.items():
        relative_page = page.relative_to(site_dir.resolve())
        for tag, attribute, value in facts.resources:
            target, fragment, issue = internal_target(
                page,
                value,
                site_dir,
                site_base,
                site_host,
            )
            if issue:
                errors.append(
                    f"{relative_page}: {tag}[{attribute}] {value!r}: {issue}"
                )
                continue
            if target is None:
                continue
            if not target.is_file():
                errors.append(
                    f"{relative_page}: broken internal resource {value!r}"
                )
                continue
            if fragment and target.suffix.lower() == ".html":
                target_facts = pages.get(target.resolve())
                if target_facts is None:
                    parser = PageParser(target)
                    parser.feed(target.read_text(encoding="utf-8"))
                    target_facts = parser.facts
                    pages[target.resolve()] = target_facts
                if fragment not in target_facts.anchors:
                    errors.append(
                        f"{relative_page}: missing anchor #{fragment} "
                        f"in {target.relative_to(site_dir)}"
                    )


def validate_expected_routes(
    site_dir: Path,
    pages: dict[Path, PageFacts],
    errors: list[str],
) -> None:
    corpus_path = ROOT / "data" / "corpus.json"
    if not corpus_path.is_file():
        return
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    problem_pages: list[Path] = []
    for pid in sorted(int(value) for value in corpus["problems"]):
        target = (site_dir / "problems" / str(pid) / "index.html").resolve()
        if target not in pages:
            errors.append(f"missing canonical problem route /problems/{pid}/")
        else:
            problem_pages.append(target)
            facts = pages[target]
            if not facts.problem_details:
                errors.append(f"/problems/{pid}/: problem block is not collapsible")
            if not facts.cpp_blocks:
                errors.append(f"/problems/{pid}/: highlighted C++ block is missing")
    contest_root = site_dir / "contests"
    for contest in corpus["contests"]:
        cid = str(contest["cid"])
        matches = [
            path.resolve()
            for path in contest_root.glob(f"**/{cid}/index.html")
            if path.is_file()
        ]
        if len(matches) != 1:
            errors.append(
                f"CID {cid}: expected one contest route, found {len(matches)}"
            )
    if len(problem_pages) != int(corpus["unique_problem_count"]):
        errors.append(
            "canonical problem route count differs from corpus "
            f"({len(problem_pages)} versus {corpus['unique_problem_count']})"
        )


def validate_runtime_assets(errors: list[str]) -> None:
    css_path = ROOT / "docs" / "stylesheets" / "extra.css"
    script_path = ROOT / "docs" / "javascripts" / "mathjax.js"
    if not css_path.is_file():
        errors.append("docs/stylesheets/extra.css is missing")
    else:
        css = css_path.read_text(encoding="utf-8")
        if "--md-code-font" not in css:
            errors.append("extra.css does not define the Material code font")
        if not re.search(r"Noto Sans (?:Mono )?CJK SC", css):
            errors.append("extra.css lacks an explicit CJK code-font fallback")
        if "overflow-x: auto" not in css:
            errors.append("extra.css does not make long code horizontally scrollable")
    if not script_path.is_file():
        errors.append("docs/javascripts/mathjax.js is missing")
    else:
        script = script_path.read_text(encoding="utf-8")
        for marker in ("window.MathJax", "document$.subscribe", "typesetPromise"):
            if marker not in script:
                errors.append(f"mathjax.js is missing {marker}")


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format_string: str, *args: object) -> None:
        pass


@contextlib.contextmanager
def serve_directory(directory: Path) -> Iterable[str]:
    class Handler(QuietHandler):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, directory=str(directory), **kwargs)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def chrome_binary(explicit: Optional[str]) -> Optional[str]:
    candidates = [
        explicit,
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    return next(
        (
            candidate
            for candidate in candidates
            if candidate and Path(candidate).is_file()
        ),
        None,
    )


def chromedriver_binary(browser: str) -> Optional[str]:
    result = subprocess.run(
        [browser, "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    match = re.search(r"\b(\d+)\.", result.stdout + result.stderr)
    browser_major = match.group(1) if match else None
    candidates = [shutil.which("chromedriver")]
    cache = Path.home() / ".cache" / "selenium" / "chromedriver"
    if cache.is_dir():
        candidates.extend(
            str(path)
            for path in sorted(cache.glob("*/*/chromedriver"), reverse=True)
        )
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        if browser_major is None:
            return candidate
        version = subprocess.run(
            [candidate, "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        if re.search(rf"\b{re.escape(browser_major)}\.", version.stdout):
            return candidate
    return None


def representative_pages(
    site_dir: Path,
    pages: dict[Path, PageFacts],
) -> list[Path]:
    root = site_dir.resolve()
    selected: list[Path] = []
    corpus_path = ROOT / "data" / "corpus.json"
    corpus = (
        json.loads(corpus_path.read_text(encoding="utf-8"))
        if corpus_path.is_file()
        else {}
    )
    contest_ids = {
        str(contest["cid"]) for contest in corpus.get("contests", [])
    }
    problem_ids = set(corpus.get("problems", {}))

    def add(path: Optional[Path]) -> None:
        if path is not None and path not in selected and path in pages:
            selected.append(path)

    add((root / "index.html").resolve())
    ordered = sorted(pages)
    add(
        next(
            (
                page
                for page in ordered
                if "contests" in page.relative_to(root).parts
                and page.parent.name in contest_ids
            ),
            None,
        )
    )
    add(
        next(
            (
                page
                for page in ordered
                if {"topics", "knowledge"}.intersection(
                    page.relative_to(root).parts
                )
                and page.parent.name not in {"topics", "knowledge"}
            ),
            None,
        )
    )
    add(
        next(
            (
                page
                for page in ordered
                if "guide" in page.relative_to(root).parts
                and page.parent.name != "guide"
            ),
            None,
        )
    )
    add(
        next(
            (
                page
                for page in ordered
                if page.parent.name in problem_ids
                and pages[page].cpp_blocks
                and pages[page].cpp_cjk_characters
            ),
            None,
        )
    )
    if not any(pages[path].cpp_blocks for path in selected):
        add(next((page for page in ordered if pages[page].cpp_blocks), None))
    if not any(pages[path].problem_details for path in selected):
        add(next((page for page in ordered if pages[page].problem_details), None))
    return selected[:MAX_VISUAL_PAGES]


def page_route(page: Path, site_dir: Path) -> str:
    relative = page.relative_to(site_dir)
    if relative == Path("index.html"):
        return "/"
    if relative.name == "index.html":
        return "/" + relative.parent.as_posix() + "/"
    return "/" + relative.as_posix()


def browser_audit(
    site_dir: Path,
    pages: dict[Path, PageFacts],
    explicit_chrome: Optional[str],
    screenshots_dir: Optional[Path],
    errors: list[str],
) -> int:
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError:
        errors.append("browser audit requires Selenium")
        return 0
    browser = chrome_binary(explicit_chrome)
    if browser is None:
        errors.append("browser audit could not find Chrome or Chromium")
        return 0
    selected = representative_pages(site_dir, pages)
    if not selected:
        errors.append("browser audit found no representative pages")
        return 0
    if screenshots_dir:
        screenshots_dir.mkdir(parents=True, exist_ok=True)
    options = Options()
    options.binary_location = browser
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.set_capability("pageLoadStrategy", "eager")
    options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    driver_path = chromedriver_binary(browser)
    service = Service(executable_path=driver_path) if driver_path else Service()
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(45)
    driver.set_script_timeout(30)
    wait = WebDriverWait(driver, 30)
    visits = 0
    palette_colors: dict[tuple[str, str, str], tuple[str, str]] = {}
    with serve_directory(site_dir) as base_url:
        try:
            for width, height, mobile, viewport_name in VIEWPORTS:
                driver.execute_cdp_cmd(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": width,
                        "height": height,
                        "deviceScaleFactor": 1,
                        "mobile": mobile,
                    },
                )
                for scheme, palette_name in PALETTES:
                    driver.execute_cdp_cmd(
                        "Emulation.setEmulatedMedia",
                        {
                            "media": "screen",
                            "features": [
                                {
                                    "name": "prefers-color-scheme",
                                    "value": palette_name,
                                }
                            ],
                        },
                    )
                    for page in selected:
                        route = page_route(page, site_dir)
                        visits += 1
                        try:
                            driver.get_log("browser")
                            driver.get(base_url + route)
                            wait.until(
                                lambda current: current.execute_script(
                                    "return !!document.querySelector("
                                    "'article.md-content__inner')"
                                )
                            )
                            driver.execute_script(
                                "document.body.setAttribute("
                                "'data-md-color-scheme', arguments[0])",
                                scheme,
                            )
                            ready = driver.execute_async_script(
                                """
                                const done = arguments[0];
                                const delay = new Promise((resolve) =>
                                  setTimeout(resolve, 8000));
                                const fonts = document.fonts
                                  ? document.fonts.ready
                                  : Promise.resolve();
                                const images = Promise.allSettled(
                                  [...document.images].map((image) =>
                                    image.complete
                                      ? Promise.resolve()
                                      : new Promise((resolve) => {
                                          image.addEventListener(
                                            "load", resolve, {once: true}
                                          );
                                          image.addEventListener(
                                            "error", resolve, {once: true}
                                          );
                                        })
                                  )
                                );
                                const math = window.MathJax
                                  && window.MathJax.startup
                                  ? window.MathJax.startup.promise
                                      .then(() => window.MathJax.typesetPromise())
                                  : Promise.resolve();
                                Promise.race([
                                  Promise.allSettled([fonts, images, math]),
                                  delay
                                ]).then(() =>
                                  requestAnimationFrame(() =>
                                    requestAnimationFrame(() => done(true))));
                                """
                            )
                            if ready is not True:
                                raise RuntimeError("page did not settle")
                            stats = driver.execute_script(
                                """
                                const article = document.querySelector(
                                  "article.md-content__inner"
                                );
                                const problemDetails = [
                                  ...document.querySelectorAll(
                                    "details.problem"
                                  )
                                ];
                                const cpp = [
                                  ...document.querySelectorAll(
                                    ".highlight.language-cpp"
                                  )
                                ];
                                const tokens = cpp.reduce(
                                  (total, block) =>
                                    total + block.querySelectorAll(
                                      "code span[class]"
                                    ).length,
                                  0
                                );
                                const badPreOverflow = [
                                  ...document.querySelectorAll(
                                    "article.md-content__inner pre"
                                  )
                                ].filter((element) => {
                                  if (
                                    element.scrollWidth
                                    <= element.clientWidth + 1
                                  ) return false;
                                  const value = getComputedStyle(
                                    element
                                  ).overflowX;
                                  return value !== "auto"
                                    && value !== "scroll";
                                }).length;
                                const mathWrappers = [
                                  ...document.querySelectorAll(".arithmatex")
                                ];
                                const mathRendered = mathWrappers.filter(
                                  (wrapper) =>
                                    wrapper.querySelector("mjx-container")
                                ).length;
                                const code = cpp.length
                                  ? cpp[0].querySelector("code")
                                  : null;
                                const style = getComputedStyle(document.body);
                                return {
                                  article: !!article,
                                  textLength: article
                                    ? article.innerText.trim().length
                                    : 0,
                                  scheme: document.body.getAttribute(
                                    "data-md-color-scheme"
                                  ),
                                  background: style.backgroundColor,
                                  foreground: style.color,
                                  problemDetails: problemDetails.length,
                                  openProblems: problemDetails.filter(
                                    (item) => item.open
                                  ).length,
                                  cpp: cpp.length,
                                  tokens,
                                  codeFont: code
                                    ? getComputedStyle(code).fontFamily
                                    : "",
                                  badPreOverflow,
                                  documentOverflow:
                                    document.documentElement.scrollWidth
                                    > document.documentElement.clientWidth + 1,
                                  brokenImages: [
                                    ...document.querySelectorAll(
                                      "article.md-content__inner img"
                                    )
                                  ].filter((image) =>
                                    image.complete
                                    && image.naturalWidth === 0
                                  ).length,
                                  mathWrappers: mathWrappers.length,
                                  mathRendered,
                                  mathErrors: document.querySelectorAll(
                                    "mjx-merror, .MathJax_Error"
                                  ).length
                                };
                                """
                            )
                            label = f"{route} [{viewport_name}/{palette_name}]"
                            if not stats["article"] or stats["textLength"] == 0:
                                errors.append(f"{label}: article is empty")
                            if stats["scheme"] != scheme:
                                errors.append(
                                    f"{label}: expected palette {scheme}, "
                                    f"found {stats['scheme']}"
                                )
                            if stats["openProblems"]:
                                errors.append(
                                    f"{label}: problem details are open by default"
                                )
                            if stats["cpp"] and stats["tokens"] < stats["cpp"]:
                                errors.append(
                                    f"{label}: C++ syntax token markup is missing"
                                )
                            if stats["cpp"] and not re.search(
                                r"PingFang|Noto Sans(?: Mono)? CJK|"
                                r"Noto Sans SC|Microsoft YaHei",
                                stats["codeFont"],
                                re.IGNORECASE,
                            ):
                                errors.append(
                                    f"{label}: CJK fallback is absent from "
                                    f"the computed code font {stats['codeFont']!r}"
                                )
                            if stats["badPreOverflow"]:
                                errors.append(
                                    f"{label}: {stats['badPreOverflow']} code "
                                    "blocks overflow without scrolling"
                                )
                            if stats["documentOverflow"]:
                                errors.append(
                                    f"{label}: document has horizontal overflow"
                                )
                            if stats["brokenImages"]:
                                errors.append(
                                    f"{label}: {stats['brokenImages']} images "
                                    "failed to render"
                                )
                            if stats["mathRendered"] != stats["mathWrappers"]:
                                errors.append(
                                    f"{label}: rendered "
                                    f"{stats['mathRendered']} of "
                                    f"{stats['mathWrappers']} formulas"
                                )
                            if stats["mathErrors"]:
                                errors.append(
                                    f"{label}: MathJax reported "
                                    f"{stats['mathErrors']} errors"
                                )
                            palette_colors[
                                (route, viewport_name, palette_name)
                            ] = (stats["background"], stats["foreground"])
                            logs = driver.get_log("browser")
                            severe = [
                                item["message"]
                                for item in logs
                                if item["level"] == "SEVERE"
                            ]
                            if severe:
                                errors.append(
                                    f"{label}: browser console errors: "
                                    + "; ".join(severe)
                                )
                            if screenshots_dir:
                                slug = (
                                    route.strip("/").replace("/", "-")
                                    or "home"
                                )
                                screenshot = screenshots_dir / (
                                    f"{slug}-{viewport_name}-{palette_name}.png"
                                )
                                driver.save_screenshot(str(screenshot))
                        except Exception as exc:
                            errors.append(
                                f"{route} [{viewport_name}/{palette_name}]: "
                                f"browser audit failed: "
                                f"{type(exc).__name__}: {exc}"
                            )
            for page in selected:
                route = page_route(page, site_dir)
                for _, _, _, viewport_name in VIEWPORTS:
                    light = palette_colors.get((route, viewport_name, "light"))
                    dark = palette_colors.get((route, viewport_name, "dark"))
                    if light and dark and light == dark:
                        errors.append(
                            f"{route} [{viewport_name}]: light and dark "
                            "palette colors are identical"
                        )
            driver.execute_cdp_cmd("Emulation.clearDeviceMetricsOverride", {})
        finally:
            driver.quit()
    return visits


def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--site-dir",
        type=Path,
        help="validate an existing MkDocs output directory",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="audit a bounded representative set in Chrome",
    )
    parser.add_argument(
        "--chrome-binary",
        help="explicit Chrome or Chromium executable",
    )
    parser.add_argument(
        "--screenshots-dir",
        type=Path,
        help="write representative viewport screenshots",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    errors: list[str] = []
    validate_runtime_assets(errors)
    markdown_paths = source_paths()
    cpp_blocks = sum(scan_markdown(path, errors) for path in markdown_paths)
    print(
        f"source: {len(markdown_paths)} Markdown files, "
        f"{cpp_blocks} C++ fences"
    )
    pages: dict[Path, PageFacts] = {}
    site_dir: Optional[Path] = None
    if args.site_dir:
        site_dir = args.site_dir
        if not site_dir.is_absolute():
            site_dir = ROOT / site_dir
        site_dir = site_dir.resolve()
        if not site_dir.is_dir():
            errors.append(f"generated site directory is missing: {site_dir}")
        else:
            pages = parse_generated_pages(site_dir, errors)
            validate_internal_resources(site_dir, pages, errors)
            validate_expected_routes(site_dir, pages, errors)
            print(
                f"generated: {len(pages)} HTML files, "
                f"{sum(item.cpp_blocks for item in pages.values())} "
                "highlighted C++ blocks"
            )
    if args.browser:
        if site_dir is None:
            site_dir = (ROOT / "site").resolve()
        if not site_dir.is_dir():
            errors.append(f"browser site directory is missing: {site_dir}")
        else:
            if not pages:
                pages = parse_generated_pages(site_dir, errors)
            screenshots = args.screenshots_dir
            if screenshots and not screenshots.is_absolute():
                screenshots = ROOT / screenshots
            visits = browser_audit(
                site_dir,
                pages,
                args.chrome_binary,
                screenshots,
                errors,
            )
            print(
                f"browser: {visits} representative viewport/palette visits"
            )
    if errors:
        print("\nRendering check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Rendering check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
