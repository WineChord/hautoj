#!/usr/bin/env python3
"""Check every published C++ solution for contest style and GNU C++17 syntax."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from site_checks import (
    EXPECTED_PROBLEMS,
    ROOT,
    CodeBlock,
    Reporter,
    code_blocks,
    markdown_files,
    relative,
)


CPP_LANGUAGES = {"cpp", "c++", "cc", "cxx"}
CONTROL_WITHOUT_SPACE = re.compile(r"\b(?:if|for|while|switch|catch)\(")
MULTI_OPERATOR = re.compile(
    r"(?<!operator)(?P<operator><<=|>>=|==|!=|<=|>=|&&|\|\||"
    r"\+=|-=|\*=|/=|%=|&=|\|=|\^=|=(?!=))"
)
COMPRESSED_ARITHMETIC = re.compile(
    r"(?<=[A-Za-z0-9_\]\)])(?P<operator>[+\-*/%])(?=[A-Za-z0-9_([])"
)
COMPRESSED_RELATION = re.compile(
    r"(?<=[A-Za-z0-9_\]\)])(?P<operator>[<>])(?=[A-Za-z0-9_(&])"
)
TEMPLATE_NAMES = {
    "array",
    "bitset",
    "deque",
    "function",
    "greater",
    "less",
    "map",
    "multimap",
    "multiset",
    "optional",
    "pair",
    "priority_queue",
    "queue",
    "set",
    "span",
    "stack",
    "tuple",
    "unordered_map",
    "unordered_set",
    "variant",
    "vector",
}


def mask_literals_and_comments(lines: list[str]) -> list[str]:
    """Preserve operator columns while hiding strings, chars, and comments."""

    masked: list[str] = []
    in_block_comment = False
    for line in lines:
        output = list(line)
        index = 0
        quote = ""
        while index < len(line):
            if in_block_comment:
                output[index] = " "
                if line.startswith("*/", index):
                    output[index : index + 2] = [" ", " "]
                    in_block_comment = False
                    index += 2
                else:
                    index += 1
                continue
            if quote:
                output[index] = " "
                if line[index] == "\\" and index + 1 < len(line):
                    output[index + 1] = " "
                    index += 2
                elif line[index] == quote:
                    quote = ""
                    index += 1
                else:
                    index += 1
                continue
            if line.startswith("//", index):
                output[index:] = [" "] * (len(line) - index)
                break
            if line.startswith("/*", index):
                output[index : index + 2] = [" ", " "]
                in_block_comment = True
                index += 2
                continue
            if line[index] in {'"', "'"}:
                quote = line[index]
                output[index] = " "
            index += 1
        masked.append("".join(output))
    return masked


def previous_word(line: str, index: int) -> str:
    match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*$", line[:index])
    return match.group(1) if match else ""


def in_control_expression(line: str, index: int) -> bool:
    for match in re.finditer(r"\b(?:if|for|while|switch)\s*\(", line):
        start = match.end()
        depth = 1
        cursor = start
        while cursor < len(line) and depth:
            if line[cursor] == "(":
                depth += 1
            elif line[cursor] == ")":
                depth -= 1
            cursor += 1
        if start <= index < cursor:
            return True
    return False


def is_template_angle(line: str, index: int, operator: str) -> bool:
    if operator == "<":
        word = previous_word(line, index)
        return word in TEMPLATE_NAMES or word.endswith("_cast")
    depth = 0
    for cursor in range(index - 1, -1, -1):
        if line[cursor] == ">":
            depth += 1
        elif line[cursor] == "<":
            if depth:
                depth -= 1
                continue
            word = previous_word(line, cursor)
            body = line[cursor + 1 : index]
            type_like = bool(re.fullmatch(r"[A-Za-z0-9_:<>, *&]+", body))
            return type_like and (word in TEMPLATE_NAMES or word.endswith("_cast"))
    return False


def check_spacing(
    block: CodeBlock, reporter: Reporter, *, enforce_line_width: bool = True
) -> None:
    lines = block.source.rstrip("\n").split("\n")
    masked = mask_literals_and_comments(lines)
    label = relative(block.path)
    for offset, (line, visible) in enumerate(zip(lines, masked), 1):
        number = block.line + offset
        location = f"{label}:{number}"
        if not line.strip():
            reporter.add(location, "C++ 代码块不能包含空行")
        if "\t" in line:
            reporter.add(location, "C++ 代码不得使用 Tab 缩进")
        if line.rstrip() != line:
            reporter.add(location, "C++ 代码行末不得保留空白")
        if enforce_line_width and len(line) > 92:
            reporter.add(location, f"C++ 行长 {len(line)} 超过 92 字符")
        if CONTROL_WITHOUT_SPACE.search(visible):
            reporter.add(location, "控制关键字与左括号之间必须留空格")
        if visible.lstrip().startswith("#"):
            continue
        for match in MULTI_OPERATOR.finditer(visible):
            start, end = match.span("operator")
            before = visible[start - 1] if start else ""
            after = visible[end] if end < len(visible) else ""
            if before and not before.isspace():
                reporter.add(
                    location,
                    f"运算符 {match.group('operator')} 左侧缺少常规空格",
                )
            if after and not after.isspace():
                reporter.add(
                    location,
                    f"运算符 {match.group('operator')} 右侧缺少常规空格",
                )
        for match in re.finditer(",", visible):
            next_index = match.end()
            if next_index < len(visible) and not visible[next_index].isspace():
                reporter.add(location, "逗号后必须留空格")
        for match in COMPRESSED_ARITHMETIC.finditer(visible):
            operator = match.group("operator")
            start = match.start("operator")
            if (
                operator in {"+", "-"}
                and start >= 2
                and visible[start - 1] in {"e", "E"}
                and (visible[start - 2].isdigit() or visible[start - 2] == ".")
            ):
                continue
            if operator in {"+", "-"} and (
                visible[max(0, start - 1) : start + 2] in {"++", "--"}
                or visible[start : start + 2] in {"++", "->", "--"}
            ):
                continue
            reporter.add(location, f"二元运算符 {operator} 两侧必须留空格")
        for match in COMPRESSED_RELATION.finditer(visible):
            start = match.start("operator")
            if is_template_angle(visible, start, match.group("operator")):
                continue
            if in_control_expression(visible, start):
                reporter.add(
                    location,
                    f"比较运算符 {match.group('operator')} 两侧必须留空格",
                )
        if re.search(r"\b(?:cin|cout|cerr|clog)\b", visible):
            for match in re.finditer(r"<<|>>", visible):
                start, end = match.span()
                before = visible[start - 1] if start else ""
                after = visible[end] if end < len(visible) else ""
                if (before and not before.isspace()) or (
                    after and not after.isspace()
                ):
                    reporter.add(
                        location,
                        f"流运算符 {match.group()} 两侧必须留空格",
                    )
        for control in re.finditer(r"\bfor\s*\(", visible):
            start = control.end()
            depth = 1
            cursor = start
            while cursor < len(visible) and depth:
                if visible[cursor] == "(":
                    depth += 1
                elif visible[cursor] == ")":
                    depth -= 1
                cursor += 1
            header = visible[start : cursor - 1] if depth == 0 else visible[start:]
            for semicolon in re.finditer(";", header):
                next_index = semicolon.end()
                if (
                    next_index < len(header)
                    and header[next_index] not in {";", " "}
                    and not header[next_index].isspace()
                ):
                    reporter.add(location, "for 头部的分号后必须留空格")


def compiler_name() -> str | None:
    configured = os.environ.get("CXX")
    if configured:
        return configured
    return next(
        (candidate for candidate in ("g++", "clang++", "c++") if shutil.which(candidate)),
        None,
    )


def compile_block(compiler: str, block: CodeBlock) -> tuple[CodeBlock, str]:
    result = subprocess.run(
        [compiler, "-std=gnu++17", "-fsyntax-only", "-x", "c++", "-"],
        input=block.source,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode == 0:
        return block, ""
    raw = result.stderr.strip() or result.stdout.strip()
    diagnostic_lines = [
        line
        for line in raw.splitlines()
        if re.search(r"\b(?:fatal error|error|warning|note):", line)
    ]
    diagnostic = "\n".join(diagnostic_lines[:12]) or "编译失败；源代码行未回显"
    diagnostic = re.sub(r"/Users/[^/\s]+/", "/Users/<redacted>/", diagnostic)
    diagnostic = re.sub(r"/var/folders/\S+", "<temporary-path>", diagnostic)
    return block, diagnostic


def main() -> None:
    reporter = Reporter()
    include_dir = ROOT / "includes" / "problems"
    include_files = sorted(include_dir.glob("haut-*.md"))
    canonical: list[CodeBlock] = []
    all_blocks: list[CodeBlock] = []

    for path in markdown_files(include_downloads=True):
        cpp = [
            block
            for block in code_blocks(path, reporter)
            if block.language in CPP_LANGUAGES
        ]
        all_blocks.extend(cpp)
        if path.parent != include_dir:
            continue
        if len(cpp) != 1:
            reporter.add(path, f"题目片段必须恰有 1 个 C++ 代码块，实际 {len(cpp)}")
            continue
        if cpp[0].skipped:
            reporter.add(path, "完整题解代码不得使用 compile:skip")
        canonical.append(cpp[0])

    reporter.require(
        len(include_files) == EXPECTED_PROBLEMS,
        "includes/problems",
        f"题目片段应为 {EXPECTED_PROBLEMS} 个，实际 {len(include_files)}",
    )
    reporter.require(
        len(canonical) == EXPECTED_PROBLEMS,
        "includes/problems",
        f"可编译题解应为 {EXPECTED_PROBLEMS} 个，实际 {len(canonical)}",
    )

    for block in all_blocks:
        check_spacing(block, reporter)

    compile_targets = list(canonical)
    canonical_paths = {block.path.resolve() for block in canonical}
    for block in all_blocks:
        if block.path.resolve() in canonical_paths:
            continue
        if "downloads" in block.path.parts:
            continue
        if block.skipped:
            continue
        if re.search(r"\b(?:int|signed)\s+main\s*\(", block.source):
            compile_targets.append(block)

    compiler = compiler_name()
    if compiler is None:
        reporter.add("CXX", "未找到 g++、clang++ 或 c++ 编译器")
        reporter.finish()
        return

    workers = min(8, max(1, os.cpu_count() or 1))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(compile_block, compiler, block): block
            for block in compile_targets
        }
        for future in as_completed(futures):
            block = futures[future]
            try:
                _, diagnostic = future.result()
            except subprocess.TimeoutExpired:
                reporter.add(
                    f"{relative(block.path)}:{block.line}",
                    "C++17 语法检查超过 30 秒",
                )
                continue
            except OSError as error:
                reporter.add("CXX", f"无法启动编译器：{error}")
                continue
            if diagnostic:
                reporter.add(
                    f"{relative(block.path)}:{block.line}",
                    f"GNU C++17 编译失败\n{diagnostic}",
                )

    reporter.finish()
    print(
        "C++ 检查通过："
        f"{len(canonical)} 个唯一题解、{len(compile_targets)} 个完整程序通过 "
        f"GNU C++17；{len(all_blocks)} 个代码块通过空行、Tab、92 字符与竞赛空格检查；"
        f"编译器 {compiler}"
    )


if __name__ == "__main__":
    main()
