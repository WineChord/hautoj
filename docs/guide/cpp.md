# 竞赛 C++14

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

- 每份代码完整、可编译，使用 C++14。
- 变量名采用常见竞赛缩写，例如 `n`、`m`、`ans`、`cnt`、`pre`、`dis`。
- 二元运算符两侧保留空格；`if (`、`for (`、逗号后保留常规空格。
- 代码块内部不放空行，缩进 4 空格，不使用 Tab。
- 先保证含义清楚，再追求短；不要把不同概念压成同一个字母。

!!! info "HAUTOJ 当前公开编译环境"
    [HAUTOJ 常见问答](https://acm.haut.edu.cn/faqs.php)列出的 C++ 编译器为
    `g++ 9.4.0`，评测参数包含 `-std=c++14 -O2 -DONLINE_JUDGE`。
    平台同时提示版本仅供参考，因此本站以 GNU C++14 作为最低兼容门槛。

## 常用数据类型

| 类型 | 常见用途 | 风险 |
|---|---|---|
| `int` | 计数、下标 | 乘法可能在约 2.1e9 外溢出 |
| `long long` | 大整数 | 超过约 9e18 仍会溢出 |
| `__int128` | 大整数中间量 | 需要自行输入输出 |
| `double` | 一般浮点 | 计算结果通常不能直接用 `==` 比较 |

## 本地编译

```bash
g++ -std=c++14 -O2 -pipe main.cpp -o main
./main
```

浮点格式单独见[`printf` 与 `setprecision`](precision.md)。
