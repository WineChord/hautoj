#!/usr/bin/env python3
"""Reader-facing normalization for HAUTOJ statement fragments."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass


PRIVATE_URL_RE = re.compile(
    r"https?://(?:localhost|127(?:\.\d+){3}|10(?:\.\d+){3}|"
    r"192\.168(?:\.\d+){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d+){2})"
    r"[^\s<>()]*",
    re.IGNORECASE,
)
IMAGE_PLACEHOLDER_RE = re.compile(
    r"\[\s*图片\s*[：:][^\]\n]*(?:\]|$)",
    re.IGNORECASE | re.MULTILINE,
)
C_COMMENT_RE = re.compile(r"/\*\s*(.*?)\s*\*/", re.DOTALL)
CODE_START_RE = re.compile(
    r"^\s*(?:#\s*(?:include|define|pragma)|using\s+namespace|"
    r"(?:int|long\s+long|double|char|void|bool)\s+main\s*\(|"
    r"(?:for|while|if|switch)\s*\(|scanf\s*\(|printf\s*\(|"
    r"cin\s*>>|cout\s*<<|[{}])",
    re.IGNORECASE,
)
STRONG_CODE_START_RE = re.compile(
    r"^\s*(?:#\s*(?:include|define|pragma)|using\s+namespace|"
    r"(?:int|signed|void)\s+main\s*\(|"
    r"while\s*\(\s*(?:scanf|std::cin|cin)|"
    r"//\s*(?:C\+\+|C\s*语言|这里)|"
    r"(?:const\s+)?(?:int|long\s+long|double|char|bool)\s+"
    r"[A-Za-z_]\w*(?:\s*\[[^\]]+\])?\s*(?:=|;|,))",
    re.IGNORECASE,
)
CPP_SIGNAL_RE = re.compile(
    r"#\s*include|using\s+namespace|(?:int|void)\s+main\s*\(|"
    r"\bscanf\s*\(|\bprintf\s*\(|\bcin\s*>>|\bcout\s*<<|"
    r"(?:for|while|if)\s*\([^)]*\)\s*\{",
    re.IGNORECASE,
)
ASCII_ART_RE = re.compile(r"^[\s+|/\\_*#.=<>-]+$")

ATOM = (
    r"(?:-?\d+(?:\.\d+)?(?:\s*\^\s*-?\d+)?|"
    r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)?"
    r"(?:\[[A-Za-z0-9]+\])?)"
)
PRODUCT = rf"{ATOM}(?:\s*(?:\*|×|·)\s*{ATOM})*"
ITEM_LIST = rf"{PRODUCT}(?:\s*[,，]\s*{PRODUCT})*"
MATH_CHAIN_RE = re.compile(
    rf"(?<![\w`$]){ITEM_LIST}"
    rf"(?:\s*(?:<=|>=|≤|≥|<|>|=)\s*{ITEM_LIST})+"
    rf"(?![\w`$])"
)
POWER_RE = re.compile(r"(?<![\w`$])\d+\s*\^\s*-?\d+(?![\w`$])")


@dataclass(frozen=True)
class ReaderBlock:
    """A semantic fragment ready for Markdown or PDF rendering."""

    kind: str
    text: str


def _comment_to_note(match: re.Match[str]) -> str:
    note = re.sub(r"\s+", " ", match.group(1)).strip()
    if not note:
        return ""
    if len(note) <= 180 and not re.search(r"[{};#]|\breturn\b", note):
        return f"题面备注：{note}"
    return note


def clean_reader_text(value: object) -> str:
    """Remove source transport artifacts without discarding reader content."""

    text = html.unescape(str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ").replace("\u2009", " ")
    text = text.replace("\u202f", " ").replace("\u3000", " ")
    text = text.replace("\u200b", "").replace("\u2060", "").replace("\ufeff", "")
    text = "".join(
        character
        for character in text
        if character in "\n\t" or ord(character) >= 32
    )
    text = PRIVATE_URL_RE.sub("（原题中的内网资源地址已省略）", text)
    text = IMAGE_PLACEHOLDER_RE.sub("", text)
    text = C_COMMENT_RE.sub(_comment_to_note, text)
    for _ in range(3):
        text = re.sub(r"(?<=[0-9A-Za-z\]])\?(?=[≤≥<>,，])", "", text)
        text = re.sub(r"(?<=[≤≥<>,，])\?(?=[0-9A-Za-z\[])", "", text)
    lines = [re.sub(r"[ \t]+$", "", line) for line in text.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n[ \t]+\n", "\n\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def compact_reader_text(value: object) -> str:
    return re.sub(r"\s+", " ", clean_reader_text(value)).strip()


def normalize_legacy_markup(value: object) -> str:
    """Convert leaked Markdown/TeX source into readable narrative text."""

    text = clean_reader_text(value)
    text = re.sub(r"(?m)^\s*#{1,6}\s+", "", text)
    text = text.replace(r"\displaystyle", "")
    text = text.replace(r"\left", "").replace(r"\right", "")
    for _ in range(4):
        text = re.sub(
            r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}",
            r"(\1)/(\2)",
            text,
        )
    replacements = {
        r"\sum": "∑",
        r"\times": "×",
        r"\cdot": "×",
        r"\leq": "≤",
        r"\le": "≤",
        r"\geq": "≥",
        r"\ge": "≥",
        r"\neq": "≠",
        r"\ne": "≠",
        r"\lfloor": "⌊",
        r"\rfloor": "⌋",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    def grouped_operator(match: re.Match[str]) -> str:
        operator, inner = match.group(1), match.group(2).strip()
        if re.fullmatch(r"-?[A-Za-z0-9]+", inner):
            return operator + inner
        return f"{operator}({inner})"

    text = re.sub(r"(\^|_)\s*\{([^{}]+)\}", grouped_operator, text)
    text = re.sub(r"(?<!\\)\${1,2}", "", text)
    text = re.sub(r"~~(.*?)~~", r"\1", text, flags=re.DOTALL)
    return text.strip()


def normalize_math_text(value: object) -> str:
    """Use readable Unicode operators for non-code PDF/plain-text output."""

    text = str(value or "")
    text = re.sub(r"(?<![<>=!])<=", " ≤ ", text)
    text = re.sub(r"(?<![<>=!])>=", " ≥ ", text)
    text = re.sub(r"(?<![<>=!])!=", " ≠ ", text)
    text = re.sub(r"(?<![<])<(?![<=])", " < ", text)
    text = re.sub(r"(?<![>])>(?![>=])", " > ", text)
    text = re.sub(
        r"(?<=[0-9A-Za-z\]\)])\s*(?:\*|·)\s*(?=[0-9A-Za-z\[\(])",
        " × ",
        text,
    )
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _looks_cpp(text: str) -> bool:
    if CPP_SIGNAL_RE.search(text):
        return True
    lines = [line for line in text.splitlines() if line.strip()]
    signals = sum(bool(CODE_START_RE.search(line)) for line in lines)
    punctuation = sum(
        bool(re.search(r"[;{}]|//|/\*", line))
        for line in lines
    )
    return len(lines) >= 3 and signals >= 2 and punctuation >= 2


def _looks_ascii_art(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    matching = sum(bool(ASCII_ART_RE.fullmatch(line)) for line in lines)
    return matching >= max(3, len(lines) - 1)


def _looks_math_line(text: str) -> bool:
    line = text.strip().strip("（）()；;。")
    if not line or len(line) > 140:
        return False
    if re.search(r"[\u4e00-\u9fff]", line):
        return False
    return bool(MATH_CHAIN_RE.search(line))


def reader_blocks(value: object) -> list[ReaderBlock]:
    """Split a field into prose, formula, C/C++, and literal-text blocks."""

    text = normalize_legacy_markup(value)
    if not text:
        return []

    # Some legacy statements place an explanatory paragraph before source code
    # and then insert a blank line after every source line.  Detect the code
    # span before paragraph splitting so those blank lines do not turn the
    # program into a sequence of ordinary prose paragraphs.
    lines = text.splitlines()
    code_start = next(
        (
            index
            for index, line in enumerate(lines)
            if STRONG_CODE_START_RE.search(line)
            and _looks_cpp("\n".join(lines[index:]))
        ),
        None,
    )
    if code_start is not None:
        before = "\n".join(lines[:code_start]).strip()
        code = "\n".join(lines[code_start:]).strip()
        blocks = reader_blocks(before) if before else []
        blocks.append(ReaderBlock("cpp", re.sub(r"\n{3,}", "\n\n", code)))
        return blocks

    blocks: list[ReaderBlock] = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        lines = paragraph.splitlines()
        code_start = next(
            (
                index
                for index, line in enumerate(lines)
                if CODE_START_RE.search(line)
            ),
            None,
        )
        if (
            code_start is not None
            and code_start > 0
            and _looks_cpp("\n".join(lines[code_start:]))
        ):
            before = "\n".join(lines[:code_start]).strip()
            if before:
                blocks.append(ReaderBlock("prose", before))
            blocks.append(
                ReaderBlock("cpp", "\n".join(lines[code_start:]).strip())
            )
            continue
        if _looks_cpp(paragraph):
            blocks.append(ReaderBlock("cpp", paragraph))
        elif _looks_ascii_art(paragraph):
            blocks.append(ReaderBlock("text", paragraph))
        elif all(_looks_math_line(line) for line in lines if line.strip()):
            blocks.append(ReaderBlock("math", paragraph))
        else:
            blocks.append(ReaderBlock("prose", paragraph))
    return blocks


def latex_expression(value: object) -> str:
    text = str(value or "").strip().strip("。；;")
    text = re.sub(
        r"([A-Za-z][A-Za-z0-9]*)\[([A-Za-z0-9]+)\]",
        r"\1_{\2}",
        text,
    )
    text = text.replace("<=", r" \le ").replace("≤", r" \le ")
    text = text.replace(">=", r" \ge ").replace("≥", r" \ge ")
    text = text.replace("!=", r" \ne ")
    text = re.sub(r"(?<![<])<(?![<=])", " < ", text)
    text = re.sub(r"(?<![>])>(?![>=])", " > ", text)
    text = re.sub(r"\s*(?:\*|×|·)\s*", r" \\times ", text)
    text = text.replace("，", ",")
    text = text.replace("%", r"\%")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _escape_markdown_text(text: str) -> str:
    text = html.escape(text, quote=False)
    text = text.replace("\\", "\\\\")
    # These characters are enabled by the site's pymdownx extensions.  Escape
    # them in statement prose so wildcards, emoticons and literal operators are
    # not silently converted to emphasis, subscript or superscript.
    for character in ("`", "*", "_", "[", "]", "~", "^"):
        text = text.replace(character, "\\" + character)
    return text


def markdown_inline_math(value: object) -> str:
    """Render conservative comparison/power fragments as inline LaTeX."""

    text = str(value or "")
    protected: list[str] = []

    def protect(rendered: str) -> str:
        token = f"\ue000{len(protected)}\ue001"
        protected.append(rendered)
        return token

    text = re.sub(
        r"`([^`\n]+)`",
        lambda match: protect(
            f"`{html.escape(match.group(1), quote=False)}`"
        ),
        text,
    )
    text = MATH_CHAIN_RE.sub(
        lambda match: protect(f"${latex_expression(match.group(0))}$"),
        text,
    )
    text = POWER_RE.sub(
        lambda match: protect(f"${latex_expression(match.group(0))}$"),
        text,
    )
    text = _escape_markdown_text(text)
    for index, rendered in enumerate(protected):
        text = text.replace(f"\ue000{index}\ue001", rendered)
    return text


def _fence(language: str, text: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    marker = "`" * max(3, longest + 1)
    return f"{marker}{language}\n{text.rstrip()}\n{marker}"


def markdown_blocks(value: object) -> str:
    """Return polished Markdown with math and literal blocks preserved."""

    rendered: list[str] = []
    for block in reader_blocks(value):
        if block.kind == "cpp":
            rendered.append(_fence("cpp", block.text))
        elif block.kind == "text":
            rendered.append(_fence("text", block.text))
        elif block.kind == "math":
            lines = [
                latex_expression(line)
                for line in block.text.splitlines()
                if line.strip()
            ]
            if len(lines) == 1:
                rendered.append(f"$$\n{lines[0]}\n$$")
            else:
                body = " \\\\\n".join(lines)
                rendered.append(
                    "$$\n\\begin{aligned}\n"
                    f"{body}\n"
                    "\\end{aligned}\n$$"
                )
        else:
            lines = [
                markdown_inline_math(line)
                for line in block.text.splitlines()
            ]
            rendered.append("\n".join(lines))
    return "\n\n".join(rendered)
