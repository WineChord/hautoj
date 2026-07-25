#!/usr/bin/env python3
"""Generate the HAUTOJ MkDocs site from the verified public corpus."""

from __future__ import annotations

import html
import ipaddress
import json
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
INCLUDES = ROOT / "includes" / "problems"
CORPUS_PATH = ROOT / "data" / "corpus.json"
VALIDATION_PATH = ROOT / "data" / "validation_by_pid.json"
IMAGE_MANIFEST_PATH = ROOT / "data" / "image_manifest.json"
SITE_MANIFEST_PATH = ROOT / "data" / "site_manifest.json"

CATEGORY_ORDER = [
    "入门语法、输入输出与格式",
    "模拟、枚举与构造",
    "数组、字符串与双指针",
    "排序、二分与区间",
    "数学、数论与组合",
    "贪心与博弈",
    "数据结构",
    "动态规划",
    "搜索、图论与树",
    "计算几何、概率与综合",
]

CATEGORY_SLUGS = {
    "入门语法、输入输出与格式": "syntax-io",
    "模拟、枚举与构造": "simulation-enumeration",
    "数组、字符串与双指针": "arrays-strings",
    "排序、二分与区间": "sorting-binary-search",
    "数学、数论与组合": "math-number-theory",
    "贪心与博弈": "greedy-games",
    "数据结构": "data-structures",
    "动态规划": "dynamic-programming",
    "搜索、图论与树": "search-graphs-trees",
    "计算几何、概率与综合": "geometry-probability",
}

CATEGORY_INTROS = {
    "入门语法、输入输出与格式": (
        "把题意准确翻译成顺序、分支和循环，并做到输出字符完全一致。"
        "训练的第一道门槛不是复杂算法，而是稳定拿到所有基础分。"
    ),
    "模拟、枚举与构造": (
        "模拟要求程序状态与题面过程一一对应；枚举要求证明没有漏解；"
        "构造则要逐条验证输出满足全部约束。"
    ),
    "数组、字符串与双指针": (
        "训练线性扫描、下标边界、字符处理、连续段和左右指针。"
        "这些能力频繁出现在周赛前中段，也是后续算法的实现基础。"
    ),
    "排序、二分与区间": (
        "排序把无序信息转成单调结构；二分依赖单调性；"
        "区间题重点处理端点、闭开边界与选择规则。"
    ),
    "数学、数论与组合": (
        "先从定义和小数据找规律，再用整除、奇偶、最大公约数、"
        "取模或组合计数证明公式，并检查溢出与浮点误差。"
    ),
    "贪心与博弈": (
        "贪心需要交换论证、下界或不变量，不能只凭直觉；"
        "博弈题先找终止条件、必胜态和奇偶性。"
    ),
    "数据结构": (
        "当普通数组无法高效维护动态信息时，再选择栈、队列、集合、"
        "哈希、堆、链表或线段树，并明确每个结构保存什么。"
    ),
    "动态规划": (
        "动态规划的四件事是状态、转移、初值和答案。"
        "先用一句话写清状态含义，再考虑维度与空间优化。"
    ),
    "搜索、图论与树": (
        "把对象抽象为点、关系抽象为边，再决定 BFS、DFS、最短路"
        "或树上遍历；必须处理访问标记、连通性和复杂度。"
    ),
    "计算几何、概率与综合": (
        "先确定数学模型，再决定是否需要浮点、期望线性性、"
        "距离公式或综合数据结构，尤其注意精度与边界。"
    ),
}

PRIMERS = {
    "固定输出与格式": "输出也是答案的一部分：大小写、空格、换行、标点和小数位数都必须符合题意。",
    "固定输出": "固定输出题通常不需要读入；逐字核对目标字符串、标点和末尾换行。",
    "条件分支": "if/else 按互斥条件选择处理路径；先覆盖特殊边界，再处理一般情况。",
    "循环": "for 适合已知次数，while 适合由条件或 EOF 决定的次数；每轮都要向结束推进。",
    "模拟": "列出状态变量与更新时间，严格按照题面规定的事件顺序更新。",
    "枚举": "明确搜索空间、判断条件与不漏解的理由，再用数据范围核对复杂度。",
    "构造": "输出不唯一时，要逐条验证长度、取值、互异性、相邻关系或总和等约束。",
    "字符串": "string 可按下标访问字符；注意空串、大小写、标点和 getline 与 cin 的衔接。",
    "字符串解析": "先确定分隔符、字符类别与多位数边界，再逐段转换。",
    "数组与序列": "明确 0/1 起下标、合法范围、首尾元素和扫描过程中保存的信息。",
    "双指针": "双指针依靠单调移动减少重复枚举；要说明移动哪一端以及为何不会漏解。",
    "双指针与滑动窗口": "右端扩展、左端收缩时必须同步维护窗口内的统计量。",
    "排序": "排序后应利用相邻性、前缀性质或单调性；并列时按题意添加次关键字。",
    "二分答案": "先写出单调判定函数，再确认可行区间、真假方向和最终返回边界。",
    "二分查找": "lower_bound 找第一个不小于目标的位置，upper_bound 找第一个大于目标的位置。",
    "数学": "把过程转成公式前，先在小数据上验证，再证明公式覆盖所有合法情况。",
    "数论": "常用整除、gcd/lcm、质数与同余；乘法前先估算是否需要 long long 或 __int128。",
    "最大公约数": "std::gcd(a, b) 使用欧几里得算法；同时检查 0 值、符号和 lcm 乘法溢出。",
    "取模": "C++ 的负数取模仍可能为负；减法后常用 (x - y + mod) % mod。",
    "奇偶性": "只关心奇偶时可化为 x % 2；和的奇偶等于各项奇偶的异或。",
    "贪心": "需要证明局部选择能延伸到全局最优，常用交换论证或下界与构造相遇。",
    "博弈论": "先找终止态和每步改变的量，再判断奇偶、不变量或必胜/必败状态。",
    "动态规划": "用一句话定义 dp 状态，再写转移来源、初值、遍历顺序和最终答案。",
    "状态压缩": "少量二元选择可用位掩码表示集合；第 i 位表示第 i 个对象是否被选择。",
    "哈希与集合": "set 有序去重，unordered_set 平均 O(1) 查询；先判断是否需要顺序。",
    "集合": "集合适合去重与成员查询；需要有序输出时使用 set 或最后排序。",
    "栈与队列": "栈后进先出，队列先进先出；BFS 用队列，括号和单调性问题常用栈。",
    "线段树": "先定义节点维护量，再实现 O(log n) 的修改和查询。",
    "搜索": "搜索必须有终止条件和访问标记；先估算状态数，避免指数爆炸。",
    "BFS": "无权图中 BFS 按层扩展，第一次到达某点就是最短步数。",
    "图论": "建图前明确点、边和方向；邻接表遍历通常是 O(V + E)。",
    "树": "树有 n - 1 条边且无环；指定根后可用父子关系做 DFS、BFS 或树形 DP。",
    "前缀和与差分": "前缀和支持 O(1) 区间和；差分把区间增减转成端点修改。",
    "前缀和": "pre[i] 表示前 i 项之和，区间 [l, r] 的和为 pre[r] - pre[l - 1]。",
    "计算几何": "明确坐标系、距离与边界；只比较距离时可用平方距离避免开方。",
    "浮点输出": "固定小数位可选 printf 或 fixed 配合 setprecision；整份程序不要混用两套 I/O。",
    "浮点误差": "不要用 == 比较计算得到的浮点数；按题目要求处理绝对或相对误差。",
    "概率": "先定义事件；期望常可用线性性拆成每个对象的贡献。",
    "高精度": "超过 64 位范围时使用字符串逐位运算，或使用题目允许的大整数方法。",
    "大整数": "若中间乘积可能超过约 9e18，就要考虑 __int128 或高精度。",
    "__int128": "__int128 可保存约 38 位有符号整数，但输入输出需要自行转换。",
    "文件尾输入": "多组数据读到 EOF 时使用 while (cin >> ...)，不要额外读取不存在的 T。",
}

