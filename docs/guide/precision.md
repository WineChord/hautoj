# 浮点输出：`printf` 还是 `setprecision`

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
