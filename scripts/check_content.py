#!/usr/bin/env python3
"""Validate HAUTOJ corpus completeness, page architecture, and public safety."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

from site_checks import (
    EXPECTED_CONTESTS,
    EXPECTED_OCCURRENCES,
    EXPECTED_PROBLEMS,
    EXPECTED_PUBLIC_CONTESTS,
    EXPECTED_TOPICS,
    ROOT,
    Reporter,
    code_blocks,
    iter_text_files,
    load_json,
    mask_fenced_code,
    read_text,
    relative,
    snippet_targets,
)


CORPUS_PATH = ROOT / "data" / "corpus.json"
VALIDATION_PATH = ROOT / "data" / "validation_by_pid.json"
IMAGE_MANIFEST_PATH = ROOT / "data" / "image_manifest.json"
PROBLEM_URL = re.compile(
    r"https://acm\.haut\.edu\.cn/problem\.php\?id=(?P<pid>\d+)"
)
CONTEST_URL = re.compile(
    r"https://acm\.haut\.edu\.cn/contest\.php\?cid=(?P<cid>\d+)"
)
PROBLEM_SNIPPET = re.compile(r"^includes/problems/haut-(?P<pid>\d+)\.md$")
SECRET_PATTERNS = {
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "authorization header": re.compile(
        r"(?i)\bauthorization\s*:\s*(?:bearer|basic)\s+\S+"
    ),
    "macOS home path": re.compile(r"/Users/[^/\s]+/"),
    "temporary macOS path": re.compile(r"/var/folders/[^\s\"']+"),
    "Windows home path": re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+\\"),
}
PRIVATE_TRACES = {
    "私人工作流叙述": re.compile(
        r"(?i)\bthe user (?:asked|wanted|told)|according to (?:the|this) prompt|"
        r"conversation history|private instruction|chain[- ]of[- ]thought|"
        r"用户(?:要求|让我|指示)|根据用户(?:要求|指令)|私有指令|内部工作流"
    ),
    "私人定向标题": re.compile(
        r"(?:给|为).{1,8}的大学与.{0,24}(?:准备|备忘录)"
    ),
    "不当结果承诺": re.compile(
        r"(?:保证|确保|包).{0,8}(?:录取|入选|进入.{0,4}(?:校队|团队))"
    ),
}
PLACEHOLDER = re.compile(
    r"(?i)\b(?:TODO|TBD|FIXME|lorem ipsum)\b|待补充|敬请期待|此处填写"
)
DISALLOWED_INVISIBLE = re.compile("[\u202a-\u202e\u2066-\u2069]")
ALLOWED_SAMPLE_STATUSES = {
    "passed",
    "unavailable_or_non_machine_readable",
    "not_available",
}
REQUIRED_SNIPPET_SECTIONS = {
    "题意": re.compile(r"\*\*(?:题意|题目摘要|题意摘要)\*\*"),
    "思路": re.compile(r"\*\*(?:思路|核心思路|解题思路)\*\*"),
    "复杂度": re.compile(r"\*\*复杂度\*\*"),
}


def integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def problem_map(corpus: dict[str, Any], reporter: Reporter) -> dict[str, dict[str, Any]]:
    raw = corpus.get("problems")
    if not isinstance(raw, dict):
        reporter.add(CORPUS_PATH, "problems 必须是以 PID 为键的对象")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        pid = str(key)
        if not pid.isdigit() or not isinstance(value, dict):
            reporter.add(CORPUS_PATH, f"非法题目记录键：{key!r}")
            continue
        if str(value.get("pid")) != pid:
            reporter.add(CORPUS_PATH, f"PID {pid} 的 pid 字段不一致")
        result[pid] = value
    return result


def expected_validation_line(record: dict[str, Any]) -> str:
    status = record.get("sample_status")
    if status == "passed":
        return (
            "- 核验状态：C++17 编译通过；"
            f"{record.get('sample_passes')} 个可机读公开样例/合法构造校验通过"
        )
    if status == "unavailable_or_non_machine_readable":
        return "- 核验状态：C++17 编译通过；公开样例不可机读，未执行"
    if status == "not_available":
        return "- 核验状态：C++17 编译通过；未提供成对公开样例"
    return ""


def validate_corpus(
    corpus: dict[str, Any], problems: dict[str, dict[str, Any]], reporter: Reporter
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    contests_raw = corpus.get("contests")
    contests = contests_raw if isinstance(contests_raw, list) else []
    if not isinstance(contests_raw, list):
        reporter.add(CORPUS_PATH, "contests 必须是数组")
    reporter.require(
        len(contests) == EXPECTED_CONTESTS,
        CORPUS_PATH,
        f"周赛应为 {EXPECTED_CONTESTS} 场，实际 {len(contests)}",
    )
    reporter.require(
        len(problems) == EXPECTED_PROBLEMS,
        CORPUS_PATH,
        f"唯一题应为 {EXPECTED_PROBLEMS} 道，实际 {len(problems)}",
    )
    declared = {
        "contest_count": EXPECTED_CONTESTS,
        "public_contest_count": EXPECTED_PUBLIC_CONTESTS,
        "problem_occurrence_count": EXPECTED_OCCURRENCES,
        "unique_problem_count": EXPECTED_PROBLEMS,
    }
    for field, expected in declared.items():
        reporter.require(
            corpus.get(field) == expected,
            CORPUS_PATH,
            f"{field} 应为 {expected}，实际 {corpus.get(field)!r}",
        )

    seen_cids: set[str] = set()
    occurrence_pairs: list[tuple[str, str]] = []
    public_count = 0
    for index, contest in enumerate(contests, 1):
        location = f"data/corpus.json:contests[{index}]"
        if not isinstance(contest, dict):
            reporter.add(location, "周赛记录必须是对象")
            continue
        cid = str(contest.get("cid"))
        if not cid.isdigit() or cid in seen_cids:
            reporter.add(location, f"CID 非法或重复：{cid!r}")
        seen_cids.add(cid)
        expected_url = f"https://acm.haut.edu.cn/contest.php?cid={cid}"
        reporter.require(
            contest.get("url") == expected_url,
            location,
            f"周赛链接必须为 {expected_url}",
        )
        status = contest.get("access_status")
        reporter.require(
            status in {"public", "private"},
            location,
            f"access_status 非法：{status!r}",
        )
        listed = contest.get("problems")
        listed_problems = listed if isinstance(listed, list) else []
        if not isinstance(listed, list):
            reporter.add(location, "problems 必须是数组")
        if status == "public":
            public_count += 1
            reporter.require(
                bool(listed_problems), location, "公开周赛必须包含题目"
            )
        else:
            reporter.require(
                not listed_problems, location, "密码场次不得猜测或收录题目"
            )
        seen_letters: set[str] = set()
        for item_index, item in enumerate(listed_problems, 1):
            item_location = f"{location}.problems[{item_index}]"
            if not isinstance(item, dict):
                reporter.add(item_location, "题目位置必须是对象")
                continue
            pid = str(item.get("pid"))
            letter = str(item.get("letter"))
            reporter.require(pid in problems, item_location, f"未知 PID：{pid}")
            reporter.require(
                bool(re.fullmatch(r"[A-Z]", letter)),
                item_location,
                f"题号必须是单个大写字母：{letter!r}",
            )
            if letter in seen_letters:
                reporter.add(item_location, f"题号重复：{letter}")
            seen_letters.add(letter)
            expected_problem_url = f"https://acm.haut.edu.cn/problem.php?id={pid}"
            reporter.require(
                item.get("url") == expected_problem_url,
                item_location,
                f"原题链接必须为 {expected_problem_url}",
            )
            occurrence_pairs.append((cid, pid))

    reporter.require(
        public_count == EXPECTED_PUBLIC_CONTESTS,
        CORPUS_PATH,
        f"公开周赛应为 {EXPECTED_PUBLIC_CONTESTS} 场，实际 {public_count}",
    )
    reporter.require(
        len(occurrence_pairs) == EXPECTED_OCCURRENCES,
        CORPUS_PATH,
        f"题目出现位置应为 {EXPECTED_OCCURRENCES} 个，实际 {len(occurrence_pairs)}",
    )

    occurrence_counter = Counter(occurrence_pairs)
    reverse_counter: Counter[tuple[str, str]] = Counter()
    for pid, problem in problems.items():
        expected_url = f"https://acm.haut.edu.cn/problem.php?id={pid}"
        reporter.require(
            problem.get("url") == expected_url,
            f"data/corpus.json:problems.{pid}",
            f"原题链接必须为 {expected_url}",
        )
        occurrences = problem.get("occurrences")
        if not isinstance(occurrences, list):
            reporter.add(
                f"data/corpus.json:problems.{pid}", "occurrences 必须是数组"
            )
            continue
        for occurrence in occurrences:
            if not isinstance(occurrence, dict):
                reporter.add(
                    f"data/corpus.json:problems.{pid}", "occurrence 必须是对象"
                )
                continue
            cid = str(occurrence.get("cid"))
            expected_url = f"https://acm.haut.edu.cn/contest.php?cid={cid}"
            reporter.require(
                occurrence.get("contest_url") == expected_url,
                f"data/corpus.json:problems.{pid}",
                f"出处链接必须为 {expected_url}",
            )
            reverse_counter[(cid, pid)] += 1
    if occurrence_counter != reverse_counter:
        missing = occurrence_counter - reverse_counter
        extra = reverse_counter - occurrence_counter
        reporter.add(
            CORPUS_PATH,
            "周赛题目表与逐题 occurrences 不一致："
            f"缺少 {sum(missing.values())}，多出 {sum(extra.values())}",
        )
    return contests, occurrence_pairs


def validate_validation_data(
    validation: dict[str, Any],
    problems: dict[str, dict[str, Any]],
    corpus: dict[str, Any],
    reporter: Reporter,
) -> dict[str, dict[str, Any]]:
    records_raw = validation.get("problems")
    records = records_raw if isinstance(records_raw, dict) else {}
    if not isinstance(records_raw, dict):
        reporter.add(VALIDATION_PATH, "problems 必须是以 PID 为键的对象")
    expected_pids = set(problems)
    actual_pids = set(records)
    if actual_pids != expected_pids:
        reporter.add(
            VALIDATION_PATH,
            f"逐题核验 PID 不完整：缺少 {len(expected_pids - actual_pids)}，"
            f"多出 {len(actual_pids - expected_pids)}",
        )
    reporter.require(
        validation.get("verified_through") == corpus.get("verified_through"),
        VALIDATION_PATH,
        "核验日期必须与语料快照一致",
    )
    reporter.require(
        validation.get("problem_count") == EXPECTED_PROBLEMS,
        VALIDATION_PATH,
        f"problem_count 必须为 {EXPECTED_PROBLEMS}",
    )

    compiled = 0
    statuses: Counter[str] = Counter()
    result: dict[str, dict[str, Any]] = {}
    for pid in sorted(expected_pids, key=int):
        record = records.get(pid)
        location = f"data/validation_by_pid.json:problems.{pid}"
        if not isinstance(record, dict):
            reporter.add(location, "核验记录缺失或类型错误")
            continue
        result[pid] = record
        if record.get("compiled") is True:
            compiled += 1
        else:
            reporter.add(location, "compiled 必须明确为 true")
        status = record.get("sample_status")
        if status not in ALLOWED_SAMPLE_STATUSES:
            reporter.add(location, f"sample_status 非法：{status!r}")
            continue
        statuses[status] += 1
        numeric_fields = ("sample_passes", "sample_skips", "paired_samples")
        if any(not integer(record.get(field)) for field in numeric_fields):
            reporter.add(location, "样例计数字段必须是整数")
            continue
        passes = record["sample_passes"]
        skips = record["sample_skips"]
        paired = record["paired_samples"]
        if status == "passed":
            reporter.require(
                passes >= 1 and skips == 0 and paired >= passes,
                location,
                "passed 必须有已执行样例，且不得带跳过计数",
            )
        elif status == "unavailable_or_non_machine_readable":
            reporter.require(
                passes == 0 and skips >= 1 and paired >= skips,
                location,
                "不可机读状态必须无通过数，并有明确跳过数",
            )
        else:
            reporter.require(
                passes == 0 and skips == 0 and paired == 0,
                location,
                "not_available 的样例计数必须全部为 0",
            )
    reporter.require(
        validation.get("compiled") == compiled == EXPECTED_PROBLEMS,
        VALIDATION_PATH,
        f"编译通过数应为 {EXPECTED_PROBLEMS}，实际 {compiled}",
    )
    reporter.require(
        validation.get("sample_pass_problem_count") == statuses["passed"],
        VALIDATION_PATH,
        "sample_pass_problem_count 与逐题状态不一致",
    )
    unavailable = (
        statuses["unavailable_or_non_machine_readable"]
        + statuses["not_available"]
    )
    reporter.require(
        validation.get("sample_unavailable_problem_count") == unavailable,
        VALIDATION_PATH,
        "sample_unavailable_problem_count 与逐题状态不一致",
    )
    return result


def validate_snippet(
    path: Path,
    pid: str,
    problem: dict[str, Any],
    validation: dict[str, Any],
    reporter: Reporter,
) -> None:
    text = read_text(path, reporter)
    nonempty = next((line.strip() for line in text.splitlines() if line.strip()), "")
    reporter.require(
        nonempty.startswith('??? problem "'),
        path,
        '题目详情必须以默认折叠的 ??? problem "..." 开始',
    )
    reporter.require(
        not nonempty.startswith("???+"),
        path,
        "题目详情不得默认展开",
    )
    for name, pattern in REQUIRED_SNIPPET_SECTIONS.items():
        reporter.require(bool(pattern.search(text)), path, f"缺少{name}部分")
    cpp_blocks = [
        block
        for block in code_blocks(path, reporter)
        if block.language in {"cpp", "c++", "cc", "cxx"}
    ]
    reporter.require(
        len(cpp_blocks) == 1,
        path,
        f"必须且只能包含 1 个完整 C++ 实现，实际 {len(cpp_blocks)}",
    )
    expected_url = f"https://acm.haut.edu.cn/problem.php?id={pid}"
    found = PROBLEM_URL.findall(text)
    reporter.require(
        found == [pid],
        path,
        f"必须且只能链接一次官方原题 {expected_url}",
    )
    expected_label = expected_validation_line(validation)
    reporter.require(
        any(
            line.strip().rstrip("。") == expected_label
            for line in text.splitlines()
        ),
        path,
        f"核验标签必须精确反映 structured status：{expected_label}",
    )


def validate_pages(
    contests: list[dict[str, Any]],
    problems: dict[str, dict[str, Any]],
    validations: dict[str, dict[str, Any]],
    reporter: Reporter,
) -> None:
    problem_dir = ROOT / "docs" / "problems"
    snippet_dir = ROOT / "includes" / "problems"
    topic_dir = ROOT / "docs" / "topics"
    contest_dir = ROOT / "docs" / "contests"

    problem_pages = {
        path.stem: path for path in sorted(problem_dir.glob("*.md")) if path.stem.isdigit()
    }
    snippets = {
        match.group("pid"): path
        for path in sorted(snippet_dir.glob("haut-*.md"))
        if (match := re.fullmatch(r"haut-(?P<pid>\d+)", path.stem))
    }
    expected_pids = set(problems)
    for label, actual in (("题目页", set(problem_pages)), ("题目片段", set(snippets))):
        if actual != expected_pids:
            reporter.add(
                problem_dir if label == "题目页" else snippet_dir,
                f"{label}集合不完整：缺少 {len(expected_pids - actual)}，"
                f"多出 {len(actual - expected_pids)}",
            )
    reporter.require(
        len(problem_pages) == EXPECTED_PROBLEMS,
        problem_dir,
        f"题目页应为 {EXPECTED_PROBLEMS} 个，实际 {len(problem_pages)}",
    )
    reporter.require(
        len(snippets) == EXPECTED_PROBLEMS,
        snippet_dir,
        f"题目片段应为 {EXPECTED_PROBLEMS} 个，实际 {len(snippets)}",
    )

    for pid, path in snippets.items():
        if pid in problems and pid in validations:
            validate_snippet(path, pid, problems[pid], validations[pid], reporter)
    for pid, path in problem_pages.items():
        targets = snippet_targets(path, reporter)
        expected_snippet = f"includes/problems/haut-{pid}.md"
        reporter.require(
            [target for _, target in targets] == [expected_snippet],
            path,
            f"完整题解页必须且只能复用自己的规范片段：{expected_snippet}",
        )
        text = read_text(path, reporter)
        required_headings = (
            "题意与输入输出",
            "思路推导",
            "正确性说明",
            "复杂度",
            "易错点",
            "题目摘要与 C++17 参考实现",
            "链接与来源",
        )
        for heading in required_headings:
            reporter.require(
                bool(re.search(rf"(?m)^##\s+{re.escape(heading)}\s*$", text)),
                path,
                f"完整题解页缺少“{heading}”章节",
            )
        expected_url = f"https://acm.haut.edu.cn/problem.php?id={pid}"
        reporter.require(
            text.count(expected_url) >= 1,
            path,
            f"完整题解页缺少官方原题链接 {expected_url}",
        )
        if pid in validations:
            expected_label = expected_validation_line(validations[pid])
            reporter.require(
                expected_label.removeprefix("- 核验状态：") in text,
                path,
                "完整题解页的核验状态与结构化记录不一致",
            )

    expected_contest_paths: dict[Path, dict[str, Any]] = {}
    for contest in contests:
        if not isinstance(contest, dict):
            continue
        cid = str(contest.get("cid"))
        end_time = str(contest.get("end_time", ""))
        year_match = re.match(r"(?P<year>\d{4})", end_time)
        if not year_match:
            reporter.add(
                f"data/corpus.json:contest.{cid}",
                f"无法从 end_time 解析年份：{end_time!r}",
            )
            continue
        expected_contest_paths[
            contest_dir / year_match.group("year") / f"{cid}.md"
        ] = contest
    actual_contest_paths = {
        path
        for path in contest_dir.rglob("*.md")
        if path.stem.isdigit()
    }
    if actual_contest_paths != set(expected_contest_paths):
        reporter.add(
            contest_dir,
            f"场次页集合不完整：缺少 {len(set(expected_contest_paths) - actual_contest_paths)}，"
            f"多出 {len(actual_contest_paths - set(expected_contest_paths))}",
        )
    reporter.require(
        len(actual_contest_paths) == EXPECTED_CONTESTS,
        contest_dir,
        f"场次页应为 {EXPECTED_CONTESTS} 个，实际 {len(actual_contest_paths)}",
    )
    rendered_occurrences = 0
    for path, contest in expected_contest_paths.items():
        if not path.is_file():
            continue
        targets = snippet_targets(path, reporter)
        actual_pids: list[str] = []
        for line, target in targets:
            match = PROBLEM_SNIPPET.fullmatch(target)
            if not match:
                reporter.add(
                    f"{relative(path)}:{line}", f"非法题目片段路径：{target}"
                )
                continue
            actual_pids.append(match.group("pid"))
        listed = contest.get("problems")
        listed = listed if isinstance(listed, list) else []
        expected = [str(item.get("pid")) for item in listed if isinstance(item, dict)]
        reporter.require(
            actual_pids == expected,
            path,
            "场次页题目顺序/重复位置与 corpus 不一致",
        )
        rendered_occurrences += len(actual_pids)
        cid = str(contest.get("cid"))
        official = f"https://acm.haut.edu.cn/contest.php?cid={cid}"
        reporter.require(
            read_text(path, reporter).count(official) == 1,
            path,
            f"场次页必须且只能链接一次官方周赛 {official}",
        )
    reporter.require(
        rendered_occurrences == EXPECTED_OCCURRENCES,
        contest_dir,
        f"场次页应复用 {EXPECTED_OCCURRENCES} 个题目位置，实际 {rendered_occurrences}",
    )

    topic_pages = [
        path
        for path in sorted(topic_dir.glob("*.md"))
        if path.stem.lower() not in {"index", "readme"}
    ]
    reporter.require(
        len(topic_pages) == EXPECTED_TOPICS,
        topic_dir,
        f"专题页应为 {EXPECTED_TOPICS} 个，实际 {len(topic_pages)}",
    )
    topic_counter: Counter[str] = Counter()
    for path in topic_pages:
        targets = snippet_targets(path, reporter)
        reporter.require(bool(targets), path, "专题页不得为空")
        for line, target in targets:
            match = PROBLEM_SNIPPET.fullmatch(target)
            if not match:
                reporter.add(
                    f"{relative(path)}:{line}", f"非法题目片段路径：{target}"
                )
                continue
            topic_counter[match.group("pid")] += 1
    reporter.require(
        set(topic_counter) == expected_pids,
        topic_dir,
        f"专题体系未覆盖全部唯一题：缺少 {len(expected_pids - set(topic_counter))}，"
        f"多出 {len(set(topic_counter) - expected_pids)}",
    )
    duplicates = [pid for pid, count in topic_counter.items() if count != 1]
    reporter.require(
        not duplicates,
        topic_dir,
        f"每道题必须归入恰好一个主专题，重复/异常 PID 数 {len(duplicates)}",
    )


def safe_repo_path(raw: str) -> Path | None:
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    resolved = (ROOT / "docs" / candidate).resolve()
    try:
        resolved.relative_to((ROOT / "docs").resolve())
    except ValueError:
        return None
    return resolved


def validate_image_manifest(
    manifest: dict[str, Any], problems: dict[str, dict[str, Any]], reporter: Reporter
) -> None:
    images_raw = manifest.get("images")
    images = images_raw if isinstance(images_raw, list) else []
    if not isinstance(images_raw, list):
        reporter.add(IMAGE_MANIFEST_PATH, "images 必须是数组")
    summary = manifest.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    limits = manifest.get("limits")
    limits = limits if isinstance(limits, dict) else {}
    max_image_bytes = limits.get("max_image_bytes", 8 * 1024 * 1024)
    max_total_bytes = limits.get("max_total_bytes", 64 * 1024 * 1024)
    if not integer(max_image_bytes) or not integer(max_total_bytes):
        reporter.add(IMAGE_MANIFEST_PATH, "图片大小上限必须是整数")
        max_image_bytes = 8 * 1024 * 1024
        max_total_bytes = 64 * 1024 * 1024

    available_paths: set[Path] = set()
    available_count = 0
    unavailable_count = 0
    total_bytes = 0
    for index, item in enumerate(images, 1):
        location = f"data/image_manifest.json:images[{index}]"
        if not isinstance(item, dict):
            reporter.add(location, "图片记录必须是对象")
            continue
        pid = str(item.get("problem_id"))
        reporter.require(pid in problems, location, f"未知 PID：{pid}")
        status = item.get("status")
        reporter.require(
            status in {"available", "unavailable"},
            location,
            f"status 非法：{status!r}",
        )
        source_url = item.get("source_url")
        if source_url is not None:
            parsed = urlsplit(str(source_url))
            reporter.require(
                parsed.scheme in {"http", "https"} and bool(parsed.netloc),
                location,
                "source_url 只能是用于溯源的 HTTP(S) 地址",
            )
            reporter.require(
                parsed.username is None and parsed.password is None,
                location,
                "source_url 不得内嵌凭据",
            )
        if status == "unavailable":
            unavailable_count += 1
            reporter.require(
                "path" not in item, location, "不可用图片不得声明本地 path"
            )
            reporter.require(
                bool(item.get("reason")), location, "不可用图片必须记录安全失败原因"
            )
            continue
        available_count += 1
        raw_path = item.get("path")
        if not isinstance(raw_path, str):
            reporter.add(location, "可用图片必须提供相对 path")
            continue
        target = safe_repo_path(raw_path)
        if target is None:
            reporter.add(location, "图片 path 越出 docs；原值未回显")
            continue
        reporter.require(
            raw_path.startswith("assets/problem-images/"),
            location,
            "镜像图片必须位于 assets/problem-images/",
        )
        reporter.require(not target.is_symlink(), location, "镜像图片不得是符号链接")
        reporter.require(target.is_file(), location, f"镜像图片不存在：{raw_path}")
        if not target.is_file():
            continue
        available_paths.add(target.resolve())
        data = target.read_bytes()
        size = len(data)
        total_bytes += size
        reporter.require(
            item.get("bytes") == size,
            location,
            f"bytes 与文件大小不一致：{item.get('bytes')!r} != {size}",
        )
        reporter.require(
            size <= max_image_bytes,
            location,
            f"单图 {size} 字节超过上限 {max_image_bytes}",
        )
        digest = hashlib.sha256(data).hexdigest()
        reporter.require(
            item.get("sha256") == digest,
            location,
            "图片 SHA-256 与清单不一致",
        )
        media_type = item.get("media_type")
        signatures = {
            "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/jpeg": data.startswith(b"\xff\xd8\xff"),
            "image/gif": data.startswith((b"GIF87a", b"GIF89a")),
            "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP",
        }
        reporter.require(
            media_type in signatures and signatures.get(media_type, False),
            location,
            f"图片内容与安全媒体类型不一致：{media_type!r}",
        )
    reporter.require(
        total_bytes <= max_total_bytes,
        IMAGE_MANIFEST_PATH,
        f"镜像图片总量 {total_bytes} 超过上限 {max_total_bytes}",
    )
    reporter.require(
        summary.get("references") == len(images),
        IMAGE_MANIFEST_PATH,
        "summary.references 与图片记录数不一致",
    )
    reporter.require(
        summary.get("available") == available_count,
        IMAGE_MANIFEST_PATH,
        "summary.available 与逐项状态不一致",
    )
    reporter.require(
        summary.get("unavailable") == unavailable_count,
        IMAGE_MANIFEST_PATH,
        "summary.unavailable 与逐项状态不一致",
    )
    reporter.require(
        summary.get("mirrored_bytes") == total_bytes,
        IMAGE_MANIFEST_PATH,
        "summary.mirrored_bytes 与实际文件不一致",
    )
    actual_paths = {
        path.resolve()
        for path in (ROOT / "docs" / "assets" / "problem-images").glob("*")
        if path.is_file()
    }
    reporter.require(
        actual_paths == available_paths,
        ROOT / "docs" / "assets" / "problem-images",
        f"图片目录与清单不一致：未登记 {len(actual_paths - available_paths)}，"
        f"缺失 {len(available_paths - actual_paths)}",
    )


def validate_public_text(reporter: Reporter) -> None:
    for path in iter_text_files():
        text = read_text(path, reporter)
        masked = mask_fenced_code(text) if path.suffix.lower() == ".md" else text
        is_rule_definition = (
            path.parent.resolve() == (ROOT / "scripts").resolve()
            and (path.name.startswith("check_") or path.name == "site_checks.py")
        )
        for number, line in enumerate(masked.splitlines(), 1):
            location = f"{relative(path)}:{number}"
            if not is_rule_definition:
                for name, pattern in SECRET_PATTERNS.items():
                    if pattern.search(line):
                        reporter.add(location, f"检测到{name}模式；内容未回显")
            if path.name != "corpus.json" and not is_rule_definition:
                for name, pattern in PRIVATE_TRACES.items():
                    if pattern.search(line):
                        reporter.add(location, f"检测到{name}；内容未回显")
                if PLACEHOLDER.search(line):
                    reporter.add(location, "不得发布占位内容")
            if DISALLOWED_INVISIBLE.search(line):
                reporter.add(location, "检测到双向文本控制字符")
        if path.suffix.lower() == ".md" and path.parts[-2:-1] != ("problems",):
            # Parsing also reports unclosed fences. Problem snippets are parsed
            # separately; duplicate diagnostics would only add noise.
            code_blocks(path, reporter)

    docs = sorted((ROOT / "docs").rglob("*.md"))
    for path in docs:
        if "downloads" in path.parts:
            continue
        text = read_text(path, reporter)
        reporter.require(
            bool(re.search(r"(?m)^#\s+\S", mask_fenced_code(text))),
            path,
            "页面缺少一级标题",
        )
        if "/problem.php?id=" in text:
            if path.parent == ROOT / "docs" / "problems" and path.stem.isdigit():
                expected = f"https://acm.haut.edu.cn/problem.php?id={path.stem}"
                unexpected = [
                    pid
                    for pid in PROBLEM_URL.findall(text)
                    if pid != path.stem
                ]
                reporter.require(
                    not unexpected,
                    path,
                    "完整题解页不得直链其他 HAUTOJ 题目",
                )
                reporter.require(expected in text, path, "完整题解页缺少自己的原题链接")
            else:
                reporter.add(
                    path,
                    "题目索引和专题页应链接站内题解，HAUTOJ 原题链接放在题解或折叠片段中",
                )


def main() -> None:
    reporter = Reporter()
    corpus_raw = load_json(CORPUS_PATH, reporter)
    validation_raw = load_json(VALIDATION_PATH, reporter)
    manifest_raw = load_json(IMAGE_MANIFEST_PATH, reporter)
    corpus = corpus_raw if isinstance(corpus_raw, dict) else {}
    validation = validation_raw if isinstance(validation_raw, dict) else {}
    manifest = manifest_raw if isinstance(manifest_raw, dict) else {}
    problems = problem_map(corpus, reporter)
    contests, _ = validate_corpus(corpus, problems, reporter)
    validations = validate_validation_data(validation, problems, corpus, reporter)
    validate_pages(contests, problems, validations, reporter)
    validate_image_manifest(manifest, problems, reporter)
    validate_public_text(reporter)
    reporter.finish()
    print(
        "内容检查通过："
        f"{len(contests)} 场周赛、{EXPECTED_OCCURRENCES} 个题目位置、"
        f"{len(problems)} 道唯一题、{EXPECTED_TOPICS} 个专题；"
        "默认折叠片段、逐题核验标签、公开内容与图片清单一致"
    )


if __name__ == "__main__":
    main()