PITFALLS = {
    "固定输出与格式": "核对中英文标点、尾随空格和末尾换行。",
    "条件分支": "条件应互斥并覆盖所有边界，尤其确认等号属于哪一侧。",
    "模拟": "按规定顺序更新，避免本轮新状态在同一轮被重复使用。",
    "字符串": "避免下标越界；getline 前要处理上一次格式化读入留下的换行。",
    "数组与序列": "检查 0/1 起下标、n = 1、首尾元素和数组容量。",
    "排序": "比较器必须满足严格弱序；相等时处理题目指定的次关键字。",
    "二分答案": "确认循环边界，以及最终返回第一个可行还是最后一个不可行。",
    "数学": "乘积使用足够宽的数据类型；除法前处理除数为 0 和整数截断。",
    "数论": "检查 gcd/lcm 的 0 值、负数和乘法溢出。",
    "取模": "减法结果可能为负，必要时先加模数再取模。",
    "贪心": "先尝试找反例；无法给出交换论证或下界时，不要把直觉当证明。",
    "动态规划": "初值不能默认全部可达；0/1 背包必须倒序遍历容量。",
    "搜索": "入队或递归时及时标记访问，避免重复状态和死循环。",
    "图论": "核对有向/无向、重边、自环、不可达点和点编号范围。",
    "浮点输出": "按误差要求保留精度，不要只按样例字符数猜小数位。",
    "构造": "样例只是一个合法答案；本地检查约束，不要逐字比对。",
    "文件尾输入": "EOF 题不要读取不存在的 T，也不要多输出内容。",
}

DIFFICULTY_ORDER = {"入门": 0, "基础提高": 1, "进阶": 2}
PRIVATE_URL = re.compile(
    r"https?://(?:localhost|127(?:\.\d+){3}|10(?:\.\d+){3}|"
    r"192\.168(?:\.\d+){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d+){2})"
    r"[^\s<>()]*",
    re.IGNORECASE,
)


def load_json(path: Path, default: object | None = None) -> object:
    if not path.is_file():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def clean_source_text(value: object) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    text = PRIVATE_URL.sub("（原题中的内网资源地址已省略）", text)
    return text.strip()


def compact(value: object) -> str:
    return re.sub(r"\s+", " ", clean_source_text(value)).strip()


def prose(value: object) -> str:
    text = html.escape(compact(value), quote=False)
    return text.replace("[", "&#91;").replace("]", "&#93;")


def title_text(value: object) -> str:
    text = prose(value)
    for char in ("\\", "`", "*", "_", "[", "]"):
        text = text.replace(char, "\\" + char)
    return text


def table_text(value: object) -> str:
    return prose(value).replace("|", "\\|")


def public_url(value: object) -> str | None:
    url = compact(value)
    if not url.startswith("https://") or PRIVATE_URL.search(url):
        return None
    try:
        host = urlsplit(url).hostname
        if not host:
            return None
        address = ipaddress.ip_address(host)
        if not address.is_global:
            return None
    except ValueError:
        if host.lower() == "localhost":
            return None
    return url


def external_link(label: object, url: object) -> str | None:
    safe_url = public_url(url)
    if not safe_url:
        return None
    return f"[{title_text(label)}](<{safe_url}>)"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def fence_block(language: str, text: object, *, indent: int = 0) -> str:
    body = clean_source_text(text).rstrip("\n")
    longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
    marker = "`" * max(3, longest + 1)
    prefix = " " * indent
    lines = [f"{prefix}{marker}{language}"]
    lines.extend(prefix + line for line in body.splitlines())
    lines.append(f"{prefix}{marker}")
    return "\n".join(lines)


def normalize_explanation(value: object) -> str:
    text = clean_source_text(value)
    cut = len(text)
    for pattern in (
        r"\n\s*\d*\s*#\s*include",
        r"\n\s*#include",
        r"\n\s*using\s+namespace",
        r"\n\s*int\s+main\s*\(",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match and match.start() >= 25:
            cut = min(cut, match.start())
    text = text[:cut]
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"(?m)^\s*\d+\s+(?=[#{}A-Za-z])", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ：:")
    if len(text) < 12:
        return "按题意建立变量，依次处理输入并严格按规定输出；具体实现见参考代码与边界检查。"
    return text


def primary_category(problem: dict) -> str:
    tags = set(problem.get("tags", []))
    code = problem.get("reference_code", "")
    if "固定输出与格式" in tags and not re.search(
        r"\bcin\s*>>|\bscanf\s*\(|\bgetline\s*\(", code
    ):
        return CATEGORY_ORDER[0]
    if tags & {"图论", "BFS", "DFS", "树", "最短路", "最短路思想", "搜索", "网格"}:
        return CATEGORY_ORDER[8]
    if tags & {"动态规划", "状态压缩", "滚动数组", "最长非降子序列"}:
        return CATEGORY_ORDER[7]
    if tags & {
        "线段树", "栈与队列", "哈希与集合", "集合", "优先队列", "双向链表", "延迟删除"
    }:
        return CATEGORY_ORDER[6]
    if tags & {"贪心", "博弈论", "区间调度"}:
        return CATEGORY_ORDER[5]
    if tags & {"计算几何", "几何", "欧氏距离", "曼哈顿距离", "概率", "期望"}:
        return CATEGORY_ORDER[9]
    if tags & {
        "数论", "最大公约数", "最小公倍数", "质数筛", "数学", "取模", "奇偶性",
        "组合计数", "快速幂", "阶乘", "对数", "高精度", "大整数", "__int128"
    }:
        return CATEGORY_ORDER[4]
    if tags & {"排序", "二分答案", "二分查找", "区间", "前缀和与差分", "前缀和"}:
        return CATEGORY_ORDER[3]
    if tags & {
        "字符串", "数组与序列", "双指针", "双指针与滑动窗口", "连续段", "矩阵",
        "二维数组", "字符串解析"
    }:
        return CATEGORY_ORDER[2]
    if tags & {"模拟", "枚举", "构造", "网格模拟", "预处理", "递推"}:
        return CATEGORY_ORDER[1]
    return CATEGORY_ORDER[0]


def primer_lines(problem: dict, maximum: int = 4) -> list[str]:
    lines: list[str] = []
    for tag in problem.get("tags", []):
        item = PRIMERS.get(tag)
        if item and item not in lines:
            lines.append(item)
        if len(lines) >= maximum:
            break
    return lines or [CATEGORY_INTROS[primary_category(problem)]]


def algorithm_steps(problem: dict) -> list[str]:
    tags = set(problem.get("tags", []))
    code = problem.get("reference_code", "")
    if "固定输出与格式" in tags and not re.search(
        r"\bcin\s*>>|\bscanf\s*\(|\bgetline\s*\(", code
    ):
        return [
            "确认题目没有输入，程序无需声明或读取测试数据。",
            f"按题面关系完成常数处理：{normalize_explanation(problem.get('official_explanation'))}",
            "原样输出结果，逐字核对字符、标点和末尾换行。",
            "程序不依赖输入规模，不需要循环或额外数据结构。",
        ]
    steps = ["按输入说明读取数据，并用最小样例手算一次，确认每个变量的含义。"]
    if "排序" in tags:
        steps.append("按主关键字排序；若存在并列规则，再加入次关键字。")
    elif tags & {"动态规划", "状态压缩"}:
        steps.append("定义 dp 状态、初值和遍历顺序，只从合法状态转移。")
    elif tags & {"图论", "BFS", "DFS", "搜索", "树"}:
        steps.append("把对象建成点和边，初始化访问或距离信息，再按选定算法扩展。")
    elif tags & {"贪心", "区间调度"}:
        steps.append("确定局部选择，并用交换论证或下界说明它不会损失最优解。")
    elif "枚举" in tags:
        steps.append("确定完整且可承受的枚举范围，对每个候选检查全部约束。")
    elif tags & {"模拟", "构造"}:
        steps.append("列出状态变量并按题面顺序更新；构造题同时维护每条输出约束。")
    elif tags & {"数学", "数论", "奇偶性", "最大公约数"}:
        steps.append("从定义推导公式或不变量，并用小数据验证边界、奇偶与整除关系。")
    else:
        steps.append("把条件翻译成分支或线性扫描，不引入题目没有要求的额外状态。")
    steps.append(f"核心处理：{normalize_explanation(problem.get('official_explanation'))}")
    steps.append("按题目要求输出，并检查最小值、最大值、重复值、单元素和格式边界。")
    return steps


def correctness_lines(problem: dict) -> list[str]:
    tags = set(problem.get("tags", []))
    code = problem.get("reference_code", "")
    if "固定输出与格式" in tags and not re.search(
        r"\bcin\s*>>|\bscanf\s*\(|\bgetline\s*\(", code
    ):
        return [
            "题目没有输入，所有合法运行面对同一个固定任务。",
            "程序输出的常量与题目要求逐字一致。",
            "因此程序在所有运行中都得到正确结果。",
        ]
    if tags & {"动态规划", "状态压缩"}:
        return [
            "状态保存当前阶段所需的全部信息，初始状态与空前缀或空选择一致。",
            "转移枚举所有合法来源，因此不漏解；取最优值时也不会保留劣解。",
            "处理完全部输入后，目标状态正好对应题目要求。",
        ]
    if tags & {"贪心", "区间调度"}:
        return [
            "每一步选择不占用额外未来资源，并达到当前可证明的最优边界。",
            "任意最优解若首个选择不同，都可交换为本算法的选择而不使答案变差。",
            "反复交换即可得到与算法相同的最优解，所以算法全局最优。",
        ]
    if tags & {"图论", "BFS", "DFS", "搜索", "树"}:
        return [
            "建图把每个合法对象或状态映射为点，把合法关系映射为边。",
            "扩展操作覆盖所有且仅覆盖合法转移，访问标记避免重复处理。",
            "遍历结束后的可达性、距离或累计值与原问题一一对应。",
        ]
    if "构造" in tags:
        return [
            "构造只使用题目允许的元素与操作。",
            "每一步都维持长度、取值、相邻关系或总和等约束。",
            "结束时规模达到要求，因此输出是完整合法答案。",
        ]
    if "模拟" in tags:
        return [
            "初始变量与题面初始状态相同。",
            "若某一步前状态正确，本步按相同顺序和规则更新后仍保持一致。",
            "由归纳法，全部步骤结束时程序状态与答案都正确。",
        ]
    if "枚举" in tags:
        return [
            "枚举范围包含每个可能答案，没有候选被遗漏。",
            "判断条件与题目约束等价，所以只接受合法候选。",
            "按题意计数、取最优或输出这些候选即可得到答案。",
        ]
    if tags & {"数学", "数论", "奇偶性", "最大公约数"}:
        return [
            "推导把原过程转换为等价的公式或不变量。",
            "算法直接计算该等价量，并使用足够宽的数据类型处理边界。",
            "因此计算结果就是题目定义的答案。",
        ]
    return [
        "扫描前保存的信息与已经处理的输入完全对应。",
        "每读入一个元素，分支覆盖其全部情况并正确更新累计状态。",
        "扫描结束时所有输入恰好处理一次，累计状态即为所求。",
    ]


def pitfall_lines(problem: dict) -> list[str]:
    lines: list[str] = []
    for tag in problem.get("tags", []):
        item = PITFALLS.get(tag)
        if item and item not in lines:
            lines.append(item)
        if len(lines) >= 3:
            break
    code = problem.get("reference_code", "")
    if ("long long" in code or "__int128" in code) and len(lines) < 4:
        lines.append("按上界估算中间量，相关变量不要退回 int。")
    if "setprecision" in code and len(lines) < 4:
        lines.append("setprecision 单独使用表示有效数字；固定小数位时必须配合 fixed。")
    return lines or ["核对读入顺序、变量类型和输出格式。", "自测最小规模、最大边界和一般样例。"]


def validation_label(pid: int, validations: dict) -> str:
    result = validations["problems"][str(pid)]
    if not result.get("compiled"):
        return "C++17 编译核验未通过"
    status = result.get("sample_status")
    if status == "passed":
        count = result.get("sample_passes", 0)
        return f"C++17 编译通过；{count} 个可机读公开样例/合法构造校验通过"
    if status == "unavailable_or_non_machine_readable":
        return "C++17 编译通过；公开样例不可机读，未执行"
    return "C++17 编译通过；未提供成对公开样例"


def code_origin(problem: dict) -> str:
    if problem.get("reference_code_status") == "independently-rewritten":
        return "依据公开题面与解析独立改写"
    return "依据公开题解整理"


def safe_source_links(problem: dict) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    official = public_url(problem.get("url"))
    if official:
        links.append(("HAUTOJ 原题", official))
    for index, url in enumerate(problem.get("solution_sources", []), 1):
        safe = public_url(url)
        if safe:
            links.append((f"公开题解/参考来源 {index}", safe))
    return links


def available_images(pid: int, image_manifest: dict) -> list[dict]:
    return [
        item
        for item in image_manifest.get("images", [])
        if isinstance(item, dict)
        and str(item.get("problem_id")) == str(pid)
        and item.get("status") == "available"
        and isinstance(item.get("path"), str)
    ]


def snippet_markdown(problem: dict, validations: dict, image_manifest: dict) -> str:
    official = external_link("打开 HAUTOJ 原题 ↗", problem.get("url")) or "原题链接当前不可用"
    code = fence_block("cpp linenums=\"1\"", problem["reference_code"], indent=4)
    proof = "；".join(correctness_lines(problem))
    pitfalls = "；".join(pitfall_lines(problem))
    validation = validation_label(problem["pid"], validations)
    lines = [
        '??? problem "展开：题意、思路、证明、复杂度与 C++17 代码"',
        f"    **题目**：{prose(problem['title'])}",
        "",
        f"    {official}{{ .problem-source target=\"_blank\" rel=\"noopener\" }}",
        "",
        f"    **题意**：{prose(problem.get('task_focus'))}",
        "",
        f"    **核心思路**：{prose(normalize_explanation(problem.get('official_explanation')))}",
        "",
        f"    **正确性**：{prose(proof)}",
        "",
        f"    **复杂度**：{prose(problem.get('complexity_note') or '以参考代码的主循环和数据结构为准。')}",
        "",
        f"    **易错点**：{prose(pitfalls)}",
        "",
        f"    - 核验状态：{prose(validation)}",
        "",
    ]
    lines.extend(
        [
        "    **C++17 实现**",
        "",
        code,
        ]
    )
    return "\n".join(lines)


def problem_page(
    problem: dict,
    validations: dict,
    image_manifest: dict,
    contest_year_by_cid: dict[int, str],
    previous_pid: int | None,
    next_pid: int | None,
) -> str:
    pid = int(problem["pid"])
    category = primary_category(problem)
    title = f"PID {pid} · {compact(problem['title'])}"
    tags_json = json.dumps(problem.get("tags", []), ensure_ascii=False)
    submissions = int(problem.get("submissions") or 0)
    accepted = int(problem.get("accepted") or 0)
    rate = f"{accepted / submissions:.1%}" if submissions else "暂无公开统计"
    official = external_link("打开 HAUTOJ 原题 ↗", problem.get("url")) or "原题链接当前不可用"
    lines = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"description: {json.dumps(compact(problem.get('task_focus')), ensure_ascii=False)}",
        f"tags: {tags_json}",
        "---",
        "",
        f"# {title_text(title)}",
        "",
        f"{official}{{ .md-button .md-button--primary target=\"_blank\" rel=\"noopener\" }}",
        "",
        "!!! info \"资料说明\"",
        "    本页是依据公开题面、公开解析与可复现代码核验整理的学习资料，不代表学校或校队的官方题解。",
        "",
        "## 题目档案",
        "",
        "| 项目 | 内容 |",
        "|---|---|",
        f"| 训练定位 | {table_text(problem.get('difficulty'))} |",
        f"| 主知识模块 | [{table_text(category)}](../topics/{CATEGORY_SLUGS[category]}.md) |",
        f"| 知识标签 | {table_text('、'.join(problem.get('tags', [])) or '基础实现')} |",
        f"| 时间 / 内存 | {table_text(problem.get('time_limit') or '未标明')} / {table_text(problem.get('memory_limit') or '未标明')} |",
        f"| 历史统计 | {accepted} 次通过 / {submissions} 次提交（{rate}） |",
        f"| 代码来源 | {table_text(code_origin(problem))} |",
        f"| 核验状态 | {table_text(validation_label(pid, validations))} |",
        "",
        "接受率只反映抓取时的历史提交快照，不等于官方难度或选拔分数线。",
        "",
        "## 周赛出处",
        "",
    ]
    for occurrence in problem.get("occurrences", []):
        cid = int(occurrence["cid"])
        year = contest_year_by_cid[cid]
        contest_link = f"../contests/{year}/{occurrence['cid']}.md"
        lines.append(
            f"- [{title_text(occurrence['contest_title'])}]({contest_link})"
            f" · {title_text(occurrence['letter'])} 题 · "
            f"{int(occurrence.get('accepted') or 0)}/{int(occurrence.get('submissions') or 0)}"
        )
    lines.extend(
        [
            "",
            "## 题意与输入输出",
            "",
            f"**题意摘要**：{prose(problem.get('task_focus'))}",
            "",
            f"**输入要点**：{prose(problem.get('input_summary') or '公开页面没有可提取的文字输入说明，请在原题页核对题面图片或特殊格式。')}",
            "",
            f"**输出要点**：{prose(problem.get('output_summary') or '公开页面没有可提取的文字输出说明，请在原题页核对题面图片或特殊格式。')}",
        ]
    )
    if problem.get("hint"):
        lines.extend(["", f"**题面提示**：{prose(problem['hint'])}"])
    lines.extend(["", "## 零基础先修", ""])
    lines.extend(f"- {prose(item)}" for item in primer_lines(problem))
    lines.extend(["", "## 思路推导", ""])
    lines.extend(f"{index}. {prose(item)}" for index, item in enumerate(algorithm_steps(problem), 1))
    lines.extend(["", "## 正确性说明", ""])
    lines.extend(f"- {prose(item)}" for item in correctness_lines(problem))
    lines.extend(
        [
            "",
            "## 复杂度",
            "",
            prose(problem.get("complexity_note") or "以参考代码的主循环和数据结构为准。"),
            "",
            "## 易错点",
            "",
        ]
    )
    lines.extend(f"- {prose(item)}" for item in pitfall_lines(problem))
    code = problem.get("reference_code", "")
    if "setprecision" in code or "printf" in code:
        lines.extend(
            [
                "",
                "!!! tip \"输出精度\"",
                "    `printf` 与 `fixed << setprecision(...)` 都是常见竞赛写法；区别和混用限制见[浮点输出专题](../guide/precision.md)。",
            ]
        )
    lines.extend(
        [
            "",
            "## 题目摘要与 C++17 参考实现",
            "",
            f'--8<-- "includes/problems/haut-{pid}.md"',
            "",
            "## 公开样例",
            "",
        ]
    )
    inputs = problem.get("sample_inputs", [])
    outputs = problem.get("sample_outputs", [])
    paired = min(len(inputs), len(outputs))
    if paired:
        for index in range(paired):
            lines.extend(
                [
                    f"### 样例 {index + 1}",
                    "",
                    "**输入**",
                    "",
                    fence_block("text", inputs[index]),
                    "",
                    "**输出**",
                    "",
                    fence_block("text", outputs[index]),
                    "",
                ]
            )
    elif outputs:
        lines.extend(
            [
                "公开页面只提取到了样例输出，没有可配对的输入；以下内容仅用于核对输出格式。",
                "",
            ]
        )
        for index, output in enumerate(outputs, 1):
            lines.extend([f"**输出 {index}**", "", fence_block("text", output), ""])
    else:
        lines.append("公开页面没有提供可成对提取的样例，请在原题页核对图片或特殊格式。")
    mirrored = available_images(pid, image_manifest)
    if mirrored:
        lines.extend(["", "## 题面图片", ""])
        for image in mirrored:
            image_index = int(image.get("image_index") or 1)
            lines.extend(
                [
                    f"![PID {pid} 题面图 {image_index}]"
                    f"(../{image['path']})",
                    "",
                ]
            )
    links = safe_source_links(problem)
    lines.extend(
        [
            "",
            "## 训练动作",
            "",
            "- 遮住代码，用 3～5 句话复述状态、步骤和复杂度，再独立实现。",
            "- 自行补三个边界：最小规模、极端或全部相等、恰好卡在条件等号处。",
            f"- 将本题归档到“{title_text(category)}”，一周后限时重做。",
            "",
            "## 链接与来源",
            "",
        ]
    )
    for label, url in links:
        rendered = external_link(label, url)
        if rendered:
            lines.append(f"- {rendered}")
    lines.extend(
        [
            "",
            "---",
            "",
            '<div class="problem-nav" markdown="1">',
            (
                f"[← PID {previous_pid}]({previous_pid}.md)"
                if previous_pid is not None
                else "<span></span>"
            ),
            "[返回题目索引](index.md)",
            (
                f"[PID {next_pid} →]({next_pid}.md)"
                if next_pid is not None
                else "<span></span>"
            ),
            "</div>",
        ]
    )
    return "\n".join(lines)


def contest_page(contest: dict, corpus: dict, previous: dict | None, next_: dict | None) -> str:
    year = contest["end_time"][:4] if contest.get("end_time") else "unknown"
    title = compact(contest["title"])
    description = f"{year} 新生周赛，CID {contest['cid']}"
    official = external_link("打开 HAUTOJ 比赛页 ↗", contest.get("url")) or "比赛链接当前不可用"
    public = contest.get("access_status") == "public"
    lines = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"description: {json.dumps(description, ensure_ascii=False)}",
        f"tags: [{json.dumps(year)}, 周赛]",
        "---",
        "",
        f"# {title_text(title)}",
        "",
        f"{official}{{ .md-button .md-button--primary target=\"_blank\" rel=\"noopener\" }}",
        "",
        "| 项目 | 内容 |",
        "|---|---|",
        f"| 日期 | {table_text((contest.get('end_time') or '未显示')[:10])} |",
        f"| CID | {int(contest['cid'])} |",
        f"| 访问状态 | {'公开' if public else '需要竞赛密码'} |",
        f"| 已核验题数 | {len(contest.get('problems', []))} |",
        "",
    ]
    if not public:
        lines.extend(
            [
                "!!! warning \"公开边界\"",
                "    当前公开页面要求竞赛密码，因此这里只保留可核验的比赛元数据；不猜测题号，也不绕过访问限制。",
            ]
        )
    else:
        lines.extend(
            [
                "本页按比赛原始题号顺序排列。每题的折叠区给出题意、核心思路、复杂度和完整 C++17 实现；标题旁可进入更详细的规范题解页。",
                "",
            ]
        )
        for occurrence in contest.get("problems", []):
            problem = corpus["problems"][str(occurrence["pid"])]
            lines.extend(
                [
                    f"## {title_text(occurrence['letter'])}. {title_text(problem['title'])}",
                    "",
                    f"[查看完整题解](../../problems/{problem['pid']}.md) · "
                    f"{int(occurrence.get('accepted') or 0)} 次通过 / "
                    f"{int(occurrence.get('submissions') or 0)} 次提交",
                    "",
                    f'--8<-- "includes/problems/haut-{problem["pid"]}.md"',
                    "",
                ]
            )
    lines.extend(
        [
            "---",
            "",
            '<div class="problem-nav" markdown="1">',
            (
                f"[← 较新的周赛](../{previous['end_time'][:4]}/{previous['cid']}.md)"
                if previous is not None
                else "<span></span>"
            ),
            "[返回周赛索引](../index.md)",
            (
                f"[更早的周赛 →](../{next_['end_time'][:4]}/{next_['cid']}.md)"
                if next_ is not None
                else "<span></span>"
            ),
            "</div>",
        ]
    )
    return "\n".join(lines)


def contest_index(corpus: dict) -> str:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for contest in corpus["contests"]:
        year = contest["end_time"][:4] if contest.get("end_time") else "未标明"
        grouped[year].append(contest)
    lines = [
        "# 新生周赛：从最新到最早",
        "",
        "共收录 78 场新生周赛，其中 76 场题目公开、2 场需要竞赛密码。"
        "进入比赛页后，可按原题号顺序展开题意、核心思路、复杂度和 C++17 实现。",
        "",
        "!!! note \"使用建议\"",
        "    第一次打开一场比赛时，先不要展开答案。按 A、B、C 的顺序限时作答，卡住后再展开对应题目的摘要。",
        "",
    ]
    for year in sorted(grouped, reverse=True):
        lines.extend([f"## {year} 年", "", "| 日期 | 比赛 | 题数 | 状态 |", "|---|---|---:|---|"])
        for contest in grouped[year]:
            status = "公开" if contest["access_status"] == "public" else "密码"
            lines.append(
                f"| {table_text((contest.get('end_time') or '')[:10])} | "
                f"[{table_text(contest['title'])}]({year}/{contest['cid']}.md) | "
                f"{len(contest.get('problems', []))} | {status} |"
            )
        lines.append("")
    return "\n".join(lines)


def problem_index(corpus: dict) -> str:
    problems = sorted(corpus["problems"].values(), key=lambda item: int(item["pid"]))
    lines = [
        "# 526 道规范题解索引",
        "",
        "每个 PID 只有一个规范题解页。可使用页面搜索，或从周赛、知识模块和标签进入同一题。",
        "",
        "| PID | 题目 | 难度 | 主知识模块 | 出现次数 |",
        "|---:|---|---|---|---:|",
    ]
    for problem in problems:
        category = primary_category(problem)
        lines.append(
            f"| {problem['pid']} | [{table_text(problem['title'])}]({problem['pid']}.md) | "
            f"{table_text(problem['difficulty'])} | "
            f"[{table_text(category)}](../topics/{CATEGORY_SLUGS[category]}.md) | "
            f"{len(problem.get('occurrences', []))} |"
        )
    return "\n".join(lines)


def topic_page(category: str, problems: list[dict]) -> str:
    slug = CATEGORY_SLUGS[category]
    counts = Counter(problem["difficulty"] for problem in problems)
    lines = [
        "---",
        f"title: {json.dumps(category, ensure_ascii=False)}",
        f"description: {json.dumps(CATEGORY_INTROS[category], ensure_ascii=False)}",
        f"tags: [{json.dumps(category, ensure_ascii=False)}]",
        "---",
        "",
        f"# {title_text(category)}",
        "",
        prose(CATEGORY_INTROS[category]),
        "",
        f"本模块共 **{len(problems)}** 道唯一题："
        f"入门 {counts.get('入门', 0)} 道、基础提高 {counts.get('基础提高', 0)} 道、进阶 {counts.get('进阶', 0)} 道。",
        "",
        "题目按难度、PID 排列。先进入完整题解独立阅读；需要快速复习时再展开摘要与代码。",
        "",
    ]
    for difficulty in ("入门", "基础提高", "进阶"):
        selected = [problem for problem in problems if problem["difficulty"] == difficulty]
        if not selected:
            continue
        lines.extend([f"## {difficulty}", ""])
        for problem in selected:
            lines.extend(
                [
                    f"### PID {problem['pid']} · {title_text(problem['title'])}",
                    "",
                    f"[查看完整题解](../problems/{problem['pid']}.md) · "
                    f"出现于 {len(problem.get('occurrences', []))} 场周赛",
                    "",
                    f'--8<-- "includes/problems/haut-{problem["pid"]}.md"',
                    "",
                ]
            )
    return "\n".join(lines)


def topics_index(grouped: dict[str, list[dict]]) -> str:
    lines = [
        "# 知识体系",
        "",
        "同一道题只维护一份规范题解；知识页按主模块组织，并保留题目曾出现的全部周赛出处。",
        "",
        "| 模块 | 唯一题数 | 学习重点 |",
        "|---|---:|---|",
    ]
    for category in CATEGORY_ORDER:
        lines.append(
            f"| [{table_text(category)}]({CATEGORY_SLUGS[category]}.md) | "
            f"{len(grouped[category])} | {table_text(CATEGORY_INTROS[category])} |"
        )
    return "\n".join(lines)


def homepage(corpus: dict, validations: dict) -> str:
    return f"""# HAUTOJ 新生周赛题解与训练手册

基于河南工业大学 HAUTOJ 公开新生周赛整理的系统化学习资料：既能按周赛时间复盘，也能按知识模块训练，并为每道题提供可搜索、可深链的规范题解。

!!! warning "非官方资料"
    本站由公开页面与公开参考资料独立整理，不代表河南工业大学、学院或校队；当年通知、选拔规则与比赛要求应以正式渠道为准。

<div class="metric-grid">
  <div><strong>{corpus["contest_count"]}</strong><span>场新生周赛</span></div>
  <div><strong>{corpus["public_contest_count"]}</strong><span>场公开可核验</span></div>
  <div><strong>{corpus["problem_occurrence_count"]}</strong><span>个题目位置</span></div>
  <div><strong>{corpus["unique_problem_count"]}</strong><span>道唯一题</span></div>
</div>

## 两条主学习路径

### 按周赛复盘

[从 2025 年第 8 场开始](contests/index.md){{ .md-button .md-button--primary }}

严格保留每场比赛与 A～K 题顺序。默认折叠题意、思路、复杂度和代码，适合先独立作答，再按需展开。

### 按知识体系训练

[打开 10 个知识模块](topics/index.md){{ .md-button }}

把 526 道唯一题重组为语法与格式、模拟、字符串、排序与二分、数学、贪心、数据结构、动态规划、图论与综合模块。

## 从哪里开始

1. 零基础先看[16 周学习路线](guide/roadmap.md)和[竞赛 C++](guide/cpp.md)。
2. 特别容易混淆的浮点输出，单独阅读[`printf` 与 `setprecision`](guide/precision.md)。
3. 每周完成一场[新生周赛](contests/index.md)，24 小时内补题，一周后重做。
4. 用[题解索引](problems/index.md)按 PID、标题或搜索结果直达规范题解。

## 核验状态

- C++17 编译：{validations["compiled"]}/{validations["problem_count"]}。
- 至少一个可机读公开样例或合法构造通过：{validations["sample_pass_problem_count"]} 道。
- 无成对样例或样例不可机读：{validations["sample_unavailable_problem_count"]} 道，页面逐题明确标注，不把“未执行”写成“已通过”。
- 语料核验日期：{corpus["verified_through"]}。

[查看收录边界与来源](methodology.md)
"""


def guide_pages() -> dict[str, str]:
    return {
        "guide/roadmap.md": """# 16 周训练路线

每周建议 5 天训练：2 天学习知识与模板、2 天专题题单、1 天完整周赛；另留 1 天补题复盘、1 天休息。只统计独立 AC 和一周后重做，不用“看过多少题解”衡量进度。

| 周 | 主题 | 达标动作 |
|---:|---|---|
| 1 | 环境、输入输出、分支 | 完成入门与格式题，做到零编译错误 |
| 2 | 循环、数组、最值、计数 | 独立写线性扫描，掌握 0/1 下标 |
| 3 | 字符串、字符处理、连续段 | 掌握 getline、子序列、回文和词频 |
| 4 | 排序、结构体、并列规则 | 写对比较器并解释排序后的单调性 |
| 5 | 枚举、模拟、构造 | 估算枚举量，为构造写检查器 |
| 6 | 基础数学、奇偶、整除 | 从小数据发现并证明公式 |
| 7 | gcd、质数、取模、快速幂 | 处理数论边界和整数溢出 |
| 8 | 前缀和、差分、双指针 | 减少重复区间计算 |
| 9 | 贪心与区间 | 给每个贪心写交换论证或反例 |
| 10 | 栈、队列、集合、哈希、堆 | 说明选用结构与复杂度 |
| 11 | BFS、DFS、网格与建图 | 正确标记访问并处理不可达 |
| 12 | 动态规划、背包 | 写清状态、转移、初值和答案 |
| 13 | 二分答案、状态压缩 | 识别单调性和小 n 子集结构 |
| 14 | 树、最短路与综合题 | 完成中后段题并写复盘 |
| 15 | 按年份完成 4 场虚拟赛 | 记录首 AC、罚时、错因和补题 |
| 16 | 完整模拟与查漏补缺 | 连续四场稳定输出，清空高频错因 |

## 每场训练闭环

1. 前 10 分钟通读全题，标记“直接做 / 有方向 / 暂无方向”。
2. 先保证基础题一次通过，再处理中档题；连续 30 分钟无实质进展就切题。
3. 每次错误提交记录原因：读题、模型、复杂度、实现、边界或格式。
4. 24 小时内补完有方向的题，72 小时内从空文件重写关键题。
5. 一周后不看题解重做；能说明正确性与复杂度才算掌握。
""",
        "guide/cpp.md": r"""# 竞赛 C++17

## 最小骨架

```cpp linenums="1"
#include <bits/stdc++.h>
using namespace std;
int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int n;
    if (!(cin >> n)) return 0;
    // 读入 -> 计算 -> 输出
    return 0;
}
```

## 代码风格

- 每份代码完整、可编译，使用 C++17。
- 变量名采用常见竞赛缩写，例如 `n`、`m`、`ans`、`cnt`、`pre`、`dis`。
- 二元运算符两侧保留空格；`if (`、`for (`、逗号后保留常规空格。
- 代码块内部不放空行，缩进 4 空格，不使用 Tab。
- 先保证含义清楚，再追求短；不要把不同概念压成同一个字母。

## 常用数据类型

| 类型 | 常见用途 | 风险 |
|---|---|---|
| `int` | 计数、下标 | 乘法可能在约 2.1e9 外溢出 |
| `long long` | 大整数 | 超过约 9e18 仍会溢出 |
| `__int128` | 大整数中间量 | 需要自行输入输出 |
| `double` | 一般浮点 | 计算结果通常不能直接用 `==` 比较 |

## 本地编译

```bash
g++ -std=c++17 -O2 -pipe main.cpp -o main
./main
```

浮点格式单独见[`printf` 与 `setprecision`](precision.md)。
""",
        "guide/precision.md": r"""# 浮点输出：`printf` 还是 `setprecision`

两种写法在算法竞赛中都很常见。选择哪一种主要取决于整份程序使用的输入输出体系，而不是性能上的绝对优劣。

## 固定保留小数位

### `scanf` / `printf`

```cpp linenums="1"
#include <bits/stdc++.h>
using namespace std;
int main() {
    double x;
    scanf("%lf", &x);
    printf("%.6f\n", x);
    return 0;
}
```

`printf("%.6f", x)` 表示小数点后 6 位，短而直接。注意 `scanf` 读取 `double` 用 `%lf`，`printf` 输出 `double` 用 `%f`；`long double` 的输入输出都用 `%Lf`。

### `cin` / `cout`

```cpp linenums="1"
#include <bits/stdc++.h>
using namespace std;
int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    double x;
    cin >> x;
    cout << fixed << setprecision(6) << x << '\n';
    return 0;
}
```

这套写法与 `string`、容器和其他 C++ 接口保持一致。`fixed` 与 `setprecision` 会持续影响后续输出，需要改变格式时重新设置。

## 最容易写错的区别

- `setprecision(6)`：默认表示 **6 位有效数字**。
- `fixed << setprecision(6)`：表示 **小数点后 6 位**。
- `scientific << setprecision(6)`：使用科学计数法，并保留小数点后 6 位。

## 不要混用

调用 `ios::sync_with_stdio(false)` 后，不要再把 `printf` 与 `cout` 混在同一程序里，否则缓冲顺序可能不符合预期。整份程序选择一套：

- 全程 `scanf` / `printf`；或
- 全程 `cin` / `cout`。

## 怎么选

- 只有简单数字输入输出，并且习惯格式串：`printf` 很顺手。
- 使用 `string`、容器、重载输出，或想统一 C++ 风格：使用 `cout`。
- 题目只要求误差而不要求固定格式：通常输出 10～15 位有效数字即可，例如 `cout << setprecision(15) << x;`。
- 题目明确“保留 k 位小数”：使用 `printf("%.kf", x)` 的实际常量形式，或 `fixed << setprecision(k)`。

无论选择哪一种，都要按题目给出的绝对或相对误差判断精度，不要只模仿样例显示了几位。
""",
        "guide/strategy.md": """# 周赛作答与复盘

## 赛中

1. 快速读完标题、输入规模和输出要求，再决定顺序。
2. 先写确定能过的题，减少 CE、格式 WA 和读入错误。
3. 为每题写下当前结论与卡点；切题后回来时不必重新思考。
4. 中后段题先确认模型和复杂度，再开始实现。
5. 提交前检查多组数据、EOF、数组范围、溢出、换行和小数位。

## 赛后

- 把错误分成读题、知识、思路、复杂度、实现、边界和格式。
- 能修出的题在 24 小时内完成；不会的题先读思路，再关闭答案重写。
- 为构造题写检查器；为浮点题按误差比较；为随机或边界问题做小数据对拍。
- 一周后限时重做，仍需查看代码的题继续保留在错题表。

## 能力层级

| 层级 | 达标表现 |
|---|---|
| L0 读题 | 复述输入输出，从范围判断大致复杂度 |
| L1 实现 | 10 分钟内写出无 CE 的基础程序 |
| L2 模型 | 识别排序、前缀和、贪心、搜索、DP 等主要模型 |
| L3 证明 | 用不变量、归纳、交换论证或状态定义解释正确性 |
| L4 调试 | 用最小反例定位边界、溢出、状态更新和格式问题 |
| L5 比赛 | 会排题、止损切题、控制罚时并完成赛后补题 |
""",
    }


def methodology_page(corpus: dict, validations: dict) -> str:
    return f"""# 收录边界与核验方法

## 覆盖范围

- 核验日期：{corpus["verified_through"]}。
- 周赛列表识别 78 场新生周赛，其中 76 场题目公开、2 场需要竞赛密码。
- 公开场次共有 588 个题目位置，对应 526 道唯一题。
- 密码场次只保留公开可见的标题、日期和 CID，不猜题，也不绕过访问限制。

## 代码与样例

- 526/526 份参考代码通过 C++17 编译。
- {validations["sample_pass_problem_count"]} 道题至少有一个可机读公开样例或合法构造完成执行核验。
- {validations["sample_unavailable_problem_count"]} 道题没有成对样例，或样例不可机读；页面逐题标注“未执行”原因。
- 公开样例通过不等于隐藏数据必然 AC，学习时仍应在原 OJ 提交验证。

## 教学分析

难度、知识标签、主模块、训练优先级与通用正确性说明属于教学整理，不是学校或团队的官方大纲。问题页会保留原题、比赛和公开参考来源，方便交叉核验。

## 内容组织

- 一个 PID 对应一个规范题解页和一个可复用摘要片段。
- 周赛页按原始 A～K 顺序引用摘要。
- 知识页按主模块和难度引用同一摘要。
- 所有完整代码来自格式化后的规范数据，不从两个大型 Markdown 文件二次复制。
"""


def downloads_page() -> str:
    return """# 离线下载

网页适合搜索、深链和移动端阅读；离线文件适合整本保存、打印和在 PDF 阅读器中使用书签目录。

| 版本 | Markdown | PDF |
|---|---|---|
| 周赛时间顺序版 | [下载 Markdown](HAUTOJ新生周赛全题详解_时间顺序版.md) | [下载 PDF](HAUTOJ新生周赛全题详解_时间顺序版.pdf) |
| 知识体系版 | [下载 Markdown](HAUTOJ新生算法训练手册_知识体系版.md) | [下载 PDF](HAUTOJ新生算法训练手册_知识体系版.pdf) |

PDF 已包含原生书签目录；C++ 代码使用等宽字体、行号和语法高亮，中文注释使用独立字体回退。
"""


def sources_page(corpus: dict) -> str:
    urls = [
        "https://acm.haut.edu.cn/contest.php?page=1",
        "https://acm.haut.edu.cn/contest.php?page=2",
        "https://acm.haut.edu.cn/contest.php?page=3",
        "https://acm.haut.edu.cn/contest.php?page=4",
    ]
    lines = [
        "# 来源",
        "",
        "## HAUTOJ 新生周赛列表",
        "",
    ]
    lines.extend(f"- [周赛列表第 {index} 页](<{url}>)" for index, url in enumerate(urls, 1))
    lines.extend(
        [
            "",
            "## 逐题来源",
            "",
            "每道规范题解页列出 HAUTOJ 原题、全部周赛出处、公开题解或参考资料以及可公开访问的题面资源。"
            "外部资料只用于交叉核验；参考实现按页面标注的来源口径独立改写或整理。",
            "",
            "## 权利与归属",
            "",
            "题目名称、题面与原始素材的权利归各自作者和平台所有。本站提供学习导航、教学分析与可复现代码核验；"
            "引用原题时请以 HAUTOJ 页面为准。",
            "",
            f"语料核验日期：{corpus['verified_through']}。",
        ]
    )
    return "\n".join(lines)


def build() -> None:
    corpus = load_json(CORPUS_PATH)
    validations = load_json(VALIDATION_PATH)
    image_manifest = load_json(IMAGE_MANIFEST_PATH, default={"images": []})
    if (
        not isinstance(corpus, dict)
        or not isinstance(validations, dict)
        or not isinstance(image_manifest, dict)
    ):
        raise TypeError("Corpus and validation records must be objects")
    expected = {
        "contest_count": 78,
        "public_contest_count": 76,
        "problem_occurrence_count": 588,
        "unique_problem_count": 526,
    }
    for key, value in expected.items():
        if int(corpus.get(key, -1)) != value:
            raise ValueError(f"{key}: expected {value}, got {corpus.get(key)}")
    if int(validations.get("problem_count", -1)) != 526 or int(validations.get("compiled", -1)) != 526:
        raise ValueError("Per-problem validation coverage is incomplete")

    generated_dirs = [
        DOCS / "contests",
        DOCS / "problems",
        DOCS / "topics",
        INCLUDES,
    ]
    for path in generated_dirs:
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)

    problems = sorted(corpus["problems"].values(), key=lambda item: int(item["pid"]))
    contest_year_by_cid = {
        int(contest["cid"]): (
            contest["end_time"][:4] if contest.get("end_time") else "unknown"
        )
        for contest in corpus["contests"]
    }
    for index, problem in enumerate(problems):
        pid = int(problem["pid"])
        write(
            INCLUDES / f"haut-{pid}.md",
            snippet_markdown(problem, validations, image_manifest),
        )
        write(
            DOCS / "problems" / f"{pid}.md",
            problem_page(
                problem,
                validations,
                image_manifest,
                contest_year_by_cid,
                int(problems[index - 1]["pid"]) if index else None,
                int(problems[index + 1]["pid"]) if index + 1 < len(problems) else None,
            ),
        )
    write(DOCS / "problems" / "index.md", problem_index(corpus))

    contests = corpus["contests"]
    for index, contest in enumerate(contests):
        year = contest["end_time"][:4] if contest.get("end_time") else "unknown"
        previous = contests[index - 1] if index else None
        next_ = contests[index + 1] if index + 1 < len(contests) else None
        write(
            DOCS / "contests" / year / f"{contest['cid']}.md",
            contest_page(contest, corpus, previous, next_),
        )
    write(DOCS / "contests" / "index.md", contest_index(corpus))

    grouped: dict[str, list[dict]] = {category: [] for category in CATEGORY_ORDER}
    for problem in problems:
        grouped[primary_category(problem)].append(problem)
    for category in CATEGORY_ORDER:
        grouped[category].sort(
            key=lambda item: (DIFFICULTY_ORDER.get(item["difficulty"], 9), int(item["pid"]))
        )
        write(DOCS / "topics" / f"{CATEGORY_SLUGS[category]}.md", topic_page(category, grouped[category]))
    write(DOCS / "topics" / "index.md", topics_index(grouped))

    write(DOCS / "index.md", homepage(corpus, validations))
    for relative, text in guide_pages().items():
        write(DOCS / relative, text)
    write(DOCS / "methodology.md", methodology_page(corpus, validations))
    write(DOCS / "downloads" / "index.md", downloads_page())
    write(DOCS / "sources.md", sources_page(corpus))
    write(
        DOCS / "changelog.md",
        f"""# 更新日志

## {corpus["verified_through"]}

- 建立 78 场新生周赛、588 个题目位置与 526 道唯一题的双路径知识库。
- 增加逐题规范页、默认折叠摘要、C++17 高亮代码、精确样例核验状态和离线下载。
- 增加周赛时间顺序、10 个知识模块、16 周路线、竞赛 C++ 与浮点输出专题。
""",
    )

    manifest = {
        "verified_through": corpus["verified_through"],
        "contests": len(contests),
        "public_contests": sum(c["access_status"] == "public" for c in contests),
        "occurrences": sum(len(c.get("problems", [])) for c in contests),
        "problems": len(problems),
        "problem_pages": len(list((DOCS / "problems").glob("[0-9]*.md"))),
        "problem_snippets": len(list(INCLUDES.glob("haut-*.md"))),
        "contest_pages": len(list((DOCS / "contests").glob("*/*.md"))),
        "topic_pages": len(list((DOCS / "topics").glob("*.md"))) - 1,
    }
    SITE_MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    build()
